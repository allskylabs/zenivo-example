"""
Cloud cover from single allsky frames, day and night, with a small learned clear-sky template.

Clouds differ from clear sky the same way day and night: they are less blue (white or grey cloud
against blue sky by day; city-lit, orange-grey cloud against the teal night sky). The colour cue is
log(B/R) per pixel of a ~160 px wide copy; at night clouds are also brighter than the sky between
them, so log luminance is a second cue.

The camera adds smooth patterns that would otherwise look like cloud: lens colour shading, light
pollution glow, vignetting, the horizon. They are removed in two steps:
- a learned per-pixel template (the static part: lens shading, glow, walls the mask misses), learned
  on dark, mostly clear nights as a running quantile of the residuals, so passing clouds average out;
- a quadratic surface per frame fitted to the clear side only (the bluest / darkest pixels), so it
  follows the sky between the clouds instead of splitting the frame in two.
A pixel is cloud when it is less blue (or, at night, brighter) than that surface by more than a fixed
margin or a few noise sigmas, whichever is larger.

Only the sky counts (station sky polygons and sky ROI, minus the info stamp corner), so buildings and
street lights do not. Stars are counted for display only: in a city sky most "stars" are windows and
lamps. The camera's auto white balance hides the absolute sky colour, so a uniformly overcast sky has
no structure to measure; such frames report little cloud with low confidence.

Runs in ~10 ms on a Pi 4 (plus star counting at night) and is sampled every few minutes, never per frame.
"""

import json
import math
import os
import tempfile
import threading
from typing import Any, Dict, Optional, Sequence, Tuple

import cv2
import numpy as np

from src.analytics.star_clarity import star_clarity
from src.analytics.stars_clouds import detect_stars_blob
from src.pipeline.star_boost import sky_polygon_mask

CLEAR_STAR_FRACTION = 0.75       # share of expected catalogue stars still seen on the clearest nights here
WORK_WIDTH = 160                  # clouds are large; more pixels add noise and CPU, not information
STAMP_BOX = (0.0, 0.0, 0.2, 0.1)  # info stamp in the top-left corner of archived / viewer frames
COLOUR_MARGIN_NIGHT = 0.04        # log(B/R) drop below the clear-sky surface that counts as cloud
COLOUR_MARGIN_DAY = 0.03
LUM_MARGIN_NIGHT = math.log(1.2)  # night: this much brighter than the clear-sky surface is cloud
NOISE_SIGMAS = 3.0                # ... or this many (smoothed) pixel-noise sigmas, whichever is larger
SMOOTH_SIGMA = 1.5                # Gaussian smoothing of the feature maps, in work pixels
TEMPLATE_RATE = 0.001             # per dark-night sample: relearns a re-aimed camera in a night or two
LEARN_MAX_CLOUD = 0.35            # only mostly clear frames teach the template (long overcast must not)
TEMPLATE_Q_COLOUR = 0.7           # clear sky is the bluer side of the colour residuals ...
TEMPLATE_Q_LUM = 0.3              # ... and the darker side of the luminance residuals
MIN_TEMPLATE_SAMPLES = 24         # before this the template is used but trusted less
MIN_SKY_LEVEL = 20.0              # median sky DN below this: black / failed frame
# Absolute day test: the envelope fit measures colour RELATIVE to the bluest part of the frame, so a uniform overcast
# (every pixel equally grey) looks like a clear sky. Here a sky with no clearly blue pixels at all is cloud.
DAY_BLUE_P90 = 1.20               # p90 of B/R at or above this: blue sky is visible, trust the relative map
DAY_GREY_P90 = 1.05               # p90 of B/R at or below this: a flat grey sky (uniform overcast)
DAY_BLUE_DUSK = 0.08              # added to DAY_BLUE_P90 with the sun below the horizon: even a grey dusk sky has a blue cast
DAY_SUN_DILATE = 9                # work pixels: extra margin around the saturated sun disc / glare for this test
# Night: a catalogue match this bad while many stars ARE visible means a stale pointing, not a cloudy sky
STALE_CLARITY = 0.1
STALE_MIN_STARS = 6


