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
        self.min_gain_db = 0.0
        self.max_gain_db = 24.0
        self.pixel_array_size: Tuple[int, int] = (0, 0)
        self._last_ts: Optional[float] = None

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
        exp_min, exp_max, _ = ctrl_info.get("ExposureTime", (100, 60_000_000, None))
        g_min, g_max, _ = ctrl_info.get("AnalogueGain", (1.0, 16.0, None))
        self.min_exposure_s = max(1e-5, float(exp_min) / 1e6)
        self.max_exposure_s = float(exp_max) / 1e6
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
        if self.picam is not None:
            exp_us = int(round(exp * 1e6))
            self.picam.set_controls({
                "AeEnable": False,
                "ExposureTime": exp_us,
                "AnalogueGain": float(10.0 ** (gain / 20.0)),
                # Frame duration must stretch to the exposure for long night frames. Its floor caps the sensor
                # at MAX_FPS: the app needs one frame every few seconds, and every extra frame is ISP and
                # Python work (at 30 fps the libcamera/Picamera2 threads alone cost ~30% of a Pi 4 core).
                "FrameDurationLimits": (
                    max(int(1e6 / self.MAX_FPS), exp_us + 1000),
                    max(int(1e6 / self.MAX_FPS), exp_us + 1000),
                ),
            })
        return {
            "requested_exposure_s": exposure_s,
            "actual_exposure_s": round(exp, 6),
            "gain_db": round(gain, 2),
            "VMAX": 0,
            "HMAX": 0,
            "SHR0": 0,
            "PGC": 0,
        }

    def capture_frame(self) -> Tuple[np.ndarray, Dict[str, Any]]:
        if not self.is_open:
            self.open()
        want_us = self.current_exposure_s * 1e6
        want_gain = 10.0 ** (self.current_gain_db / 20.0)
        # Frames queued before the last control change still carry the old exposure: skip them
        deadline = time.time() + max(10.0, self.current_exposure_s * 4 + 8.0)
        frame = md = None
        for _ in range(6):
            req = self.picam.capture_request()
            try:
                md = req.get_metadata()
                frame = req.make_array("main").copy()
            finally:
                req.release()
            got_us = float(md.get("ExposureTime", want_us))
            got_gain = float(md.get("AnalogueGain", want_gain))
            exp_ok = abs(got_us - want_us) <= max(0.05 * want_us, 150.0)
            gain_ok = abs(got_gain - want_gain) <= max(0.08 * want_gain, 0.1)
            if (exp_ok and gain_ok) or time.time() > deadline:
                break

        if frame.ndim == 3 and frame.shape[2] == 4:
            frame = frame[:, :, :3]
        now = time.time()
        frame_dur_us = float(md.get("FrameDuration", md.get("ExposureTime", want_us)))
        self._last_ts = now
        exp_s = float(md.get("ExposureTime", want_us)) / 1e6
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
        if md.get("DigitalGain") is not None:
            meta["digital_gain"] = round(float(md["DigitalGain"]), 3)
        if md.get("SensorTemperature") is not None:
            meta["sensor_temp_c"] = round(float(md["SensorTemperature"]), 1)
        if md.get("ColourGains") is not None:
            meta["colour_gains"] = [round(float(g), 3) for g in md["ColourGains"]]
        return frame, meta

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
