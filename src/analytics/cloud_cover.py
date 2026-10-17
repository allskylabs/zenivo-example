"""
Cloud cover from single allsky frames, day and night, plus a small learned clear-sky reference.

Structure (day and night): the IMX708 NoIR sees clouds brighter than the sky gaps between them
(sunlit by day, lit by city lights at night), while its colours are too washed out for the classic
red/blue ratio. The luminance is detrended with a quadratic surface (removes vignetting, sun glow
and the horizon gradient) and split with Otsu; when the two classes are clearly apart the bright
class is cloud. A frame without that contrast is uniform: all clear or all overcast.

Uniform frames are decided by:
- night (sun below -6°): stars per cell of a coarse grid against the best counts seen in each cell
  recently (a slowly decaying maximum), so it adapts to the lens, light pollution and buildings;
- day: exposure-normalised sky brightness against the clear-sky level learned from mixed skies.

Everything runs on downscaled frames (~3 ms by day, ~100 ms by night on a Pi 4) and is sampled
every few minutes, never per frame.
"""

import json
import math
import os
import tempfile
import threading
from typing import Any, Dict, List, Optional, Sequence

import cv2
import numpy as np

from src.analytics.stars_clouds import detect_stars_blob

STRUCT_WIDTH = 240         # clouds are large; more pixels add nothing to the structure analysis
MIN_SEPARATION = 0.09      # Otsu class means this far apart (fraction of sky level) => clouds visible
OVERCAST_RATIO = 2.5       # uniform day sky this many times brighter than learned clear sky => overcast
GRID_COLS, GRID_ROWS = 8, 4
NIGHT_REF_DECAY = 0.9993   # per night sample (~5 min): reference half-life ~1 week of nights
MIN_NIGHT_SAMPLES = 24     # before this the per-cell star reference is not trusted
DEFAULT_CLEAR_STARS = 150  # total stars on a clear night until the reference has been learned


def _status(cc: Optional[float]) -> str:
    if cc is None:
        return "unknown"
    return "clear" if cc < 25.0 else ("scattered" if cc < 70.0 else "overcast")


def _log_radiance(level_dn: float, exposure_s: float, gain_db: float) -> float:
    """Log of the linear sky brightness per second of exposure at unity gain (JPEG gamma undone)."""
    lin = (max(0.5, level_dn) / 255.0) ** 2.2
    return math.log(lin / max(1e-9, exposure_s * 10.0 ** (gain_db / 20.0)))


def _crop_roi(img: np.ndarray, roi: Sequence[float]) -> np.ndarray:
    x0, y0, x1, y1 = (min(1.0, max(0.0, float(v))) for v in roi)
    if x1 - x0 < 0.05 or y1 - y0 < 0.05:
        return img
    h, w = img.shape[:2]
    return img[int(y0 * h):max(int(y0 * h) + 1, round(y1 * h)), int(x0 * w):max(int(x0 * w) + 1, round(x1 * w))]


class CloudReference:
    """Learned clear-sky references, persisted as a tiny JSON file (written at most once per sample)."""

    def __init__(self, path: Optional[str] = None):
        self.path = path
        self.lock = threading.Lock()
        self.reset(save=False)
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as fp:
                    d = json.load(fp)
                if len(d.get("night_cells") or []) == GRID_COLS * GRID_ROWS:
                    self.night_cells = [float(c) for c in d["night_cells"]]
                self.night_samples = int(d.get("night_samples", 0))
                self.day_clear = d.get("day_clear")
                self.day_samples = int(d.get("day_samples", 0))
            except Exception:
                pass

    def to_dict(self) -> Dict[str, Any]:
        return {
            "night_cells": [round(c, 2) for c in self.night_cells],
            "night_samples": self.night_samples,
            "day_clear": self.day_clear,
            "day_samples": self.day_samples,
        }

    def save(self) -> None:
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path) or ".", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fp:
                json.dump(self.to_dict(), fp)
            os.replace(tmp, self.path)
        except Exception:
            pass

    def reset(self, save: bool = True) -> None:
        """Forget everything learned (e.g. after moving or re-aiming the camera)."""
        self.night_cells: List[float] = [0.0] * (GRID_COLS * GRID_ROWS)
        self.night_samples = 0
        self.day_clear: Optional[float] = None  # log radiance / sin(sun elevation) of clear day sky
        self.day_samples = 0
        if save:
            self.save()


