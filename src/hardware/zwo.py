"""
ZWO ASI camera driver (ASI120/174/178/224/290/294/533/585/662/676/2600/6200 ...).

Uses the `zwoasi` Python wrapper around ZWO's own `libASICamera2` SDK, which must be installed
separately (https://www.zwoastro.com/software/ , udev rules for non-root USB access). Set `ZWO_ASI_LIB`
to the full path of libASICamera2.so when it is not on the loader path.

Exposure and gain are set manually. Frames are read as RAW16, demosaiced on the CPU (colour sensors) and
reduced to 8-bit BGR so the rest of the pipeline sees the same data as from the other backends. A mono sensor
is returned as a grey BGR image. ZWO gain units are 0.1 dB, so `gain_db = ASI_GAIN / 10`.
"""

import logging
import os
import time
from typing import Any, Dict, Optional, Tuple

import numpy as np

from .driver_interface import CameraDriverInterface

logger = logging.getLogger("allsky.zwo")

# ASI BayerPattern (0 RGGB, 1 BGGR, 2 GRBG, 3 GBRG) -> OpenCV code name (OpenCV names the pattern by its
# second row/column offset, so RGGB data is COLOR_BayerBG2BGR)
_BAYER_CV = {0: "COLOR_BayerBG2BGR", 1: "COLOR_BayerRG2BGR", 2: "COLOR_BayerGB2BGR", 3: "COLOR_BayerGR2BGR"}

_LIB_CANDIDATES = (
    "/usr/local/lib/libASICamera2.so",
    "/usr/lib/libASICamera2.so",
    "/usr/lib/aarch64-linux-gnu/libASICamera2.so",
    "/usr/lib/x86_64-linux-gnu/libASICamera2.so",
    "libASICamera2.so",
)

_asi_ready = False


def _load_asi(lib_path: Optional[str] = None):
    """Import zwoasi and initialise the SDK once; returns the module."""
    global _asi_ready
    import zwoasi as asi  # type: ignore

    if not _asi_ready:
        paths = [lib_path or os.environ.get("ZWO_ASI_LIB") or ""] + list(_LIB_CANDIDATES)
        last: Optional[Exception] = None
        for p in [x for x in paths if x]:
            try:
                asi.init(p)
                _asi_ready = True
                break
            except Exception as exc:  # wrong path or SDK missing: try the next one
                last = exc
        if not _asi_ready:
            raise RuntimeError(f"ZWO ASI SDK (libASICamera2) not found; set ZWO_ASI_LIB ({last})")
    return asi


def list_zwo_cameras() -> list:
    """Names of the attached ZWO cameras, or [] when the wrapper or SDK is missing."""
    try:
        asi = _load_asi()
        return list(asi.list_cameras())
    except Exception:
        return []


