"""Sky clarity from catalogue stars: how many of the stars that should be visible are actually seen.

With the camera pointing solved, the stars brighter than a limit that sit on open sky are projected into the
frame. Each is "seen" when its pixel neighbourhood has a clear peak over the local background. Cloud, haze and
fog hide stars long before they look like cloud in the image, and an overcast sky with no colour structure
(which the image-based cloud map cannot judge) shows no stars at all. clarity = seen / expected.

Needs the star-solved pointing; returns None clarity when too few stars are expected (below the horizon,
masked, or a narrow field) to say anything.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Dict, Optional

import cv2
import numpy as np

from src.analytics.adsb import pointing_rotation
from src.calibration import bright_star_solver as bss

MIN_EXPECTED = 5
PEAK_SIGMA = 4.5
MIN_ALT_DEG = 8.0


def star_clarity(frame: np.ndarray, solution: Dict[str, Any], lat: float, lon: float, when: datetime,
                 sky_mask: Optional[np.ndarray] = None, max_mag: float = 3.4) -> Dict[str, Any]:
    """Expected vs seen catalogue stars in one frame (any size; the solution's pixel scale is rescaled)."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    gray = gray.astype(np.float32)
    h, w = gray.shape
    cx0, cy0 = solution["optical_center_px"]
    k = w / (2.0 * cx0)
    f, cx, cy = float(solution["focal_px"]) * k, cx0 * k, cy0 * k
    stars = [s for s in bss.load_deep_catalog() if s["mag"] <= max_mag]
    aa = bss.stars_alt_az(stars, lat, lon, when)
    keep = aa[:, 0] >= MIN_ALT_DEG
    if not keep.any():
        return {"expected": 0, "seen": 0, "clarity": None}
    enu = bss._enu(aa[keep, 0], aa[keep, 1])
    R = pointing_rotation(solution["pointing_az_deg"], solution["pointing_alt_deg"], solution.get("roll_deg", 0.0))
    xy, ok = bss._project(R, enu, f, cx, cy, solution.get("projection", "rectilinear"),
                          float(solution.get("distortion_k") or 0.0))
    mags = np.array([s["mag"] for s in stars])[keep]
    margin = 14
    expected = seen = 0
    for (x, y), good, mag in zip(xy, ok, mags):
        if not good or not (margin <= x < w - margin and margin <= y < h - margin):
            continue
        xi, yi = int(round(x)), int(round(y))
        if sky_mask is not None:
            my, mx = sky_mask.shape
            if not sky_mask[min(my - 1, yi * my // h), min(mx - 1, xi * mx // w)]:
                continue
        expected += 1
        core = gray[yi - 2:yi + 3, xi - 2:xi + 3]
        ring = gray[yi - 12:yi + 13, xi - 12:xi + 13].copy()
        ring[8:17, 8:17] = np.nan
        bg = float(np.nanmedian(ring))
        sigma = max(1.4826 * float(np.nanmedian(np.abs(ring - bg))), 1.0)
        if float(core.max()) - bg > max(PEAK_SIGMA * sigma, 6.0):
            seen += 1
    clarity = round(seen / expected, 2) if expected >= MIN_EXPECTED else None
    return {"expected": expected, "seen": seen, "clarity": clarity}