def _status(cc: Optional[float]) -> str:
    if cc is None:
        return "unknown"
    return "clear" if cc < 25.0 else ("scattered" if cc < 70.0 else "overcast")


def _log_radiance(level_dn: float, exposure_s: float, gain_db: float) -> float:
    """Log of the linear sky brightness per second of exposure at unity gain (JPEG gamma undone)."""
    lin = (max(0.5, level_dn) / 255.0) ** 2.2
    return math.log(lin / max(1e-9, exposure_s * 10.0 ** (gain_db / 20.0)))


def _basis(h: int, w: int) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    xx = xx / max(1, w - 1) * 2.0 - 1.0
    yy = yy / max(1, h - 1) * 2.0 - 1.0
    return np.stack([np.ones_like(xx), xx, yy, xx * xx, xx * yy, yy * yy], axis=-1)


def _envelope_fit(v: np.ndarray, valid: np.ndarray, basis: np.ndarray, upper: bool, iters: int = 5) -> np.ndarray:
    """Quadratic surface through the clear side of v: refitted to the pixels at most one robust sigma
    below it (upper=True) or above it (upper=False), so clouds on the other side do not pull it."""
    sel = valid
    fit = np.zeros_like(v)
    for _ in range(iters):
        coef, *_ = np.linalg.lstsq(basis[sel], v[sel], rcond=None)
        fit = basis @ coef
        r = v - fit
        rs = r[sel]
        s = 1.4826 * float(np.median(np.abs(rs - np.median(rs)))) + 1e-4
        new = valid & ((r > -s) if upper else (r < s))
        if new.sum() < 30:
            break
        sel = new
    return fit


def _pixel_noise(v: np.ndarray, valid: np.ndarray) -> float:
    """Robust per-pixel noise sigma from neighbour differences (cloud edges are rare outliers)."""
    ok = valid[:, 1:] & valid[:, :-1]
    d = (v[:, 1:] - v[:, :-1])[ok]
    if d.size < 10:
        return 0.0
    return 1.4826 * float(np.median(np.abs(d - np.median(d)))) / math.sqrt(2.0)


class CloudReference:
    """Learned clear-sky template (colour and luminance residual maps at the work size) and its sample
    count, persisted as <path> (JSON) and <path without .json>_template.npy (~60 KB)."""

    SAVE_EVERY = 3  # template samples between writes

    def __init__(self, path: Optional[str] = None):
        self.path = path
        self.lock = threading.Lock()
        self.reset(save=False)
        if not path:
            return
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as fp:
                    d = json.load(fp)
                if int(d.get("version", 1)) >= 2:  # version 1 held star counts, nothing reusable
                    self.night_samples = int(d.get("night_samples", 0))
            if self.night_samples and os.path.exists(self._template_path()):
                t = np.load(self._template_path())
                if t.ndim == 3 and t.shape[0] == 2:
                    self.template = t.astype(np.float32)
            if self.template is None:
                self.night_samples = 0
        except Exception:
            self.reset(save=False)

    def _template_path(self) -> str:
        base = self.path[:-5] if self.path and self.path.endswith(".json") else (self.path or "")
        return base + "_template.npy"

    def to_dict(self) -> Dict[str, Any]:
        return {"version": 2, "night_samples": self.night_samples,
                "learned": self.night_samples >= MIN_TEMPLATE_SAMPLES}

    def save(self) -> None:
        if not self.path:
            return
        try:
            d = os.path.dirname(self.path) or "."
            os.makedirs(d, exist_ok=True)
            if self.template is not None:
                fd, tmp = tempfile.mkstemp(dir=d, suffix=".npy")
                with os.fdopen(fd, "wb") as fp:
                    np.save(fp, self.template.astype(np.float16))
                os.replace(tmp, self._template_path())
            fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fp:
                json.dump(self.to_dict(), fp)
            os.replace(tmp, self.path)
        except Exception:
            pass

    def reset(self, save: bool = True) -> None:
        """Forget everything learned (e.g. after moving or re-aiming the camera)."""
        self.template: Optional[np.ndarray] = None  # (2, h, w): clear-sky colour and luminance residuals
        self.night_samples = 0
        if save:
            try:
                os.remove(self._template_path())
            except OSError:
                pass
            self.save()

    def learn(self, res_c: np.ndarray, res_l: np.ndarray, valid: np.ndarray) -> None:
        """One dark-night sample: move the template a small step towards the clear-sky quantile of the
        residuals (bluer side for colour, darker side for luminance)."""
        if self.template is None or self.template.shape[1:] != res_c.shape:
            self.template = np.zeros((2,) + res_c.shape, np.float32)
            self.night_samples = 0
        tc, tl = self.template[0], self.template[1]
        tc += TEMPLATE_RATE * valid * np.where(res_c < tc, TEMPLATE_Q_COLOUR - 1.0, TEMPLATE_Q_COLOUR)
        tl += TEMPLATE_RATE * valid * np.where(res_l < tl, TEMPLATE_Q_LUM - 1.0, TEMPLATE_Q_LUM)
        self.night_samples += 1
        if self.night_samples % self.SAVE_EVERY == 0:
            self.save()


