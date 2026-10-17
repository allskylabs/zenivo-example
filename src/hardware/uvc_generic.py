"""
Generic USB (UVC) / V4L2 camera driver via OpenCV.

For any webcam or USB astro camera exposing a standard V4L2 capture node. Exposure is set through
the UVC `exposure_time_absolute` control (100 µs units) and gain through the `gain` control, mapped
linearly from dB onto the control's raw range. Exposures longer than the camera's hardware ceiling
are synthesised by summing consecutive frames (software integration).
"""

import glob
import logging
import math
import os
import re
import subprocess
import time
from typing import Any, Dict, Optional, Tuple

import numpy as np

from .driver_interface import CameraDriverInterface

logger = logging.getLogger("allsky.uvc")


def _usb_id(node: str) -> str:
    """Return 'vvvv:pppp' for a /dev/videoN node, or '' when not a USB device."""
    name = os.path.basename(node)
    dev = os.path.realpath(f"/sys/class/video4linux/{name}/device")
    for _ in range(4):
        vid = os.path.join(dev, "idVendor")
        if os.path.isfile(vid):
            try:
                with open(vid) as fv, open(os.path.join(dev, "idProduct")) as fp:
                    return f"{fv.read().strip()}:{fp.read().strip()}"
            except OSError:
                return ""
        dev = os.path.dirname(dev)
    return ""


def find_uvc_device_nodes(exclude_ids: Tuple[str, ...] = ()) -> list:
    """List primary capture nodes of uvcvideo devices (index 0 of each camera)."""
    nodes = []
    for path in sorted(glob.glob("/sys/class/video4linux/video*"), key=lambda p: int(re.sub(r"\D", "", p) or 0)):
        try:
            driver = os.path.basename(os.path.realpath(os.path.join(path, "device", "driver")))
            with open(os.path.join(path, "index")) as f:
                index = int(f.read().strip() or 0)
        except (OSError, ValueError):
            continue
        if driver != "uvcvideo" or index != 0:
            continue
        node = "/dev/" + os.path.basename(path)
        if _usb_id(node) in exclude_ids:
            continue
        nodes.append(node)
    return nodes


