"""Automatic sky mask proposal: open-sky and obstruction polygons from one camera frame.

Uses the same smooth-background detector as the plate solver (`estimate_sky_mask`), then turns the binary
mask into simplified polygons in frame fractions ([[x, y], ...], 0..1, x right, y down), the format the
Sky Mask editor stores. Holes inside a sky region (a tree in the middle of the frame) become obstruction
polygons. The result is only a proposal: the user reviews it in the editor and presses Apply.
"""

from typing import Any, Dict, List

import cv2
import numpy as np

from .bright_star_solver import estimate_sky_mask

MAX_POLYS = 16          # same limits as SkyMaskRequest / set_sky_mask
MAX_POINTS = 64
MIN_AREA_FRAC = 0.004   # ignore specks smaller than 0.4 % of the frame
_WORK_WIDTH = 480       # analyse a reduced frame: fast on a Raspberry Pi and smooths the outline


def _black_surround(gray: np.ndarray) -> bool:
    """True when the four corners are nearly black: a fisheye image circle on a dark frame."""
    h, w = gray.shape
    s = max(2, min(h, w) // 12)
    corners = [gray[:s, :s], gray[:s, -s:], gray[-s:, :s], gray[-s:, -s:]]
    return all(float(c.mean()) < 4.0 for c in corners)


def _polygons(binary: np.ndarray, hole_kind: bool = False) -> List[List[List[float]]]:
    h, w = binary.shape
    contours, hier = cv2.findContours(binary, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    out_outer, out_holes = [], []
    for i, c in enumerate(contours):
        if cv2.contourArea(c) < MIN_AREA_FRAC * h * w:
            continue
        eps = 0.004 * cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, eps, True)
        while len(approx) > MAX_POINTS:
            eps *= 1.4
            approx = cv2.approxPolyDP(c, eps, True)
        if len(approx) < 3:
            continue
        poly = [[round(float(x) / (w - 1), 4), round(float(y) / (h - 1), 4)] for x, y in approx.reshape(-1, 2)]
        is_hole = hier is not None and hier[0][i][3] != -1
        (out_holes if is_hole else out_outer).append((cv2.contourArea(c), poly))
    pick = out_holes if hole_kind else out_outer
    pick.sort(key=lambda t: -t[0])
    return [p for _, p in pick[:MAX_POLYS]]


def propose_sky_mask(frame_bgr: np.ndarray) -> Dict[str, Any]:
    """Polygons for the open sky and for obstructions inside it, plus the sky share of the frame.

    Returns {"sky": [...], "obstruction": [...], "sky_fraction": 0..1}; both lists are empty when no clear
    open-sky region is found (the caller then tells the user to draw the mask by hand)."""
    if frame_bgr is None or frame_bgr.size == 0:
        return {"sky": [], "obstruction": [], "sky_fraction": 0.0}
    h, w = frame_bgr.shape[:2]
    if w > _WORK_WIDTH:
        small = cv2.resize(frame_bgr, (_WORK_WIDTH, max(1, round(h * _WORK_WIDTH / w))), interpolation=cv2.INTER_AREA)
    else:
        small = frame_bgr
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY) if small.ndim == 3 else small
    m = estimate_sky_mask(small).astype(np.uint8)
    if _black_surround(gray):  # the dark area around a fisheye circle is smooth too: keep the image circle only
        circle = np.zeros_like(m)
        gh, gw = gray.shape
        cv2.circle(circle, (gw // 2, gh // 2), int(min(gh, gw) * 0.49), 1, -1)
        m &= circle
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    frac = float(m.mean())
    if frac < 0.05:
        return {"sky": [], "obstruction": [], "sky_fraction": round(frac, 3)}
    # pad by one pixel so a region touching the border still closes into a polygon
    padded = cv2.copyMakeBorder(m, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
    sky = _polygons(padded)
    holes = _polygons(padded, hole_kind=True)

    def unpad(polys):
        gh, gw = m.shape
        return [[[round(min(1.0, max(0.0, (x * (gw + 1) - 1) / (gw - 1))), 4),
                  round(min(1.0, max(0.0, (y * (gh + 1) - 1) / (gh - 1))), 4)] for x, y in p] for p in polys]

    return {"sky": unpad(sky), "obstruction": unpad(holes), "sky_fraction": round(frac, 3)}
