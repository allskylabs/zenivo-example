"""
QHYCCD camera driver (QHY5III/QHY178/QHY294/QHY533/QHY268/QHY600 ... any model the SDK supports).

Talks to QHYCCD's official `libqhyccd` C SDK directly through ctypes (no pip package: the Python bindings on
PyPI are very young and bundle binary SDK copies). Install the SDK from https://www.qhyccd.com/ (libqhyccd.so
and its udev rules) or set `QHY_SDK_LIB` to the full path of libqhyccd.so.

Single-frame mode, 16-bit transfer. Colour sensors are demosaiced on the CPU, everything is reduced to 8-bit BGR
(a mono sensor gives grey), like the other backends. QHY gain is in model-specific SDK units, not dB: the SDK
range [min, max] is mapped linearly onto 0..GAIN_DB_SPAN "dB", which is only a monotonic scale for the
auto-exposure loop, not a calibrated gain.
"""

import ctypes
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .driver_interface import CameraDriverInterface
from .rawconv import raw16_to_bgr8

logger = logging.getLogger("allsky.qhy")

QHYCCD_SUCCESS = 0
# CONTROL_ID values from qhyccdstruct.h
CONTROL_GAIN, CONTROL_OFFSET, CONTROL_EXPOSURE, CONTROL_SPEED, CONTROL_TRANSFERBIT = 6, 7, 8, 9, 10
CONTROL_USBTRAFFIC, CONTROL_CURTEMP, CAM_COLOR, CAM_BIN2X2MODE = 12, 14, 20, 22
# BAYER_ID: GB=1, GR=2, BG=3, RG=4
_BAYER_NAME = {1: "GBRG", 2: "GRBG", 3: "BGGR", 4: "RGGB"}

GAIN_DB_SPAN = 30.0

_LIB_CANDIDATES = (
    "/usr/lib/x86_64-linux-gnu/libqhyccd.so",
    "/usr/lib/aarch64-linux-gnu/libqhyccd.so",
    "/usr/local/lib/libqhyccd.so",
    "/usr/lib/libqhyccd.so",
    "libqhyccd.so",
)


