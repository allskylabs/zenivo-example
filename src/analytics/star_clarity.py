"""Sky clarity from catalogue stars: how many of the stars that should be visible are actually seen.

With the camera pointing solved, the stars brighter than a limit that sit on open sky are projected into the
frame. Each is "seen" when its pixel neighbourhood has a clear peak over the local background. Cloud, haze and
fog hide stars long before they look like cloud in the image, and an overcast sky with no colour structure
(which the image-based cloud map cannot judge) shows no stars at all. clarity = seen / expected.

Needs the star-solved pointing; returns None clarity when too few stars are expected (below the horizon,
masked, or a narrow field) to say anything.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

import cv2
import numpy as np

from src.analytics.adsb import pointing_rotation
from src.calibration import bright_star_solver as bss

MIN_EXPECTED = 5
PEAK_SIGMA = 4.5
MIN_ALT_DEG = 8.0
MAX_SHIFT_FRAC = 0.04  # pointing drift searched, as a share of the frame width


def _peak_map(gray8: np.ndarray) -> np.ndarray:
    """True where a 5x5 core stands out of its surroundings: peak above 4.5 noise sigma (and 6 DN)."""
    bg = cv2.medianBlur(gray8, 25)
    sigma = np.maximum(1.4826 * cv2.medianBlur(cv2.absdiff(gray8, bg), 25).astype(np.float32), 1.0)
    core = cv2.dilate(gray8, np.ones((5, 5), np.uint8)).astype(np.float32)
    return (core - bg.astype(np.float32)) > np.maximum(PEAK_SIGMA * sigma, 6.0)


def star_clarity(frame: np.ndarray, solution: Dict[str, Any], lat: float, lon: float, when: datetime,
                 sky_mask: Optional[np.ndarray] = None, max_mag: float = 3.4) -> Dict[str, Any]:
    """Expected vs seen catalogue stars in one frame (any size; the solution's pixel scale is rescaled).

    The stored pointing drifts a little (a nudged mount, a re-solve at another size), so the projected
    star positions are searched over a small shift: the shift where clearly more stars stand out than at
    chance is used. Without such a shift (cloud, no stars) the stored pointing is used as is.
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    gray8 = np.clip(gray, 0, 255).astype(np.uint8)
    h, w = gray8.shape
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
    margin = 14
    pts = []
    for x, y in xy[ok]:
        if not (margin <= x < w - margin and margin <= y < h - margin):
            continue
        xi, yi = int(round(x)), int(round(y))
        if sky_mask is not None:
            my, mx = sky_mask.shape
            if not sky_mask[min(my - 1, yi * my // h), min(mx - 1, xi * mx // w)]:
                continue
        pts.append((xi, yi))
    expected = len(pts)
    if expected < MIN_EXPECTED:
        return {"expected": expected, "seen": 0, "clarity": None}
    peak = _peak_map(gray8)
    px = np.array([p[0] for p in pts])
    py = np.array([p[1] for p in pts])
    r = max(8, int(MAX_SHIFT_FRAC * w))
    shifts = np.arange(-r, r + 1)
    counts = np.zeros((shifts.size, shifts.size), np.int32)  # [dy, dx]
    for i, dy in enumerate(shifts):
        yy = np.clip(py + dy, 0, h - 1)
        for j, dx in enumerate(shifts):
            counts[i, j] = int(peak[yy, np.clip(px + dx, 0, w - 1)].sum())
    seen0 = int(counts[r, r])
    bi, bj = np.unravel_index(int(np.argmax(counts)), counts.shape)
    best, chance = int(counts[bi, bj]), float(np.median(counts))
    seen, shift = seen0, (0, 0)
    # a real shift lights up many stars at once; noise peaks give a few at random
    if best > seen0 and best >= max(MIN_EXPECTED, chance + 4.0 + 3.0 * chance ** 0.5):
        seen, shift = best, (int(shifts[bj]), int(shifts[bi]))
    return {"expected": expected, "seen": seen, "clarity": round(seen / expected, 2), "shift_px": shift}
