"""
Calibration and Image Processing Engine for IMX335 AllSky Camera.
"""

from .dark import (
    DarkCalibrationInterface,
    DarkCalibrationEngine,
    verify_ac1_reduction,
)
from .stacking import (
    LiveStackingEngine,
    stack_frames_sigma_clipped,
)
from .auto_exposure import (
    DayNightProfile,
    DayNightAutoExposure,
    calculate_solar_elevation,
)

__all__ = [
    "DarkCalibrationInterface",
    "DarkCalibrationEngine",
    "verify_ac1_reduction",
    "LiveStackingEngine",
    "stack_frames_sigma_clipped",
    "DayNightProfile",
    "DayNightAutoExposure",
    "calculate_solar_elevation",
]
