"""
Astronomical Analytics Module for IMX335 AllSky Observatory.
Provides real-time star counting, cloud cover estimation,
and transient meteor streak detection.
"""

from .stars_clouds import (
    estimate_stars_and_clouds,
    detect_stars_and_clouds,
    detect_stars_blob,
    StarAndCloudAnalyzer,
)
from .meteor import (
    detect_meteors,
    extract_streak_moments,
    extract_event_crop,
    MeteorDetector,
)

__all__ = [
    "estimate_stars_and_clouds",
    "detect_stars_and_clouds",
    "detect_stars_blob",
    "StarAndCloudAnalyzer",
    "detect_meteors",
    "extract_streak_moments",
    "extract_event_crop",
    "MeteorDetector",
]