_mask_cache: Dict[Any, np.ndarray] = {}


def sky_work_mask(shape: Tuple[int, int], sky_polygons: Optional[dict], roi: Sequence[float],
                  circle: Optional[Sequence[float]] = None) -> np.ndarray:
    """Boolean sky mask at the work size: sky polygons inside the ROI, minus the info stamp corner,
    eroded by a pixel so walls and roof edges do not bleed in. `circle` = (cx, cy, r) as fractions of the frame
    width/height/width: the lens image circle, so the dark rim and corners outside it are never judged."""
    key = (tuple(shape), repr(sky_polygons), tuple(round(float(v), 4) for v in roi),
           tuple(round(float(v), 3) for v in circle) if circle else None)
    m = _mask_cache.get(key)
    if m is not None:
        return m
    h, w = shape
    poly = sky_polygon_mask((h, w), sky_polygons)
    m = np.ones((h, w), np.uint8) if poly is None else poly.astype(np.uint8)
    x0, y0, x1, y1 = (min(1.0, max(0.0, float(v))) for v in roi)
    if x1 - x0 >= 0.05 and y1 - y0 >= 0.05:
        r = np.zeros_like(m)
        r[int(y0 * h):max(int(y0 * h) + 1, round(y1 * h)), int(x0 * w):max(int(x0 * w) + 1, round(x1 * w))] = 1
        m &= r
    if circle:
        yy, xx = np.mgrid[0:h, 0:w]
        m &= ((xx + 0.5 - circle[0] * w) ** 2 + (yy + 0.5 - circle[1] * h) ** 2) <= (circle[2] * w) ** 2
    sx0, sy0, sx1, sy1 = STAMP_BOX
    m[int(sy0 * h):math.ceil(sy1 * h), int(sx0 * w):math.ceil(sx1 * w)] = 0
    m = cv2.erode(m, np.ones((3, 3), np.uint8)) > 0
    if len(_mask_cache) > 8:
        _mask_cache.clear()
    _mask_cache[key] = m
    return m


