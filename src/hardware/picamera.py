"""
Raspberry Pi CSI camera driver (libcamera / Picamera2).

Works with every sensor libcamera supports on the Pi: Camera Module 3 (IMX708, incl. NoIR/Wide),
HQ camera (IMX477), GS (IMX296), IMX462/IMX290/IMX585 starlight modules, Camera Module 2 (IMX219), ...

Exposure and gain are set manually (AE disabled) through libcamera controls. The requested gain is
clamped to the sensor's analogue gain range and the exposure to the sensor's exposure range, so the
auto-exposure loop simply sees the actually applied values read back from the frame metadata.
"""

import logging
import math
import time
from typing import Any, Dict, Optional, Tuple

import numpy as np

from .driver_interface import CameraDriverInterface

logger = logging.getLogger("allsky.picamera")

# Known pixel pitches (µm) for the station / plate-solver sensor spec
_PIXEL_PITCH_UM = {
    "imx708": 1.4,
    "imx477": 1.55,
    "imx296": 3.45,
    "imx219": 1.12,
    "imx290": 2.9,
    "imx462": 2.9,
    "imx327": 2.9,
    "imx585": 2.9,
    "imx519": 1.22,
    "ov5647": 1.4,
    "ov9281": 3.0,
}

# Fixed-lens Raspberry Pi camera modules: (focal length mm, horizontal FOV °, vertical FOV °)
_FIXED_LENSES = {
    "imx708": (4.74, 66.0, 41.0),
    "imx708_wide": (2.75, 102.0, 67.0),
    "imx219": (3.04, 62.2, 48.8),
    "ov5647": (3.6, 53.5, 41.4),
}


def list_picameras() -> list:
    """Return libcamera's camera list, or [] when Picamera2 is unavailable."""
    try:
        from picamera2 import Picamera2  # type: ignore
    except Exception:
        return []
    try:
        return list(Picamera2.global_camera_info())
    except Exception:
        return []