class ZwoAsiController(CameraDriverInterface):
    """ZWO ASI USB camera with manual long-exposure control."""

    simulator = False
    backend = "zwo"

    def __init__(self, camera_num: int = 0, width: Optional[int] = None, height: Optional[int] = None):
        self.camera_num = int(camera_num)
        self._req_size = (int(width), int(height)) if width and height else None
        self.width = int(width or 0)
        self.height = int(height or 0)
        self.cam = None
        self.is_open = False
        self.model = "unknown"
        self.is_color = False
        self.bayer = 0
        self.bin = 1
        self.pixel_size_um = 3.75
        self.native_size: Tuple[int, int] = (0, 0)
        self.current_exposure_s = 0.01
        self.current_gain_db = 0.0
        self.min_exposure_s = 32e-6
        self.max_exposure_s = 2000.0
        self.min_gain_db = 0.0
        self.max_gain_db = 30.0
        self._applied: Optional[Tuple[int, int]] = None  # (exposure_us, gain) last written to the camera
        self._last_ts: Optional[float] = None
        self._asi = None

    # ------------------------------------------------------------------
    def open(self, device_path: Optional[str] = None) -> bool:
        if self.is_open:
            return True
        asi = _load_asi()
        self._asi = asi
        n = asi.get_num_cameras()
        if n <= self.camera_num:
            raise RuntimeError(f"no ZWO ASI camera at index {self.camera_num} ({n} found)")
        cam = asi.Camera(self.camera_num)
        prop = cam.get_camera_property()
        self.model = str(prop.get("Name", "ZWO ASI"))
        self.is_color = bool(prop.get("IsColorCam"))
        self.bayer = int(prop.get("BayerPattern", 0) or 0)
        self.pixel_size_um = float(prop.get("PixelSize", 3.75) or 3.75)
        self.native_size = (int(prop["MaxWidth"]), int(prop["MaxHeight"]))

        # Hardware binning only when a smaller size was requested (largest bin that still covers it)
        self.bin = 1
        if self._req_size:
            for b in sorted(prop.get("SupportedBins") or [1]):
                if self.native_size[0] // b >= self._req_size[0] and self.native_size[1] // b >= self._req_size[1]:
                    self.bin = int(b)
        # Output width must be a multiple of 8 and height a multiple of 2 for the SDK
        w = self.native_size[0] // self.bin // 8 * 8
        h = self.native_size[1] // self.bin // 2 * 2
        cam.set_roi(start_x=0, start_y=0, width=w, height=h, bins=self.bin, image_type=asi.ASI_IMG_RAW16)
        self.width, self.height = w, h

        ctrls = cam.get_controls()
        exp = ctrls.get("Exposure") or {}
        gain = ctrls.get("Gain") or {}
        self.min_exposure_s = max(1e-5, float(exp.get("MinValue", 32)) / 1e6)
        self.max_exposure_s = float(exp.get("MaxValue", 2_000_000_000)) / 1e6
        self.min_gain_db = float(gain.get("MinValue", 0)) / 10.0
        self.max_gain_db = float(gain.get("MaxValue", 300)) / 10.0
        # USB bandwidth: keep it high so a full-frame transfer finishes quickly; ignore cameras without it
        try:
            cam.set_control_value(asi.ASI_BANDWIDTHOVERLOAD, int((ctrls.get("BandWidth") or {}).get("MaxValue", 100)))
        except Exception:
            pass
        self.cam = cam
        self.is_open = True
        self._applied = None
        self.set_hardware_exposure(self.current_exposure_s, self.current_gain_db)
        logger.info(f"ZWO {self.model} open: {w}x{h} bin {self.bin}, {'colour' if self.is_color else 'mono'}")
        return True

    def close(self) -> None:
        cam, self.cam = self.cam, None
        self.is_open = False
        if cam is not None:
            try:
                cam.stop_exposure()
            except Exception:
                pass
            try:
                cam.close()
            except Exception:
                pass

    def unlock_extension_unit(self) -> bool:
        return True

    # ------------------------------------------------------------------
    def set_hardware_exposure(self, exposure_s: float, gain_db: float) -> Dict[str, Any]:
        exp = max(self.min_exposure_s, min(self.max_exposure_s, float(exposure_s)))
        gain = max(self.min_gain_db, min(self.max_gain_db, float(gain_db)))
        self.current_exposure_s = exp
        self.current_gain_db = gain
        self._apply()
        return {
            "requested_exposure_s": exposure_s,
            "actual_exposure_s": round(exp, 6),
            "gain_db": round(gain, 2),
            "VMAX": 0,
            "HMAX": 0,
            "SHR0": 0,
            "PGC": 0,
        }

    def _apply(self) -> None:
        if self.cam is None or self._asi is None:
            return
        want = (int(round(self.current_exposure_s * 1e6)), int(round(self.current_gain_db * 10.0)))
        if want == self._applied:
            return
        self.cam.set_control_value(self._asi.ASI_EXPOSURE, want[0])
        self.cam.set_control_value(self._asi.ASI_GAIN, want[1])
        self._applied = want

    # ------------------------------------------------------------------
    def _read(self) -> np.ndarray:
        """One exposure; a transient SDK I/O error (seen once on the first frame after open) is retried once."""
        try:
            return self._read_once()
        except TimeoutError:
            raise
        except Exception as exc:
            logger.warning(f"ZWO exposure error ({exc}); retrying once")
            try:
                self.cam.stop_exposure()
            except Exception:
                pass
            return self._read_once()

    def _read_once(self) -> np.ndarray:
        """One exposure, bounded by a deadline so a stuck camera cannot hold the capture lock forever."""
        asi, cam = self._asi, self.cam
        self._apply()
        cam.start_exposure()
        deadline = time.time() + self.current_exposure_s * 2 + 10.0
        time.sleep(min(self.current_exposure_s, 0.5))
        while True:
            st = cam.get_exposure_status()
            if st == asi.ASI_EXP_SUCCESS:
                break
            if st == asi.ASI_EXP_FAILED:
                raise RuntimeError("ZWO exposure failed")
            if time.time() > deadline:
                cam.stop_exposure()
                raise TimeoutError("ZWO exposure timed out")
            time.sleep(0.01 if self.current_exposure_s < 1.0 else 0.1)
        data = cam.get_data_after_exposure()
        return np.frombuffer(data, dtype=np.uint16).reshape(self.height, self.width)

    def capture_frame(self) -> Tuple[np.ndarray, Dict[str, Any]]:
        if not self.is_open:
            self.open()
        t0 = time.time()
        raw = self._read()
        if self.is_color:
            import cv2

            bgr16 = cv2.cvtColor(raw, getattr(cv2, _BAYER_CV.get(self.bayer, _BAYER_CV[0])))
            frame = (bgr16 >> 8).astype(np.uint8)
        else:
            grey = (raw >> 8).astype(np.uint8)
            frame = np.repeat(grey[:, :, None], 3, axis=2)
        now = time.time()
        delta_ms = (now - self._last_ts) * 1000.0 if self._last_ts else (now - t0) * 1000.0
        self._last_ts = now
        meta: Dict[str, Any] = {
            "hw_frame_delta": round(delta_ms, 2),
            "actual_exposure_s": round(self.current_exposure_s, 6),
            "gain_db": round(self.current_gain_db, 2),
            "VMAX": 0,
            "HMAX": 0,
            "SHR0": 0,
            "PGC": 0,
            "timestamp": round(now, 4),
            "simulator": False,
            "camera_backend": self.backend,
        }
        try:
            meta["sensor_temp_c"] = round(float(self.cam.get_control_value(self._asi.ASI_TEMPERATURE)[0]) / 10.0, 1)
        except Exception:
            pass
        return frame, meta

    # ------------------------------------------------------------------
    def get_info(self) -> Dict[str, Any]:
        return {
            "backend": self.backend,
            "vid_pid": "03c3",  # ZWO USB vendor id
            "sensor": f"{self.model} ({'colour' if self.is_color else 'mono'})",
            "sensor_model": self.model,
            "pixel_pitch_um": self.pixel_size_um,
            "native_width": self.native_size[0] or self.width,
            "native_height": self.native_size[1] or self.height,
            "has_registers": False,
        }

    def get_limits(self) -> Dict[str, float]:
        return {
            "min_exposure_s": self.min_exposure_s,
            "max_exposure_s": self.max_exposure_s,
            "min_gain_db": self.min_gain_db,
            "max_gain_db": self.max_gain_db,
        }
