"""
Meteors detection module alias (re-exports from meteor.py).
Ensures compatibility with both singular and plural module naming conventions.
"""

from .meteor import (
    detect_meteors,
    extract_streak_moments,
    extract_event_crop,
    MeteorDetector,
)

__all__ = [
    "detect_meteors",
    "extract_streak_moments",
    "extract_event_crop",
    "MeteorDetector",
]