def _query_controls(node: str) -> Dict[str, Dict[str, int]]:
    """Parse `v4l2-ctl -l` into {name: {min, max, default, value}}."""
    try:
        out = subprocess.run(["v4l2-ctl", "-d", node, "-l"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return {}
    ctrls: Dict[str, Dict[str, int]] = {}
    for line in out.splitlines():
        m = re.match(r"\s*(\w+)\s+0x[0-9a-f]+\s+\((\w+)\)\s*:(.*)", line)
        if not m:
            continue
        fields = dict(re.findall(r"(\w+)=(-?\d+)", m.group(3)))
        ctrls[m.group(1)] = {k: int(v) for k, v in fields.items()}
    return ctrls


class GenericUVCCameraController(CameraDriverInterface):
    """Any V4L2/UVC camera through OpenCV, with software integration beyond the hardware exposure limit."""

    simulator = False
    backend = "v4l2"

    # Gain in dB mapped onto the raw `gain` control range (UVC gain units are vendor specific)
    NOMINAL_MAX_GAIN_DB = 30.0
    MAX_SOFT_EXPOSURE_S = 60.0

    def __init__(self, device_path: Optional[str] = None, width: Optional[int] = None, height: Optional[int] = None):
        self.device_path = device_path or (find_uvc_device_nodes() or ["/dev/video0"])[0]
        self.width = int(width or 0)
        self.height = int(height or 0)
        self.cap = None
        self.is_open = False
        self.current_exposure_s = 0.02
        self.current_gain_db = 0.0
        self.min_exposure_s = 0.0001
        self.hw_max_exposure_s = 0.5
        self.max_gain_db = self.NOMINAL_MAX_GAIN_DB
        self.controls: Dict[str, Dict[str, int]] = {}
        self.usb_id = _usb_id(self.device_path)
        self.card = os.path.basename(self.device_path)
        self._hw_exp_s = self.current_exposure_s
        self._n_sub = 1
        self._last_ts: Optional[float] = None

    def _set_ctrl(self, name: str, value: int) -> None:
        if name not in self.controls:
            return
        subprocess.run(
            ["v4l2-ctl", "-d", self.device_path, f"--set-ctrl={name}={int(value)}"],
            capture_output=True, timeout=5,
        )

    def open(self, device_path: Optional[str] = None) -> bool:
        if self.is_open:
            return True
        import cv2

        if device_path:
            self.device_path = device_path
            self.usb_id = _usb_id(device_path)
        cap = cv2.VideoCapture(self.device_path, cv2.CAP_V4L2)
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open V4L2 device {self.device_path}")
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        # Ask for the requested size, or something huge so the driver picks its largest mode
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width or 10000)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height or 10000)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        ok, frame = cap.read()
        if not ok or frame is None:
            cap.release()
            raise RuntimeError(f"No frames from V4L2 device {self.device_path}")
        self.height, self.width = frame.shape[:2]
        self.cap = cap
        self.is_open = True

        self.controls = _query_controls(self.device_path)
        exp_ctrl = self.controls.get("exposure_time_absolute") or self.controls.get("exposure_absolute")
        if exp_ctrl:
            self.min_exposure_s = max(1e-4, exp_ctrl.get("min", 1) / 1e4)
            self.hw_max_exposure_s = max(self.min_exposure_s, exp_ctrl.get("max", 5000) / 1e4)
        if "gain" not in self.controls:
            self.max_gain_db = 0.0
        try:
            with open(f"/sys/class/video4linux/{os.path.basename(self.device_path)}/name") as f:
                self.card = f.read().strip()
        except OSError:
            pass
        logger.info(
            "V4L2 camera '%s' (%s) opened on %s: %dx%d, hw exposure %.4f..%.2fs",
            self.card, self.usb_id or "?", self.device_path, self.width, self.height,
            self.min_exposure_s, self.hw_max_exposure_s,
        )
        self.set_hardware_exposure(self.current_exposure_s, self.current_gain_db)
        return True

    def close(self) -> None:
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass
        self.cap = None
        self.is_open = False

    def unlock_extension_unit(self) -> bool:
        return True

    def set_hardware_exposure(self, exposure_s: float, gain_db: float) -> Dict[str, Any]:
        exp = max(self.min_exposure_s, min(self.MAX_SOFT_EXPOSURE_S, float(exposure_s)))
        gain = max(0.0, min(self.max_gain_db, float(gain_db)))
        self._n_sub = max(1, int(math.ceil(exp / self.hw_max_exposure_s - 1e-9)))
        self._hw_exp_s = exp / self._n_sub
        self.current_exposure_s = exp
        self.current_gain_db = gain
        if self.is_open:
            if "auto_exposure" in self.controls:
                self._set_ctrl("auto_exposure", 1)  # V4L2_EXPOSURE_MANUAL
            name = "exposure_time_absolute" if "exposure_time_absolute" in self.controls else "exposure_absolute"
            self._set_ctrl(name, int(round(self._hw_exp_s * 1e4)))
            if "exposure_dynamic_framerate" in self.controls:
                self._set_ctrl("exposure_dynamic_framerate", 1)
            g = self.controls.get("gain")
            if g and self.max_gain_db > 0:
                raw = g.get("min", 0) + (g.get("max", 100) - g.get("min", 0)) * gain / self.max_gain_db
                self._set_ctrl("gain", int(round(raw)))
            # Drop frames buffered with the previous settings
            for _ in range(2):
                self.cap.grab()
        return {
            "requested_exposure_s": exposure_s,
            "actual_exposure_s": round(exp, 6),
            "gain_db": round(gain, 2),
            "sub_frames": self._n_sub,
            "VMAX": 0,
            "HMAX": 0,
            "SHR0": 0,
            "PGC": 0,
        }

    def capture_frame(self) -> Tuple[np.ndarray, Dict[str, Any]]:
        if not self.is_open:
            self.open()
        t0 = time.time()
        acc = None
        n = 0
        for _ in range(self._n_sub):
            ok, frame = self.cap.read()
            if not ok or frame is None:
                if n:
                    break
                raise RuntimeError(f"V4L2 read failed on {self.device_path}")
            acc = frame.astype(np.float32) if acc is None else acc + frame
            n += 1
        out = np.clip(acc, 0, 255).astype(np.uint8)
        now = time.time()
        delta_ms = (now - self._last_ts) * 1000.0 if self._last_ts else (now - t0) * 1000.0
        self._last_ts = now
        meta = {
            "hw_frame_delta": round(delta_ms, 2),
            "actual_exposure_s": round(self._hw_exp_s * n, 6),
            "gain_db": round(self.current_gain_db, 2),
            "sub_frames": n,
            "VMAX": 0,
            "HMAX": 0,
            "SHR0": 0,
            "PGC": 0,
            "timestamp": round(now, 4),
            "simulator": False,
            "camera_backend": self.backend,
        }
        return out, meta

    def get_info(self) -> Dict[str, Any]:
        return {
            "backend": self.backend,
            "vid_pid": self.usb_id or "v4l2",
            "sensor": f"{self.card} ({self.device_path})",
            "sensor_model": self.card,
            "pixel_pitch_um": 2.0,
            "native_width": self.width,
            "native_height": self.height,
            "has_registers": False,
        }

    def get_limits(self) -> Dict[str, float]:
        return {
            "min_exposure_s": self.min_exposure_s,
            "max_exposure_s": self.MAX_SOFT_EXPOSURE_S,
            "min_gain_db": 0.0,
            "max_gain_db": self.max_gain_db,
        }
