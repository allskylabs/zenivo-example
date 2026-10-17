"""
Focus assistant: sharpness metrics for a native-resolution region of interest.

Three methods, picked automatically or by the user:
- "stars":    median Half-Flux Radius (HFR, px) of unsaturated point sources. Lower is sharper.
- "lights":   the same HFR measure on bright, possibly saturated night lights (street lamps, windows),
              with larger stamps. Lower is sharper.
- "contrast": brightness-normalised Tenengrad (mean squared Sobel gradient) for daytime scenes.
              Higher is sharper.

Everything runs on the ROI only (a few hundred px square), so a measurement costs ~1-3 ms.
"""

import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional

import cv2
import numpy as np

METHODS = ("auto", "stars", "lights", "contrast")
LOWER_IS_BETTER = {"stars": True, "lights": True, "contrast": False}
# Scores within one family share a scale (stars and lights are both HFR in px)
FAMILY = {"stars": "hfr", "lights": "hfr", "contrast": "contrast"}
ROI_SIZES = (128, 192, 256, 384, 512, 768)
SMOOTH_FRAMES = (1, 3, 5, 9, 15)


def smooth_scores(scores: List[float]) -> float:
    """Robust average of the last frames: trimmed mean (min and max dropped) from 5 frames up, so one
    frame spoiled by heat shimmer, wind shake or a passing light does not move the result."""
    vals = sorted(scores)
    if len(vals) >= 5:
        vals = vals[1:-1]
    return float(np.mean(vals))


def score_noise_pct(scores: List[float]) -> Optional[float]:
    """Frame-to-frame score noise in % of the score: robust spread of consecutive differences / sqrt 2.
    Differences cancel the slow change from turning the lens, so this is the noise even mid-adjustment."""
    if len(scores) < 6:
        return None
    arr = np.asarray(scores, np.float64)
    d = np.diff(arr)
    sigma = 1.4826 * float(np.median(np.abs(d - np.median(d)))) / np.sqrt(2.0)
    ref = abs(float(np.median(arr)))
    return round(100.0 * sigma / ref, 2) if ref > 0 else None


def display_stretch(img: np.ndarray) -> np.ndarray:
    """Brighten a frame for viewing only (measurements always use the original): stretch the 0.5-99.7 %
    levels to full range and, for dark scenes, lift the shadows with a gamma so dim sky detail shows."""
    sample = img[::4, ::4]
    lo, hi = np.percentile(sample, (0.5, 99.7))
    if hi - lo < 2:
        return img
    med = (float(np.median(sample)) - lo) / (hi - lo)
    gamma = 1.0 if med >= 0.25 else max(0.35, np.log(0.25) / np.log(max(med, 1e-3)))
    ramp = np.clip((np.arange(256, dtype=np.float32) - lo) / (hi - lo), 0, 1) ** gamma
    return cv2.LUT(img, (ramp * 255.0 + 0.5).astype(np.uint8))


def _to_gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 3:
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return img


def _background(gray: np.ndarray):
    """Median background and robust (MAD) noise of an image."""
    sample = gray[::2, ::2] if gray.size > 65536 else gray
    bg = float(np.median(sample))
    noise = float(np.median(np.abs(sample.astype(np.float32) - bg))) * 1.4826
    return bg, max(noise, 0.75)