def cloud_map(
    frame: np.ndarray,
    night: bool,
    sky_polygons: Optional[dict] = None,
    roi: Sequence[float] = (0.0, 0.0, 1.0, 1.0),
    template: Optional[np.ndarray] = None,
    want_residuals: bool = False,
    circle: Optional[Sequence[float]] = None,
) -> Dict[str, Any]:
    """Per-pixel cloud mask of one frame at the work size.

    Returns cloud (bool map, None when the frame cannot be judged), valid (judged sky pixels), sky_frac
    (valid share of the sky mask), saturated_frac, sky_level (median DN of the sky) and, with
    want_residuals, res_c / res_l: the residuals without the template, which the template learns from.
    """
    h, w = frame.shape[:2]
    ww = min(WORK_WIDTH, w)
    wh = max(8, round(h * ww / w))
    small = cv2.resize(frame, (ww, wh), interpolation=cv2.INTER_AREA).astype(np.float32)
    if small.ndim == 2:
        small = np.repeat(small[:, :, None], 3, axis=2)
    sky = sky_work_mask((wh, ww), sky_polygons, roi, circle)
    out: Dict[str, Any] = {"cloud": None, "valid": None, "sky_frac": 0.0, "saturated_frac": 0.0,
                           "sky_level": 0.0, "res_c": None, "res_l": None, "br_p90": None}
    if not sky.any():
        return out
    sat = small.max(axis=2) >= 248
    out["saturated_frac"] = float(sat[sky].mean())
    b, g, r = cv2.split(small + 1.0)
    lum = (b + g + r) / 3.0
    out["sky_level"] = float(np.median(lum[sky])) - 1.0
    valid = sky & ~cv2.dilate(sat.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool) & (lum > 6.0)
    out["valid"] = valid
    out["sky_frac"] = float(valid.sum()) / float(sky.sum())
    if valid.sum() < 50 or out["sky_frac"] < 0.3 or out["sky_level"] < MIN_SKY_LEVEL:
        return out

    c_raw, l_raw = np.log(b / r), np.log(lum)
    if not night:
        # Absolute blue test, away from the sun disc and its glare (saturated pixels plus a margin)
        far = valid & ~cv2.dilate(sat.astype(np.uint8), np.ones((DAY_SUN_DILATE,) * 2, np.uint8)).astype(bool)
        if far.sum() >= 50:
            out["br_p90"] = float(np.exp(np.percentile(c_raw[far], 90)))
    nc, nl = _pixel_noise(c_raw, valid), _pixel_noise(l_raw, valid)
    c = cv2.GaussianBlur(c_raw, (0, 0), SMOOTH_SIGMA)
    l = cv2.GaussianBlur(l_raw, (0, 0), SMOOTH_SIGMA)
    basis = _basis(wh, ww)
    smooth_gain = 1.0 / (math.sqrt(4.0 * math.pi) * SMOOTH_SIGMA)  # pixel noise left after the smoothing
    if want_residuals:
        out["res_c"] = c - _envelope_fit(c, valid, basis, upper=True)
        out["res_l"] = l - _envelope_fit(l, valid, basis, upper=False)
    if template is not None and template.shape[1:] == c.shape:
        c = c - template[0]
        l = l - template[1]

    margin_c = max(COLOUR_MARGIN_NIGHT if night else COLOUR_MARGIN_DAY, NOISE_SIGMAS * nc * smooth_gain)
    score = (_envelope_fit(c, valid, basis, upper=True) - c) / margin_c
    if night:
        margin_l = max(LUM_MARGIN_NIGHT, NOISE_SIGMAS * nl * smooth_gain)
        score = np.maximum(score, (l - _envelope_fit(l, valid, basis, upper=False)) / margin_l)
    out["cloud"] = (score > 1.0) & valid
    return out


