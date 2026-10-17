"""
Hardware module for Sony IMX335 + Realtek RTS5869 AllSky Camera subsystem.
"""

from .constants import (
    SENSOR_WIDTH,
    SENSOR_HEIGHT,
    DEFAULT_HMAX,
    DEFAULT_VMAX,
    BASE_LINE_TIME_S,
    INCK_HZ,
    MIN_EXPOSURE_S,
    MAX_EXPOSURE_S,
    MIN_GAIN_DB,
    MAX_GAIN_DB,
)
from .registers import (
    calculate_exposure_registers,
    calculate_gain_register,
    decode_exposure_timing,
    format_timing_summary,
)
from .xu import RTS5869XUController, find_rts5869_device_node
from .v4l2_mmap import V4L2MmapCaptureSession
from .simulator import SimulatedIMX335Controller
from .driver import (
    CameraDriverInterface,
    RTS5869CameraController,
    get_camera_driver,
)

__all__ = [
    "SENSOR_WIDTH",
    "SENSOR_HEIGHT",
    "DEFAULT_HMAX",
    "DEFAULT_VMAX",
    "BASE_LINE_TIME_S",
    "INCK_HZ",
    "MIN_EXPOSURE_S",
    "MAX_EXPOSURE_S",
    "MIN_GAIN_DB",
    "MAX_GAIN_DB",
    "calculate_exposure_registers",
    "calculate_gain_register",
    "decode_exposure_timing",
    "format_timing_summary",
    "RTS5869XUController",
    "find_rts5869_device_node",
    "V4L2MmapCaptureSession",
    "SimulatedIMX335Controller",
    "CameraDriverInterface",
    "RTS5869CameraController",
    "get_camera_driver",
]
