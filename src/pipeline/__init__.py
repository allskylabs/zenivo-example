"""
Astronomical Pipeline Module for IMX335 AllSky Observatory.
Provides automated generation of Keograms, Startrails composites,
Timelapse videos, and Astrometric Fisheye Overlays.
"""

from .keogram import generate_keogram, extract_meridian_slice, KeogramGenerator
from .startrails import generate_startrails, StartrailsAccumulator
from .timelapse import compile_timelapse, render_metadata_overlay
from .overlay import (
    project_alt_az_to_fisheye,
    render_celestial_overlay,
    calculate_local_sidereal_time,
    equatorial_to_horizontal,
    BRIGHT_STARS_CATALOG,
    CONSTELLATION_LINES,
)

__all__ = [
    "generate_keogram",
    "extract_meridian_slice",
    "KeogramGenerator",
    "generate_startrails",
    "StartrailsAccumulator",
    "compile_timelapse",
    "render_metadata_overlay",
    "project_alt_az_to_fisheye",
    "render_celestial_overlay",
    "calculate_local_sidereal_time",
    "equatorial_to_horizontal",
    "BRIGHT_STARS_CATALOG",
    "CONSTELLATION_LINES",
]