class QhySdk:
    """Thin ctypes wrapper of the SDK calls the driver uses (a fake of this class stands in for it in tests)."""

    def __init__(self, lib_path: Optional[str] = None):
        paths = [lib_path or os.environ.get("QHY_SDK_LIB") or ""] + list(_LIB_CANDIDATES)
        last: Optional[Exception] = None
        self.lib = None
        for p in [x for x in paths if x]:
            try:
                self.lib = ctypes.CDLL(p)
                break
            except OSError as exc:
                last = exc
        if self.lib is None:
            raise RuntimeError(f"QHYCCD SDK (libqhyccd) not found; set QHY_SDK_LIB ({last})")
        L = self.lib
        u32, vp, dbl = ctypes.c_uint32, ctypes.c_void_p, ctypes.c_double
        L.OpenQHYCCD.restype = vp
        L.OpenQHYCCD.argtypes = [ctypes.c_char_p]
        L.GetQHYCCDParam.restype = dbl
        L.GetQHYCCDParam.argtypes = [vp, ctypes.c_int]
        L.SetQHYCCDParam.restype = u32
        L.SetQHYCCDParam.argtypes = [vp, ctypes.c_int, dbl]
        L.IsQHYCCDControlAvailable.restype = u32
        L.IsQHYCCDControlAvailable.argtypes = [vp, ctypes.c_int]
        L.GetQHYCCDParamMinMaxStep.restype = u32
        L.GetQHYCCDParamMinMaxStep.argtypes = [vp, ctypes.c_int] + [ctypes.POINTER(dbl)] * 3
        L.GetQHYCCDMemLength.restype = u32
        L.GetQHYCCDMemLength.argtypes = [vp]
        for name in ("CloseQHYCCD", "InitQHYCCD", "ExpQHYCCDSingleFrame", "CancelQHYCCDExposingAndReadout"):
            getattr(L, name).restype = u32
            getattr(L, name).argtypes = [vp]
        L.SetQHYCCDStreamMode.restype = u32
        L.SetQHYCCDStreamMode.argtypes = [vp, ctypes.c_uint8]
        L.SetQHYCCDBinMode.restype = u32
        L.SetQHYCCDBinMode.argtypes = [vp, u32, u32]
        L.SetQHYCCDBitsMode.restype = u32
        L.SetQHYCCDBitsMode.argtypes = [vp, u32]
        L.SetQHYCCDResolution.restype = u32
        L.SetQHYCCDResolution.argtypes = [vp, u32, u32, u32, u32]
        L.GetQHYCCDChipInfo.restype = u32
        L.GetQHYCCDChipInfo.argtypes = [vp, ctypes.POINTER(dbl), ctypes.POINTER(dbl), ctypes.POINTER(u32),
                                        ctypes.POINTER(u32), ctypes.POINTER(dbl), ctypes.POINTER(dbl), ctypes.POINTER(u32)]
        L.GetQHYCCDSingleFrame.restype = u32
        L.GetQHYCCDSingleFrame.argtypes = [vp] + [ctypes.POINTER(u32)] * 4 + [ctypes.c_void_p]
        L.GetQHYCCDId.restype = u32
        L.GetQHYCCDId.argtypes = [u32, ctypes.c_char_p]
        L.GetQHYCCDModel.restype = u32
        L.GetQHYCCDModel.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        L.ScanQHYCCD.restype = u32
        L.ReleaseQHYCCDResource.restype = u32
        self._open = 0
        L.InitQHYCCDResource.restype = u32
        if L.InitQHYCCDResource() != QHYCCD_SUCCESS:
            raise RuntimeError("QHYCCD SDK could not initialise (USB access?)")

    def scan(self) -> List[Tuple[str, str]]:
        """[(camera id, model)] of the attached cameras. The SDK's USB view can predate a camera plugged in after
        InitQHYCCDResource, so when nothing is found and no camera is open the resource is released and initialised
        again (QHY's documented way to pick up a hot-plugged camera) and the scan repeated."""
        out = self._scan_once()
        if not out and self._open == 0:
            self.lib.ReleaseQHYCCDResource()
            if self.lib.InitQHYCCDResource() == QHYCCD_SUCCESS:
                out = self._scan_once()
        return out

    def _scan_once(self) -> List[Tuple[str, str]]:
        out = []
        for i in range(int(self.lib.ScanQHYCCD())):
            buf, mod = ctypes.create_string_buffer(64), ctypes.create_string_buffer(64)
            if self.lib.GetQHYCCDId(i, buf) == QHYCCD_SUCCESS:
                cam_id = buf.value.decode(errors="replace")
                self.lib.GetQHYCCDModel(buf, mod)
                out.append((cam_id, mod.value.decode(errors="replace") or cam_id))
        return out

    def open(self, cam_id: str):
        h = self.lib.OpenQHYCCD(cam_id.encode())
        if not h:
            raise RuntimeError(f"QHY camera {cam_id} could not be opened")
        self._open += 1
        return h

    def close(self, h) -> None:
        self.lib.CloseQHYCCD(h)
        self._open = max(0, self._open - 1)

    def init_camera(self, h) -> None:
        self.lib.SetQHYCCDStreamMode(h, 0)  # single frame
        if self.lib.InitQHYCCD(h) != QHYCCD_SUCCESS:
            raise RuntimeError("QHY InitQHYCCD failed")

    def chip_info(self, h) -> Dict[str, float]:
        cw, ch, pw, ph = (ctypes.c_double() for _ in range(4))
        iw, ih, bpp = (ctypes.c_uint32() for _ in range(3))
        if self.lib.GetQHYCCDChipInfo(h, cw, ch, iw, ih, pw, ph, bpp) != QHYCCD_SUCCESS:
            raise RuntimeError("QHY GetQHYCCDChipInfo failed")
        return {"width": int(iw.value), "height": int(ih.value), "pixel_um": float(pw.value), "bpp": int(bpp.value)}

    def has_control(self, h, control: int) -> int:
        """SDK status: QHYCCD_SUCCESS (0) when available; for CAM_COLOR the Bayer id is returned instead."""
        return int(self.lib.IsQHYCCDControlAvailable(h, control))

    def param_range(self, h, control: int) -> Optional[Tuple[float, float]]:
        lo, hi, st = (ctypes.c_double() for _ in range(3))
        if self.lib.GetQHYCCDParamMinMaxStep(h, control, lo, hi, st) != QHYCCD_SUCCESS:
            return None
        return float(lo.value), float(hi.value)

    def set_param(self, h, control: int, value: float) -> None:
        self.lib.SetQHYCCDParam(h, control, float(value))

    def get_param(self, h, control: int) -> float:
        return float(self.lib.GetQHYCCDParam(h, control))

    def set_bin(self, h, b: int) -> None:
        self.lib.SetQHYCCDBinMode(h, b, b)

    def set_bits(self, h, bits: int) -> None:
        self.lib.SetQHYCCDBitsMode(h, bits)

    def set_resolution(self, h, w: int, hgt: int) -> None:
        if self.lib.SetQHYCCDResolution(h, 0, 0, w, hgt) != QHYCCD_SUCCESS:
            raise RuntimeError("QHY SetQHYCCDResolution failed")

    def expose(self, h) -> None:
        self.lib.ExpQHYCCDSingleFrame(h)  # returns when the exposure has started (or ended on some models)

    def get_frame(self, h) -> Tuple[int, int, int, int, bytes]:
        buf = ctypes.create_string_buffer(int(self.lib.GetQHYCCDMemLength(h)))
        w, hgt, bpp, ch = (ctypes.c_uint32() for _ in range(4))
        if self.lib.GetQHYCCDSingleFrame(h, w, hgt, bpp, ch, buf) != QHYCCD_SUCCESS:
            raise RuntimeError("QHY GetQHYCCDSingleFrame failed")
        n = w.value * hgt.value * ch.value * (bpp.value // 8)
        return w.value, hgt.value, bpp.value, ch.value, buf.raw[:n]

    def cancel(self, h) -> None:
        self.lib.CancelQHYCCDExposingAndReadout(h)


_sdk: Optional[QhySdk] = None
_sdk_lock = threading.Lock()


def _get_sdk() -> QhySdk:
    global _sdk
    with _sdk_lock:
        if _sdk is None:
            _sdk = QhySdk()
        return _sdk


def list_qhy_cameras() -> List[str]:
    """Model names of the attached QHY cameras, or [] when the SDK is missing."""
    try:
        return [model for _id, model in _get_sdk().scan()]
    except Exception:
        return []


class QhyController(CameraDriverInterface):
    """QHYCCD USB camera, single-frame mode with manual exposure."""

    simulator = False
    backend = "qhy"

    def __init__(self, camera_num: int = 0, width: Optional[int] = None, height: Optional[int] = None):
        self.camera_num = int(camera_num)
        self._req_size = (int(width), int(height)) if width and height else None
        self.width = int(width or 0)
        self.height = int(height or 0)
        self.sdk: Optional[QhySdk] = None
        self.h = None
        self.is_open = False
        self.model = "unknown"
        self.bayer: Optional[str] = None
        self.bin = 1
        self.pixel_size_um = 3.75
        self.native_size: Tuple[int, int] = (0, 0)
        self.current_exposure_s = 0.01
        self.current_gain_db = 0.0
        self.min_exposure_s = 1e-5
        self.max_exposure_s = 3600.0
        self.min_gain_db = 0.0
        self.max_gain_db = GAIN_DB_SPAN
        self._gain_range = (0.0, 100.0)
        self._last_ts: Optional[float] = None
        self._applied: Optional[Tuple[int, float]] = None

    # ------------------------------------------------------------------
    def open(self, device_path: Optional[str] = None) -> bool:
        if self.is_open:
            return True
        sdk = self.sdk or _get_sdk()
        cams = sdk.scan()
        if len(cams) <= self.camera_num:
            raise RuntimeError(f"no QHY camera at index {self.camera_num} ({len(cams)} found)")
        cam_id, self.model = cams[self.camera_num]
        h = sdk.open(cam_id)
        try:
            sdk.init_camera(h)
            info = sdk.chip_info(h)
            self.native_size = (info["width"], info["height"])
            self.pixel_size_um = info["pixel_um"] or self.pixel_size_um
            color = sdk.has_control(h, CAM_COLOR)
            self.bayer = _BAYER_NAME.get(color)
            # Hardware binning only for mono sensors (it destroys the Bayer pattern) and only to cover a smaller size
            self.bin = 1
            if self._req_size and not self.bayer and sdk.has_control(h, CAM_BIN2X2MODE) == QHYCCD_SUCCESS:
                if self.native_size[0] // 2 >= self._req_size[0] and self.native_size[1] // 2 >= self._req_size[1]:
                    self.bin = 2
            sdk.set_bin(h, self.bin)
            sdk.set_bits(h, 16)
            w = self.native_size[0] // self.bin // 8 * 8
            hg = self.native_size[1] // self.bin // 2 * 2
            sdk.set_resolution(h, w, hg)
            self.width, self.height = w, hg
            rng = sdk.param_range(h, CONTROL_EXPOSURE)
            if rng:
                self.min_exposure_s = max(1e-5, rng[0] / 1e6)
                self.max_exposure_s = rng[1] / 1e6
            grng = sdk.param_range(h, CONTROL_GAIN)
            if grng and grng[1] > grng[0]:
                self._gain_range = grng
            sdk.set_param(h, CONTROL_USBTRAFFIC, 30)
        except Exception:
            try:
                sdk.close(h)
            except Exception:
                pass
            raise
        self.sdk, self.h, self.is_open = sdk, h, True
        self._applied = None
        self.set_hardware_exposure(self.current_exposure_s, self.current_gain_db)
        logger.info(f"QHY {self.model} open: {w}x{hg} bin {self.bin}, {self.bayer or 'mono'}")
        return True

    def close(self) -> None:
        h, self.h = self.h, None
        self.is_open = False
        if h is not None and self.sdk is not None:
            for fn in (self.sdk.cancel, self.sdk.close):
                try:
                    fn(h)
                except Exception:
                    pass

    def unlock_extension_unit(self) -> bool:
        return True

    # ------------------------------------------------------------------
    def _db_to_gain(self, db: float) -> float:
        lo, hi = self._gain_range
        return lo + (hi - lo) * max(0.0, min(GAIN_DB_SPAN, db)) / GAIN_DB_SPAN

    def set_hardware_exposure(self, exposure_s: float, gain_db: float) -> Dict[str, Any]:
        exp = max(self.min_exposure_s, min(self.max_exposure_s, float(exposure_s)))
        gain = max(self.min_gain_db, min(self.max_gain_db, float(gain_db)))
        self.current_exposure_s, self.current_gain_db = exp, gain
        self._apply()
        return {"requested_exposure_s": exposure_s, "actual_exposure_s": round(exp, 6), "gain_db": round(gain, 2),
                "VMAX": 0, "HMAX": 0, "SHR0": 0, "PGC": 0}

    def _apply(self) -> None:
        if self.h is None or self.sdk is None:
            return
        want = (int(round(self.current_exposure_s * 1e6)), round(self._db_to_gain(self.current_gain_db), 2))
        if want == self._applied:
            return
        self.sdk.set_param(self.h, CONTROL_EXPOSURE, want[0])
        self.sdk.set_param(self.h, CONTROL_GAIN, want[1])
        self._applied = want

    # ------------------------------------------------------------------
    def _read_once(self) -> np.ndarray:
        """One exposure. The SDK call blocks until readout and has no timeout, so it runs in a worker thread that is
        cancelled when it exceeds the deadline (a stuck camera must not hold the capture lock forever)."""
        self._apply()
        box: Dict[str, Any] = {}

        def work():
            try:
                self.sdk.expose(self.h)
                box["frame"] = self.sdk.get_frame(self.h)
            except Exception as exc:
                box["err"] = exc

        t = threading.Thread(target=work, name="qhy-capture", daemon=True)
        t.start()
        t.join(self.current_exposure_s * 2 + 15.0)
        if t.is_alive():
            try:
                self.sdk.cancel(self.h)
            except Exception:
                pass
            raise TimeoutError("QHY exposure timed out")
        if "err" in box:
            raise box["err"]
        w, hgt, bpp, ch, data = box["frame"]
        if ch != 1:
            raise RuntimeError(f"unexpected QHY channel count {ch}")
        arr = np.frombuffer(data, dtype=np.uint16 if bpp == 16 else np.uint8).reshape(hgt, w)
        if bpp != 16:
            arr = arr.astype(np.uint16) << 8
        return arr

    def _read(self) -> np.ndarray:
        try:
            return self._read_once()
        except TimeoutError:
            raise
        except Exception as exc:
            logger.warning(f"QHY exposure error ({exc}); retrying once")
            try:
                self.sdk.cancel(self.h)
            except Exception:
                pass
            return self._read_once()

    def capture_frame(self) -> Tuple[np.ndarray, Dict[str, Any]]:
        if not self.is_open:
            self.open()
        t0 = time.time()
        frame = raw16_to_bgr8(self._read(), self.bayer)
        now = time.time()
        delta_ms = (now - self._last_ts) * 1000.0 if self._last_ts else (now - t0) * 1000.0
        self._last_ts = now
        meta: Dict[str, Any] = {
            "hw_frame_delta": round(delta_ms, 2), "actual_exposure_s": round(self.current_exposure_s, 6),
            "gain_db": round(self.current_gain_db, 2), "VMAX": 0, "HMAX": 0, "SHR0": 0, "PGC": 0,
            "timestamp": round(now, 4), "simulator": False, "camera_backend": self.backend,
        }
        try:
            temp = self.sdk.get_param(self.h, CONTROL_CURTEMP)
            if -80.0 < temp < 100.0:  # models without a temperature sensor return an absurd value
                meta["sensor_temp_c"] = round(temp, 1)
        except Exception:
            pass
        return frame, meta

    # ------------------------------------------------------------------
    def get_info(self) -> Dict[str, Any]:
        return {
            "backend": self.backend, "vid_pid": "1618",  # QHYCCD USB vendor id
            "sensor": f"{self.model} ({self.bayer and 'colour' or 'mono'})", "sensor_model": self.model,
            "pixel_pitch_um": self.pixel_size_um,
            "native_width": self.native_size[0] or self.width, "native_height": self.native_size[1] or self.height,
            "has_registers": False,
        }

    def get_limits(self) -> Dict[str, float]:
        return {"min_exposure_s": self.min_exposure_s, "max_exposure_s": self.max_exposure_s,
                "min_gain_db": self.min_gain_db, "max_gain_db": self.max_gain_db}