def analyze_cloud_cover(
    frame: np.ndarray,
    exposure_s: float,
    gain_db: float,
    sun_elev: float,
    roi: Sequence[float] = (0.0, 0.0, 1.0, 1.0),
    reference: Optional[CloudReference] = None,
    sky_polygons: Optional[dict] = None,
    count_stars: bool = True,
    star_ref: Optional[Dict[str, Any]] = None,
    circle: Optional[Sequence[float]] = None,
    day_abs: bool = True,
) -> Dict[str, Any]:
    """
    Estimate cloud cover of one frame.

    Args:
        frame: BGR frame (grayscale works, without the colour cue); stars are counted at the size given.
        exposure_s, gain_db: capture settings, used for the reported sky brightness.
        sun_elev: solar elevation in degrees; at or below -6° the night rules apply.
        roi: sky region as fractions (left, top, right, bottom) of the frame.
        reference: learned clear-sky template, updated in place on dark nights (None: no template).
        sky_polygons: station sky mask ({"sky": [...], "obstruction": [...]} in frame fractions).
        count_stars: at night also count the stars in the sky (reported only).
        circle: lens image circle (cx, cy, r) in fractions of the frame (width, height, width); outside it is not sky.
        day_abs: by day also apply the absolute blue-sky test (a uniform overcast has no blue pixels at all).
        star_ref: {"solution", "lat", "lon", "when"} (star-solved pointing, station, aware datetime). At night
            the catalogue stars that should be visible are looked for; a sky showing far fewer than expected
            (thin cloud, haze, a uniform overcast the colour map cannot see) raises the cloud cover.

    Returns:
        dict with cloud_cover_pct (None when the frame cannot be judged), status, method
        ("day" | "night"), confidence (0..1), star_count, sky_fraction and sky_brightness.
    """
    if frame is None or frame.size == 0:
        raise ValueError("Cannot analyse cloud cover on an empty frame")
    exposure_s = max(1e-6, float(exposure_s or 0.0))
    gain_db = float(gain_db or 0.0)
    night = sun_elev <= -6.0
    learn = reference is not None and sun_elev < -12.0
    template, learned = None, False
    if reference is not None:
        with reference.lock:
            if reference.template is not None:
                template = reference.template.copy()
            learned = reference.night_samples >= MIN_TEMPLATE_SAMPLES
    cm = cloud_map(frame, night, sky_polygons, roi, template, want_residuals=learn, circle=circle)
    result: Dict[str, Any] = {"method": "night" if night else "day", "star_count": 0,
                              "sky_fraction": round(cm["sky_frac"], 3), "sky_brightness": None}
    if cm["sky_level"] > 0:
        result["sky_brightness"] = round(_log_radiance(cm["sky_level"], exposure_s, gain_db), 3)
    if cm["cloud"] is None:
        # Sun glare / overexposed, black or fully masked frame: nothing trustworthy to measure
        status = "overexposed" if cm["saturated_frac"] > 0.3 else "unknown"
        return {**result, "cloud_cover_pct": None, "status": status, "confidence": 0.0}

    valid = cm["valid"]
    cc = float(cm["cloud"][valid].mean())
    if learn and cc < LEARN_MAX_CLOUD:
        with reference.lock:
            reference.learn(cm["res_c"], cm["res_l"], valid)

    if day_abs and not night and cm["br_p90"] is not None:
        blue = DAY_BLUE_P90 + (DAY_BLUE_DUSK if sun_elev < 0.0 else 0.0)
        grey = (blue - cm["br_p90"]) / (blue - DAY_GREY_P90)
        result["day_grey"] = round(min(1.0, max(0.0, grey)), 2)
        cc = max(cc, result["day_grey"])

    if night and count_stars:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        _, _, stars = detect_stars_blob(np.ascontiguousarray(gray))
        fh, fw = gray.shape[:2]
        mh, mw = valid.shape
        result["star_count"] = sum(
            1 for s in stars if valid[min(mh - 1, int(s["y"] * mh / fh)), min(mw - 1, int(s["x"] * mw / fw))])

    clarity_used = False
    if star_ref is not None and sun_elev < -12.0:
        try:
            sc = star_clarity(frame, star_ref["solution"], star_ref["lat"], star_ref["lon"], star_ref["when"], valid)
            result.update(stars_expected=sc["expected"], stars_seen=sc["seen"], star_clarity=sc["clarity"],
                          star_shift_px=sc.get("shift_px"))
            if sc["clarity"] is not None:
                if sc["clarity"] < STALE_CLARITY and result["star_count"] >= STALE_MIN_STARS and cc < 0.3:
                    # Many stars stand out but none where the catalogue puts them: the stored pointing is stale
                    result["star_ref_stale"] = True
                else:
                    # A clear sky shows about CLEAR_STAR_FRACTION of the catalogue stars above the glow
                    cc = max(cc, max(0.0, 1.0 - sc["clarity"] / CLEAR_STAR_FRACTION))
                    clarity_used = True
        except Exception:
            pass

    confidence = 0.85 if night else 0.75
    if not learned:
        confidence *= 0.75
    if -12.0 < sun_elev < 5.0:
        confidence *= 0.6  # twilight: the sky colour changes across the frame by the minute
    if night and sun_elev < -12.0 and not clarity_used:
        confidence *= 0.7  # colour / luma only: no (valid) star evidence either way
    if cc < 0.05:
        confidence *= 0.7  # clear, or a uniform overcast with no structure to see
    confidence *= min(1.0, cm["sky_frac"] / 0.8)
    if cm["saturated_frac"] > 0.05:
        confidence *= 0.5  # sun glare / overexposure washes the cloud colour out around it
    cc_pct = round(100.0 * cc, 1)
    return {**result, "cloud_cover_pct": cc_pct, "status": _status(cc_pct), "confidence": round(confidence, 2)}