def _point_sources(gray: np.ndarray, lights: bool, max_sources: int = 25) -> List[Dict[str, Any]]:
    """Detect point sources and measure each one's HFR on a background-subtracted stamp."""
    h, w = gray.shape
    bg, noise = _background(gray)
    sat_level = 250 if gray.dtype == np.uint8 else float(gray.max()) * 0.98
    # Light smoothing for peak finding only; a median filter would erase a perfectly focused star
    smooth = cv2.GaussianBlur(gray, (3, 3), 0)
    k_sigma = 8.0 if lights else 5.0
    thr = bg + max(k_sigma * noise, 12.0 if lights else 4.0)
    win = 15 if lights else 7
    dil = cv2.dilate(smooth, np.ones((win, win), np.uint8))
    ys, xs = np.nonzero((smooth == dil) & (smooth > thr))
    if len(xs) == 0:
        return []
    order = np.argsort(smooth[ys, xs])[::-1]
    radius = 32 if lights else 14
    yy, xx = np.mgrid[-radius:radius + 1, -radius:radius + 1].astype(np.float32)
    g32 = gray.astype(np.float32)
    taken: List[Dict[str, Any]] = []
    for idx in order[: max_sources * 4]:
        x, y = int(xs[idx]), int(ys[idx])
        if x < radius or y < radius or x >= w - radius or y >= h - radius:
            continue
        if any(abs(x - s["x"]) <= radius and abs(y - s["y"]) <= radius for s in taken):
            continue
        stamp = g32[y - radius:y + radius + 1, x - radius:x + radius + 1]
        peak = float(stamp.max())
        saturated = peak >= sat_level
        if saturated and not lights:
            continue
        # Local background from the stamp's border ring
        ring = np.concatenate([stamp[0], stamp[-1], stamp[1:-1, 0], stamp[1:-1, -1]])
        local_bg = float(np.median(ring))
        s = stamp - local_bg
        # Keep only the source itself: the connected blob above 2.5 sigma that holds the peak, grown by
        # one pixel for the faint wings. Scattered noise pixels far from the core would inflate HFR.
        mask = (s > 2.5 * noise).astype(np.uint8)
        n_lbl, labels = cv2.connectedComponents(mask, connectivity=8)
        if n_lbl < 2:
            continue
        py, px = np.unravel_index(int(np.argmax(cv2.GaussianBlur(s, (3, 3), 0))), s.shape)
        lbl = labels[py, px]
        if lbl == 0:
            continue
        blob = labels == lbl
        n_px = int(blob.sum())
        # A single hot pixel, or a blob touching the stamp edge (cloud edge, extended object)
        if n_px < 2 or blob[0].any() or blob[-1].any() or blob[:, 0].any() or blob[:, -1].any():
            continue
        keep = cv2.dilate(blob.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
        sv = np.where(keep, np.clip(s, 0, None), 0.0)
        flux = float(sv.sum())
        if flux <= 0:
            continue
        cx = float((sv * xx).sum() / flux)
        cy = float((sv * yy).sum() / flux)
        r = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
        hfr = float((r * sv).sum() / flux)
        if hfr > radius * 0.6:
            continue
        taken.append({
            "x": int(round(x + cx)), "y": int(round(y + cy)), "hfr": round(hfr, 3), "peak": round(peak, 1),
            "snr": round((peak - local_bg) / noise, 1), "saturated": bool(saturated), "pixels": n_px,
        })
        if len(taken) >= max_sources:
            break
    return taken


def _contrast(gray: np.ndarray) -> float:
    """Brightness-normalised Tenengrad: mean squared gradient / mean^2 (exposure-independent)."""
    g = gray.astype(np.float32)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    mean = float(g.mean())
    return float((gx * gx + gy * gy).mean()) / max(mean * mean, 1.0) * 100.0


def measure_focus(img: np.ndarray, method: str = "auto") -> Dict[str, Any]:
    """Measure the sharpness of an ROI (BGR or gray). Returns score, method and per-source details."""
    t0 = time.perf_counter()
    gray = _to_gray(img)
    bg, noise = _background(gray)
    result: Dict[str, Any] = {"background": round(bg, 1), "noise": round(noise, 2)}

    if method not in METHODS:
        method = "auto"
    chosen = method
    sources: List[Dict[str, Any]] = []
    if method == "auto":
        # Dark sky/scene: try stars, then lights; bright scene or nothing point-like: contrast
        if bg < 90:
            sources = _point_sources(gray, lights=False)
            chosen = "stars"
            if len(sources) < 3:
                lit = _point_sources(gray, lights=True)
                if lit:
                    sources, chosen = lit, "lights"
                elif not sources:
                    chosen = "contrast"
        else:
            chosen = "contrast"
    elif method in ("stars", "lights"):
        sources = _point_sources(gray, lights=(method == "lights"))

    result["method"] = chosen
    result["requested_method"] = method
    result["lower_is_better"] = LOWER_IS_BETTER[chosen]
    if chosen == "contrast":
        result["score"] = round(_contrast(gray), 4)
        result["unit"] = "sharpness"
        result["sources"] = []
        result["n_sources"] = 0
    else:
        hfrs = [s["hfr"] for s in sources]
        result["sources"] = sources[:12]
        result["n_sources"] = len(sources)
        if hfrs:
            hfr = float(np.median(hfrs))
            result["score"] = round(hfr, 3)
            result["hfr"] = round(hfr, 3)
            result["fwhm"] = round(2.0 * hfr, 2)  # Gaussian profile: FWHM = 2 x HFR
        else:
            result["score"] = None
        result["unit"] = "HFR px"
        # Contrast as a secondary readout for when there are too few sources
        result["contrast"] = round(_contrast(gray), 4)
    result["calc_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)
    return result


def find_best_star(img: np.ndarray, lights: bool = False) -> Optional[Dict[str, Any]]:
    """Brightest well-measured point source in a (downscaled) frame, for picking the ROI automatically."""
    gray = _to_gray(img)
    sources = _point_sources(gray, lights=lights, max_sources=40)
    if not sources and not lights:
        sources = _point_sources(gray, lights=True, max_sources=40)
    if not sources:
        return None
    # Prefer strong but unsaturated sources
    sources.sort(key=lambda s: (s["saturated"], -s["snr"]))
    return sources[0]


class FocusSession:
    """State of an interactive focusing session (ROI, method, score history and best score)."""

    HISTORY = 240

    def __init__(self) -> None:
        self.active = False
        self.started_at = 0.0
        self.last_poll = 0.0
        self.last_action = 0.0  # last start/config/metering/exposure change by a user
        self.sweep: Optional[Dict[str, Any]] = None  # lens autofocus status (motorised lenses)
        self.sweep_cancel = False
        self.cx = 0.5          # ROI centre, normalised to the native frame
        self.cy = 0.5
        self.roi_size = 256    # native pixels
        self.method = "auto"
        self.smooth = 5        # frames in the robust running average
        self.history: Deque[Dict[str, Any]] = deque(maxlen=self.HISTORY)
        self.best: Optional[float] = None
        self.best_method: Optional[str] = None
        self._family_misses = 0
        self.last_result: Optional[Dict[str, Any]] = None
        self.result_key: Optional[tuple] = None
        self.crop_jpeg: Optional[bytes] = None
        self.overview_key: Optional[float] = None
        self.overview_jpeg: Optional[bytes] = None

    def reset_stats(self) -> None:
        self.history.clear()
        self.best = None
        self.best_method = None
        self.result_key = None

    def configure(self, cx=None, cy=None, roi_size=None, method=None, smooth=None) -> None:
        changed = False
        if smooth is not None:
            n = int(smooth)
            n = min(SMOOTH_FRAMES, key=lambda s: abs(s - n))
            if n != self.smooth:
                self.smooth = n
                changed = True
        if cx is not None:
            self.cx = min(1.0, max(0.0, float(cx)))
            changed = True
        if cy is not None:
            self.cy = min(1.0, max(0.0, float(cy)))
            changed = True
        if roi_size is not None:
            size = int(roi_size)
            self.roi_size = min(ROI_SIZES, key=lambda s: abs(s - size))
            changed = True
        if method is not None and method in METHODS and method != self.method:
            self.method = method
            changed = True
        if changed:
            # A different region or method makes old scores incomparable
            self.reset_stats()

    def roi_rect(self, frame_w: int, frame_h: int):
        size = min(self.roi_size, frame_w, frame_h)
        x0 = int(round(self.cx * frame_w - size / 2))
        y0 = int(round(self.cy * frame_h - size / 2))
        x0 = min(max(0, x0), frame_w - size)
        y0 = min(max(0, y0), frame_h - size)
        return x0, y0, size, size

    def measure(self, img: np.ndarray) -> Dict[str, Any]:
        """measure_focus() with auto mode held on one score family, so a single frame without stars
        (passing cloud, wind shake) does not throw away the history; it switches after 5 misses."""
        result = measure_focus(img, self.method)
        if self.method != "auto" or self.best_method is None:
            return result
        if FAMILY[result["method"]] == FAMILY[self.best_method] and result.get("score") is not None:
            self._family_misses = 0
            return result
        self._family_misses += 1
        if self._family_misses >= 5:
            self._family_misses = 0
            return result
        held = self.best_method if FAMILY[self.best_method] != "hfr" else "lights"
        alt = measure_focus(img, held)
        if alt.get("score") is None and held == "lights":
            alt = measure_focus(img, "stars")
        alt["requested_method"] = "auto"
        return alt

    def record(self, result: Dict[str, Any], ts: float) -> Dict[str, Any]:
        score = result.get("score")
        method = result.get("method")
        if FAMILY.get(method) != FAMILY.get(self.best_method):
            # Auto mode switched from HFR to contrast or back: scores are on another scale
            self.history.clear()
            self.best = None
            self.best_method = method
        smoothed = None
        if score is not None:
            self.history.append({"t": round(ts, 3), "score": score})
            # Best and quality use the running average: single-frame noise (heat shimmer, camera JPEG,
            # sensor) is a few %, enough to set an unreachable "best" from one lucky frame
            raw = [p["score"] for p in self.history]
            recent = raw[-self.smooth:]
            smoothed = smooth_scores(recent)
            self.history[-1]["smoothed"] = round(smoothed, 4)
            lower = LOWER_IS_BETTER.get(method, False)
            if len(recent) >= min(self.smooth, len(raw)) and (
                self.best is None or (smoothed < self.best if lower else smoothed > self.best)
            ):
                self.best = round(smoothed, 4)
        out = dict(result)
        out["best"] = self.best
        out["smoothed"] = round(smoothed, 4) if smoothed is not None else None
        out["smooth"] = self.smooth
        noise = score_noise_pct([p["score"] for p in list(self.history)[-30:]])
        out["noise_pct"] = noise
        if smoothed is not None and self.best:
            lower = LOWER_IS_BETTER.get(method, False)
            ratio = (self.best / smoothed) if lower else (smoothed / self.best)
            out["quality_pct"] = round(max(0.0, min(1.0, ratio)) * 100.0, 1)
        else:
            out["quality_pct"] = None
        # History stays server-side (GET /api/focus); the per-frame result travels in an HTTP header,
        # which proxies such as nginx cap at 4-8 KB
        out["n_history"] = len(self.history)
        return out

    def state(self) -> Dict[str, Any]:
        return {
            "active": self.active,
            "started_at": self.started_at,
            "cx": self.cx,
            "cy": self.cy,
            "roi_size": self.roi_size,
            "roi_sizes": list(ROI_SIZES),
            "method": self.method,
            "methods": list(METHODS),
            "smooth": self.smooth,
            "smooth_frames": list(SMOOTH_FRAMES),
            "best": self.best,
            "best_method": self.best_method,
            "history": list(self.history),
        }