class PiCameraController(CameraDriverInterface):
    """Raspberry Pi CSI camera via Picamera2 with manual long-exposure control."""

    simulator = False
    backend = "picamera"
    MAX_FPS = 5.0
    # Largest exposure floor capture_frame() may learn from the sensor (IMX708 binned mode: 53 us)
    FLOOR_CAP_S = 0.0002
    # Below the sensor floor the ISP Brightness control darkens further (a "virtual ND"): 0.1 per stop,
    # up to ISP_ND_STOPS. At 53 us the IMX708 NoIR daytime sky still came out at 200-255 DN.
    ISP_ND_STOPS = 2.0
    ISP_BRIGHTNESS_PER_STOP = 0.1

    def __init__(
        self,
        camera_num: int = 0,
        width: Optional[int] = None,
        height: Optional[int] = None,
        awb: bool = True,
    ):
        self.camera_num = int(camera_num)
        self._req_size = (int(width), int(height)) if width and height else None
        self.width = int(width or 0)
        self.height = int(height or 0)
        self.awb = awb
        self.picam = None
        self.is_open = False
        self.model = "unknown"
        self.current_exposure_s = 0.01
        self.current_gain_db = 0.0
        self.min_exposure_s = 0.0001
        self.max_exposure_s = 60.0
        # Shortest exposure the sensor mode really reaches (learned from frames), and what to request for it
        self.sensor_floor_s: Optional[float] = None
        self._floor_request_us = 1
        self._want_us = self.current_exposure_s * 1e6
        self._has_brightness = False
        self.isp_brightness = 0.0
        self.min_gain_db = 0.0
        self.max_gain_db = 24.0
        self.pixel_array_size: Tuple[int, int] = (0, 0)
        self._last_ts: Optional[float] = None
        # Frame rate ceiling; focus mode raises it for a responsive zoom view
        self.max_fps = self.MAX_FPS
        # Motorised lens (Camera Module 3 / IMX708, IMX519, ...): LensPosition in dioptres (0 = infinity).
        # None = leave the lens where libcamera puts it (IMX708 tuning default is 1.0 D, i.e. ~1 m).
        self.lens_range: Optional[Tuple[float, float, float]] = None
        self.lens_position_target: Optional[float] = None
        self.lens_position: Optional[float] = None
        self.af_state: Optional[int] = None
        self.focus_fom: Optional[int] = None
        self._af_running = False
        self._scaler_crop: Optional[Tuple[int, int, int, int]] = None

    # ------------------------------------------------------------------
    def _pick_sensor_mode(self, cam) -> Dict[str, Any]:
        """Pick a full field-of-view mode; prefer the 2x2 binned one (better SNR, less RAM on a Pi)."""
        modes = list(cam.sensor_modes or [])
        if not modes:
            return {}
        full_w = max(m["crop_limits"][2] for m in modes)
        full_fov = [m for m in modes if m["crop_limits"][2] >= full_w * 0.98] or modes
        if self._req_size:
            rw, rh = self._req_size
            fitting = [m for m in full_fov if m["size"][0] >= rw and m["size"][1] >= rh]
            pool = fitting or full_fov
            return min(pool, key=lambda m: m["size"][0] * m["size"][1])
        binned = [m for m in full_fov if m["size"][0] <= 2600]
        pool = binned or full_fov
        return max(pool, key=lambda m: m["size"][0] * m["size"][1])

    def open(self, device_path: Optional[str] = None) -> bool:
        if self.is_open:
            return True
        from picamera2 import Picamera2  # type: ignore

        cam = Picamera2(self.camera_num)
        props = cam.camera_properties or {}
        self.model = str(props.get("Model", "unknown"))
        pas = props.get("PixelArraySize") or (0, 0)
        self.pixel_array_size = (int(pas[0]), int(pas[1]))

        mode = self._pick_sensor_mode(cam)
        size = self._req_size or (tuple(mode["size"]) if mode else (1920, 1080))
        # ISP output width must be even (and is happiest when a multiple of 16)
        size = (int(size[0]) // 16 * 16, int(size[1]) // 2 * 2)
        raw_cfg = {"size": tuple(mode["size"])} if mode else {}

        ctrl_info = cam.camera_controls or {}
        if "LensPosition" in ctrl_info and "AfMode" in ctrl_info:
            lp_min, lp_max, lp_def = ctrl_info["LensPosition"]
            self.lens_range = (float(lp_min), float(lp_max), float(lp_def if lp_def is not None else lp_min))
        exp_min, exp_max, _ = ctrl_info.get("ExposureTime", (100, 60_000_000, None))
        g_min, g_max, _ = ctrl_info.get("AnalogueGain", (1.0, 16.0, None))
        self.min_exposure_s = max(1e-5, float(exp_min) / 1e6)
        self.max_exposure_s = float(exp_max) / 1e6
        # Request at the floor: the mode's own minimum. Asking for the floor itself (53 us) made the IPA round
        # it down to whole lines and add up to 1.5x digital gain on top (DigitalGain 1.32 at 53 us, 1.0 at 13 us)
        mode_min = (mode.get("exposure_limits") or (None,))[0] if mode else None
        self._floor_request_us = max(1, int(mode_min if mode_min else exp_min))
        self._has_brightness = "Brightness" in ctrl_info
        self.min_gain_db = 0.0
        self.max_gain_db = round(20.0 * math.log10(max(1.0, float(g_max))), 2)

        config = cam.create_still_configuration(
            main={"size": size, "format": "RGB888"},  # RGB888 = B,G,R byte order (OpenCV BGR)
            raw=raw_cfg or None,
            buffer_count=2,
            queue=False,
            controls={"AeEnable": False, "AwbEnable": bool(self.awb)},
        )
        cam.configure(config)
        cam.start()
        self.picam = cam
        self.width, self.height = config["main"]["size"]
        self.is_open = True
        logger.info(
            "Pi camera %s opened: %dx%d (sensor mode %s), exposure %.4f..%.1fs, gain 0..%.1f dB",
            self.model, self.width, self.height, mode.get("size") if mode else "?",
            self.min_exposure_s, self.max_exposure_s, self.max_gain_db,
        )
        self.set_hardware_exposure(self.current_exposure_s, self.current_gain_db)
        if self.lens_range and self.lens_position_target is not None:
            self.set_lens_position(self.lens_position_target)
        return True

    def close(self) -> None:
        if self.picam is not None:
            try:
                self.picam.stop()
            except Exception:
                pass
            try:
                self.picam.close()
            except Exception:
                pass
        self.picam = None
        self.is_open = False

    def unlock_extension_unit(self) -> bool:
        return True  # nothing to unlock on CSI cameras

    # ------------------------------------------------------------------
    def set_hardware_exposure(self, exposure_s: float, gain_db: float) -> Dict[str, Any]:
        exp = max(self.min_exposure_s, min(self.max_exposure_s, float(exposure_s)))
        gain = max(self.min_gain_db, min(self.max_gain_db, float(gain_db)))
        self.current_exposure_s = exp
        self.current_gain_db = gain
        floor = self.sensor_floor_s
        exp_us = int(round(exp * 1e6))
        self._want_us = exp_us
        brightness = 0.0
        if floor is not None and exp <= floor * 1.001:
            exp_us = self._floor_request_us
            self._want_us = floor * 1e6
            if self._has_brightness and exp < floor:
                brightness = -self.ISP_BRIGHTNESS_PER_STOP * min(self.ISP_ND_STOPS, math.log2(floor / exp))
        self.isp_brightness = round(brightness, 3)
        if self.picam is not None:
            ctrls = {
                "AeEnable": False,
                "ExposureTime": exp_us,
                "AnalogueGain": float(10.0 ** (gain / 20.0)),
                # Frame duration must stretch to the exposure for long night frames. Its floor caps the sensor
                # at MAX_FPS: the app needs one frame every few seconds, and every extra frame is ISP and
                # Python work (at 30 fps the libcamera/Picamera2 threads alone cost ~30% of a Pi 4 core).
                "FrameDurationLimits": (
                    max(int(1e6 / self.max_fps), exp_us + 1000),
                    max(int(1e6 / self.max_fps), exp_us + 1000),
                ),
            }
            if self._has_brightness:
                ctrls["Brightness"] = self.isp_brightness
            self.picam.set_controls(ctrls)
        return {
            "requested_exposure_s": exposure_s,
            "actual_exposure_s": round(exp, 6),
            "gain_db": round(gain, 2),
            "VMAX": 0,
            "HMAX": 0,
            "SHR0": 0,
            "PGC": 0,
        }

    @staticmethod
    def _copy_main(req) -> np.ndarray:
        try:
            from picamera2 import MappedArray  # type: ignore

            with MappedArray(req, "main", write=False) as m:
                return np.array(m.array, order="C")
        except (ImportError, TypeError):
            return req.make_array("main")

    def capture_frame(self) -> Tuple[np.ndarray, Dict[str, Any]]:
        if not self.is_open:
            self.open()
        want_us = self._want_us
        want_gain = 10.0 ** (self.current_gain_db / 20.0)
        # Frames queued before the last control change still carry the old exposure: skip them
        deadline = time.time() + max(10.0, self.current_exposure_s * 4 + 8.0)
        frame = md = None
        seen_us = []  # ExposureTime of every frame looked at, to tell a sensor floor from stale frames
        # Never block forever: libcamera occasionally stops delivering frames, and a capture that hangs while
        # holding the service's capture lock froze every page (focus, telemetry, snapshots). On timeout the
        # pending camera jobs are cancelled and the error lets the service close and re-open the camera.
        wait_s = max(5.0, self.current_exposure_s * 3 + 5.0)
        for attempt in range(6):
            try:
                req = self.picam.capture_request(wait=wait_s)
            except TimeoutError as exc:
                try:
                    self.picam.cancel_all_and_flush()
                except Exception:
                    pass
                raise TimeoutError(f"Pi camera delivered no frame in {wait_s:.0f} s") from exc
            try:
                md = req.get_metadata()
                got_us = float(md.get("ExposureTime", want_us))
                got_gain = float(md.get("AnalogueGain", want_gain))
                seen_us.append(got_us)
                exp_ok = abs(got_us - want_us) <= max(0.05 * want_us, 150.0)
                if exp_ok and got_us > want_us * 1.1:
                    # Within the slack but clearly longer: a sensor floor or a frame still on the previous
                    # exposure. Only a value repeated over three frames is trusted as the floor below.
                    exp_ok = len(seen_us) >= 3 and all(abs(u - got_us) <= 2.0 for u in seen_us[-3:])
                gain_ok = abs(got_gain - want_gain) <= max(0.08 * want_gain, 0.1)
                done = (exp_ok and gain_ok) or time.time() > deadline or attempt == 5
                if done:
                    # Copy only the frame that is kept, once, under a read-only DMA sync
                    # (make_array() maps it read-write, so every release also wrote the cache back)
                    frame = self._copy_main(req)
            finally:
                req.release()
            if done:
                break

        if frame.ndim == 3 and frame.shape[2] == 4:
            frame = frame[:, :, :3]
        now = time.time()
        frame_dur_us = float(md.get("FrameDuration", md.get("ExposureTime", want_us)))
        self._last_ts = now
        exp_s = float(md.get("ExposureTime", want_us)) / 1e6
        floor_us = seen_us[-1]
        steady = len(seen_us) >= 3 and all(abs(u - floor_us) <= 2.0 for u in seen_us[-3:])
        if exp_s < self.FLOOR_CAP_S and exp_s > want_us * 1.1e-6 and steady:
            # libcamera's ExposureTime minimum is below what the sensor mode can do (IMX708 2304x1296: 26 vs 53 us);
            # asking for less makes the ISP add digital gain, so the floor actually reached becomes the limit.
            # Only a steady, small value counts: frames still carrying the previous (longer) exposure once
            # ratcheted the floor to 587 us and left daytime frames saturated with no way to darken them.
            self.sensor_floor_s = exp_s
            self.min_exposure_s = exp_s / (2.0 ** self.ISP_ND_STOPS if self._has_brightness else 1.0)
            # Re-request at the floor (mode minimum, no digital gain), or below it through ISP Brightness
            self.set_hardware_exposure(max(self.current_exposure_s, self.min_exposure_s), self.current_gain_db)
        elif self.isp_brightness and abs(exp_s * 1e6 - want_us) <= 2.0:
            # Sensor at its floor, ISP darkening the rest: report the equivalent exposure so brightness keeps
            # tracking exposure for the auto exposure loop, cloud radiance and sky-event frame matching
            exp_s = self.current_exposure_s
        gain_db = 20.0 * math.log10(max(1.0, float(md.get("AnalogueGain", want_gain))))
        meta: Dict[str, Any] = {
            "hw_frame_delta": round(frame_dur_us / 1000.0, 2),
            "actual_exposure_s": round(exp_s, 6),
            "gain_db": round(gain_db, 2),
            "VMAX": 0,
            "HMAX": 0,
            "SHR0": 0,
            "PGC": 0,
            "timestamp": round(now, 4),
            "simulator": False,
            "camera_backend": self.backend,
        }
        if self.isp_brightness:
            meta["isp_brightness"] = self.isp_brightness
            meta["sensor_exposure_s"] = round(float(md.get("ExposureTime", want_us)) / 1e6, 6)
        if md.get("LensPosition") is not None:
            self.lens_position = round(float(md["LensPosition"]), 4)
            meta["lens_position"] = self.lens_position
        if md.get("AfState") is not None:
            self.af_state = int(md["AfState"])
            meta["af_state"] = self.af_state
            if self._af_running and self.af_state in (2, 3) and self.lens_position is not None:
                # Scan finished (focused or failed): hold the lens there instead of hunting further
                self.set_lens_position(self.lens_position)
        if md.get("ScalerCrop") is not None:
            self._scaler_crop = tuple(int(v) for v in md["ScalerCrop"])
        if md.get("FocusFoM") is not None:
            self.focus_fom = int(md["FocusFoM"])
        if md.get("DigitalGain") is not None:
            meta["digital_gain"] = round(float(md["DigitalGain"]), 3)
        if md.get("SensorTemperature") is not None:
            meta["sensor_temp_c"] = round(float(md["SensorTemperature"]), 1)
        if md.get("ColourGains") is not None:
            meta["colour_gains"] = [round(float(g), 3) for g in md["ColourGains"]]
        return frame, meta

    # ------------------------------------------------------------------
    # Motorised lens
    # ------------------------------------------------------------------
    def set_max_fps(self, fps: float) -> None:
        self.max_fps = max(0.5, float(fps))
        self.set_hardware_exposure(self.current_exposure_s, self.current_gain_db)

    def set_lens_position(self, dioptres: float) -> Optional[float]:
        """Manual focus: hold the lens at this position (dioptres = 1 / distance in m; 0 = infinity)."""
        if not self.lens_range:
            return None
        lo, hi, _ = self.lens_range
        pos = round(min(hi, max(lo, float(dioptres))), 4)
        self.lens_position_target = pos
        self._af_running = False
        if self.picam is not None:
            self.picam.set_controls({"AfMode": 0, "LensPosition": pos})
        return pos

    def start_autofocus(self, window: Optional[Tuple[float, float, float, float]] = None) -> bool:
        """One libcamera autofocus scan (PDAF + contrast, good in daylight), optionally metered on a window
        given as fractions (x, y, w, h) of the sensor field. Poll af_status() until it is no longer scanning."""
        if not self.lens_range or self.picam is None:
            return False
        ctrls: Dict[str, Any] = {"AfMode": 1, "AfRange": 0, "AfSpeed": 1}
        crop = self._scaler_crop
        if window and crop:
            cx, cy, cw, ch = crop
            fx, fy, fw, fh = window
            ctrls["AfMetering"] = 1
            ctrls["AfWindows"] = [(int(cx + fx * cw), int(cy + fy * ch), max(16, int(fw * cw)), max(16, int(fh * ch)))]
        else:
            ctrls["AfMetering"] = 0
        self.picam.set_controls(ctrls)
        self.picam.set_controls({"AfTrigger": 0})
        self._af_running = True
        return True

    def af_status(self) -> Dict[str, Any]:
        """AfState 0 idle, 1 scanning, 2 focused, 3 failed, updated from captured frames' metadata (keep
        capturing while polling). Once a scan ends the lens is held (manual) at the position it found."""
        return {"af_state": self.af_state, "lens_position": self.lens_position, "scanning": self._af_running}

    def lens_info(self) -> Optional[Dict[str, Any]]:
        if not self.lens_range:
            return None
        lo, hi, default = self.lens_range
        return {
            "available": True,
            "min": lo,
            "max": hi,
            "default": default,
            "position": self.lens_position if self.lens_position is not None else self.lens_position_target,
            "held": self.lens_position_target,
            "af_state": self.af_state,
            "focus_fom": self.focus_fom,
            "camera_af": True,
        }

    # ------------------------------------------------------------------
    def get_info(self) -> Dict[str, Any]:
        sensor = self.model.split("_")[0].lower()
        pitch = _PIXEL_PITCH_UM.get(sensor, 1.55)
        return {
            "backend": self.backend,
            "vid_pid": "csi",
            "sensor": f"Raspberry Pi camera {self.model}",
            "sensor_model": self.model,
            "pixel_pitch_um": pitch,
            "native_width": self.pixel_array_size[0] or self.width,
            "native_height": self.pixel_array_size[1] or self.height,
            "has_registers": False,
            "lens": self._lens_info(),
        }

    def _lens_info(self) -> Optional[Dict[str, Any]]:
        name = self.model.lower().replace("_noir", "")
        spec = _FIXED_LENSES.get(name) or _FIXED_LENSES.get(name.split("_")[0] if "wide" not in name else "")
        if not spec:
            return None
        f_mm, hfov, vfov = spec
        return {
            "lens_model": f"Raspberry Pi {self.model} fixed lens {f_mm} mm ({hfov}° x {vfov}°)",
            "focal_length_mm": f_mm,
            "fov_horizontal_deg": hfov,
            "fov_vertical_deg": vfov,
        }

    def get_limits(self) -> Dict[str, float]:
        return {
            "min_exposure_s": self.min_exposure_s,
            "max_exposure_s": self.max_exposure_s,
            "min_gain_db": self.min_gain_db,
            "max_gain_db": self.max_gain_db,
        }