def analyze_structure(frame: np.ndarray) -> Dict[str, Any]:
    """Bright-cloud vs. dark-gap segmentation of a (cropped) frame.

    Returns valid_frac, saturated_frac, bimodal, separation, cloud_frac (bright class among valid
    pixels), clear_level / sky_level (DN of the dark class / all valid sky).
    """
    h, w = frame.shape[:2]
    sw = min(STRUCT_WIDTH, w)
    small = cv2.resize(frame, (sw, max(8, round(h * sw / w))), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY) if small.ndim == 3 else small
    # Median removes stars and other point sources: only cloud-sized structure should split the sky
    lum = cv2.medianBlur(gray, 5).astype(np.float32)
    peak = cv2.medianBlur(small.max(axis=2) if small.ndim == 3 else small, 5)
    saturated = peak >= 250
    valid = ~saturated & (lum > 4)
    out = {"valid_frac": float(valid.mean()), "saturated_frac": float(saturated.mean()), "bimodal": False,
           "separation": 0.0, "cloud_frac": None, "clear_level": None, "sky_level": None}
    if valid.sum() < 50:
        return out

    # Remove smooth gradients (vignetting, sun glow, horizon) that would otherwise split the sky in two
    hh, ww = lum.shape
    yy, xx = np.mgrid[0:hh, 0:ww].astype(np.float32)
    xx = xx / max(1, ww - 1) * 2.0 - 1.0
    yy = yy / max(1, hh - 1) * 2.0 - 1.0
    basis = np.stack([np.ones_like(xx), xx, yy, xx * xx, xx * yy, yy * yy], axis=-1)[valid]
    vals = lum[valid]
    coef, *_ = np.linalg.lstsq(basis, vals, rcond=None)
    sky_level = max(4.0, float(np.median(vals)))
    z = (vals - basis @ coef) / sky_level

    lo, hi = np.percentile(z, [1, 99])
    q = np.clip((z - lo) / max(1e-6, hi - lo) * 255.0, 0, 255).astype(np.uint8).reshape(-1, 1)
    t, _ = cv2.threshold(q, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    bright = q.ravel() > t
    if bright.all() or not bright.any():
        return {**out, "sky_level": sky_level, "clear_level": sky_level}
    sep = float(z[bright].mean() - z[~bright].mean())
    out.update({
        "bimodal": sep >= MIN_SEPARATION,
        "separation": round(sep, 3),
        "cloud_frac": float(bright.mean()),
        "clear_level": float(np.median(vals[~bright])),
        "sky_level": sky_level,
    })
    return out


def _night_stars(gray: np.ndarray, reference: Optional[CloudReference], learn: bool) -> Dict[str, Any]:
    """Star-based cloud fraction from per-cell counts against the learned clear-sky counts."""
    h, w = gray.shape[:2]
    n_stars, _, stars = detect_stars_blob(gray)
    counts = np.zeros(GRID_COLS * GRID_ROWS, np.float32)
    for s in stars:
        c = min(GRID_COLS - 1, int(s["x"] * GRID_COLS / w))
        r = min(GRID_ROWS - 1, int(s["y"] * GRID_ROWS / h))
        counts[r * GRID_COLS + c] += 1

    learned = False
    cells = np.full_like(counts, DEFAULT_CLEAR_STARS / counts.size)
    if reference is not None:
        with reference.lock:
            ref = np.array(reference.night_cells, np.float32)
            if learn:
                # Slowly decaying maximum: rises quickly on clear nights, forgets a re-aimed camera in ~a week
                ref = np.where(counts > ref, ref + 0.25 * (counts - ref), ref * NIGHT_REF_DECAY)
                reference.night_cells = ref.tolist()
                reference.night_samples += 1
                reference.save()
            learned = reference.night_samples >= MIN_NIGHT_SAMPLES
        if learned:
            cells = ref
    # Cells that never show stars (buildings, street lights, lens corners) carry no information
    informative = cells >= 1.5
    if not informative.any():
        return {"star_count": int(n_stars), "star_cloud": None, "learned": learned, "coverage": 0.0}
    clear = np.minimum(1.0, counts[informative] / cells[informative])
    return {
        "star_count": int(n_stars),
        "star_cloud": 1.0 - float(np.average(clear, weights=cells[informative])),
        "learned": learned,
        "coverage": float(informative.mean()),
    }


def analyze_cloud_cover(
    frame: np.ndarray,
    exposure_s: float,
    gain_db: float,
    sun_elev: float,
    roi: Sequence[float] = (0.0, 0.0, 1.0, 1.0),
    reference: Optional[CloudReference] = None,
) -> Dict[str, Any]:
    """
    Estimate cloud cover of one frame.

    Args:
        frame: BGR or grayscale frame; star detection runs at the size given, so downscale first.
        exposure_s, gain_db: capture settings, used to normalise sky brightness.
        sun_elev: solar elevation in degrees; above -6° the day rules apply.
        roi: sky region as fractions (left, top, right, bottom) of the frame.
        reference: learned clear-sky reference, updated in place (None: fixed defaults, no learning).

    Returns:
        dict with cloud_cover_pct (None when the frame cannot be judged), status, method
        ("day" | "night"), confidence (0..1), star_count, separation and sky_brightness.
    """
    if frame is None or frame.size == 0:
        raise ValueError("Cannot analyse cloud cover on an empty frame")
    exposure_s = max(1e-6, float(exposure_s or 0.0))
    gain_db = float(gain_db or 0.0)
    night = sun_elev <= -6.0
    sky = _crop_roi(frame, roi)
    st = analyze_structure(sky)
    result: Dict[str, Any] = {"method": "night" if night else "day", "star_count": 0,
                              "separation": st["separation"], "sky_brightness": None}
    if st["valid_frac"] < 0.4:
        # Sun glare / overexposed (or black) frame: nothing trustworthy to measure
        status = "overexposed" if st["saturated_frac"] > 0.3 else "unknown"
        return {**result, "cloud_cover_pct": None, "status": status, "confidence": 0.0}
    log_rad = _log_radiance(st["clear_level"] or st["sky_level"], exposure_s, gain_db)
    result["sky_brightness"] = round(log_rad, 3)
    struct_cc = st["cloud_frac"] if st["bimodal"] else None

    cc: Optional[float] = None
    confidence = 0.0
    if night:
        gray = cv2.cvtColor(sky, cv2.COLOR_BGR2GRAY) if sky.ndim == 3 else sky
        stars = _night_stars(np.ascontiguousarray(gray), reference, learn=sun_elev < -12.0)
        result["star_count"] = stars["star_count"]
        star_cc = stars["star_cloud"]
        if struct_cc is not None and star_cc is not None and stars["learned"]:
            cc, confidence = 0.5 * struct_cc + 0.5 * star_cc, 0.85
        elif struct_cc is not None:
            cc, confidence = struct_cc, 0.7
        elif star_cc is not None:
            cc, confidence = star_cc, (0.75 if stars["learned"] else 0.4) * stars["coverage"] ** 0.5
        if sun_elev > -12.0:
            confidence *= 0.6  # nautical twilight hides faint stars and brightens the sky
    else:
        norm = log_rad - math.log(max(0.1, math.sin(math.radians(max(sun_elev, 1.0)))))
        day_clear = None
        if reference is not None:
            with reference.lock:
                if st["bimodal"] and 0.1 <= st["cloud_frac"] <= 0.9 and sun_elev > 10.0:
                    # Mixed sky: the dark class is genuinely clear sky, so it calibrates the clear level
                    reference.day_clear = norm if reference.day_clear is None else reference.day_clear + 0.1 * (norm - reference.day_clear)
                    reference.day_samples += 1
                    reference.save()
                if reference.day_samples >= 3:
                    day_clear = reference.day_clear
        if struct_cc is not None:
            cc, confidence = struct_cc, 0.75
        elif day_clear is not None:
            # Uniform sky: overcast if much brighter than the learned clear sky
            cc = min(1.0, max(0.0, (norm - day_clear - math.log(1.4)) / math.log(OVERCAST_RATIO / 1.4)))
            confidence = 0.6
        if sun_elev < 5.0:
            confidence *= 0.6
    if cc is None:
        return {**result, "cloud_cover_pct": None, "status": "unknown", "confidence": 0.0}
    confidence *= min(1.0, st["valid_frac"] / 0.8)
    cc_pct = round(100.0 * cc, 1)
    return {**result, "cloud_cover_pct": cc_pct, "status": _status(cc_pct), "confidence": round(confidence, 2)}
