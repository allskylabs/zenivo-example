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
ROI_MODES = ("auto", "manual")


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


def _point_sources(
    gray: np.ndarray,
    lights: bool,
    max_sources: int = 25,
    keep_mask: Optional[np.ndarray] = None,
) -> List[Dict[str, Any]]:
    """Detect point sources and measure each one's HFR (star size in px) on a background-subtracted stamp."""
    h, w = gray.shape
    sat_level = 244 if gray.dtype == np.uint8 else float(gray.max()) * 0.95
    # Local background by morphological opening (removes anything smaller than the kernel): the residual
    # (white top-hat) holds the point sources, and its noise is not inflated by buildings, trees or glow
    k = 31 if lights else 17
    opened = cv2.morphologyEx(gray, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    resid = cv2.subtract(gray, opened)
    # Pixel noise from horizontal neighbour differences plus top-hat residual IQR (handles Bayer ISP correlation)
    rows = gray[::2] if gray.size > 65536 else gray
    d = rows[:, 1:].astype(np.int16) - rows[:, :-1].astype(np.int16)
    noise_d1 = 1.4826 * float(np.median(np.abs(d - np.median(d)))) / np.sqrt(2.0)
    res_sample = resid[::2, ::2] if resid.size > 65536 else resid
    res_iqr = 0.7413 * float(np.percentile(res_sample, 75) - np.percentile(res_sample, 25))
    noise = max(noise_d1, res_iqr, 0.75)
    # Light smoothing for peak finding only; a median filter would erase a perfectly focused star
    smooth = cv2.GaussianBlur(resid, (3, 3), 0)
    k_sigma = 8.0 if lights else 5.0
    thr = max(k_sigma * noise, 20.0 if lights else 4.0)
    win = 15 if lights else 7
    dil = cv2.dilate(smooth, np.ones((win, win), np.uint8))
    cond = (smooth == dil) & (smooth > thr)
    if keep_mask is not None and keep_mask.shape == gray.shape:
        cond = cond & keep_mask
    ys, xs = np.nonzero(cond)
    if len(xs) == 0:
        return []
    order = np.argsort(smooth[ys, xs])[::-1]
    radius = 32 if lights else 16
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
        # Local background and local noise from the stamp's border ring (rejects textured clouds/buildings)
        ring = np.concatenate([stamp[0], stamp[-1], stamp[1:-1, 0], stamp[1:-1, -1]])
        local_bg = float(np.median(ring))
        ring_mad = 1.4826 * float(np.median(np.abs(ring - local_bg)))
        ring_iqr = 0.7413 * float(np.percentile(ring, 75) - np.percentile(ring, 25))
        ring_noise = max(ring_mad, ring_iqr, 1.0)
        eff_noise = max(noise, ring_noise)
        if peak - local_bg < max(k_sigma * eff_noise, 20.0 if lights else 4.0):
            continue
        s = stamp - local_bg
        s_smooth = cv2.GaussianBlur(s, (3, 3), 0)
        py, px = np.unravel_index(int(np.argmax(s_smooth)), s.shape)
        if py < 1 or py >= s.shape[0] - 1 or px < 1 or px >= s.shape[1] - 1:
            continue
        # Reject single/double hot pixels and Bayer-demosaiced spikes on raw unsmoothed stamp
        raw_mask = (s > 2.5 * eff_noise).astype(np.uint8)
        n_raw, raw_lbls = cv2.connectedComponents(raw_mask, connectivity=8)
        if n_raw < 2 or raw_lbls[py, px] == 0:
            continue
        raw_blob = raw_lbls == raw_lbls[py, px]
        if int(raw_blob.sum()) < 3:
            continue
        patch3 = s[py - 1:py + 2, px - 1:px + 2]
        if float(np.median(patch3)) < max(1.5 * eff_noise, 0.18 * float(s[py, px])):
            continue
        # Grow source footprint using 3x3 smoothed stamp (> 1.35 sigma) so faint defocused wings are not
        # clipped off into a 2-3 px tip (~1.16 px HFR) while avoiding background quantization drift.
        sm_mask = (s_smooth > 1.35 * eff_noise).astype(np.uint8)
        _, sm_lbls = cv2.connectedComponents(sm_mask, connectivity=8)
        blob = (sm_lbls == sm_lbls[py, px]) if sm_lbls[py, px] != 0 else raw_blob
        n_px = int(blob.sum())
        if n_px < 3 or blob[0].any() or blob[-1].any() or blob[:, 0].any() or blob[:, -1].any():
            continue
        keep = cv2.dilate(blob.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
        sv = np.where(keep, np.clip(s, 0, None), 0.0)
        flux = float(sv.sum())
        if flux <= 0:
            continue
        cx = float((sv * xx).sum() / flux)
        cy = float((sv * yy).sum() / flux)
        r = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
        hfr_mom = float((r * sv).sum() / flux)
        if not lights:
            ann = stamp[(r >= 8.5) & (r <= 15.0)]
            bg_ann = float(np.median(ann)) if ann.size else local_bg
            s_ap = stamp[r <= 6.0] - bg_ann - 1.0 * eff_noise
            f_ap = max(flux, float(np.clip(s_ap, 0, None).sum()))
            p_core = max(float(s[py, px]), 1.0)
            alpha = min(0.65, (1.35 * eff_noise) / p_core)
            f_tot = max(f_ap, flux / max(0.35, 1.0 - alpha))
            hfr_fp = 1.1774 * float(np.sqrt(max(0.0, f_tot) / (2.0 * np.pi * p_core)))
            hfr = max(hfr_mom, hfr_fp)
        else:
            hfr = hfr_mom
        if not lights and int(raw_blob.sum()) <= 4 and hfr < 1.22:
            continue
        if hfr > radius * 0.62:
            continue
        taken.append({
            "x": int(round(x + cx)), "y": int(round(y + cy)), "hfr": round(hfr, 3),
            "fwhm": round(2.0 * hfr, 2), "peak": round(peak, 1), "flux": round(flux, 1),
            "snr": round((peak - local_bg) / eff_noise, 1), "saturated": bool(saturated), "pixels": n_px,
        })
        if len(taken) >= max_sources:
            break
    taken.sort(key=lambda s: (s["saturated"], -s["flux"]))
    return taken


def _contrast(gray: np.ndarray) -> float:
    """Brightness-normalised Tenengrad: mean squared gradient / mean^2 (exposure-independent).
    A light sigma=1 blur first keeps sensor noise (dominant at night and high gain) from swamping the
    gradients; on a live daytime crop it kept ~70 % of the raw sensitivity to defocus."""
    g = cv2.GaussianBlur(gray.astype(np.float32), (0, 0), 1.0)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    mean = float(g.mean())
    return float((gx * gx + gy * gy).mean()) / max(mean * mean, 1.0) * 100.0


def measure_focus(img: np.ndarray, method: str = "auto", is_night: Optional[bool] = None) -> Dict[str, Any]:
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
        if is_night is False:
            chosen = "contrast"
        else:
            # Dark or urban-skyglow night sky: try stars first (HFR star size), then lights; daytime scene: contrast
            sample = gray[::2, ::2] if gray.size > 65536 else gray
            iqr = float(np.percentile(sample, 75) - np.percentile(sample, 25))
            if is_night is True or bg < 90 or (bg < 160 and iqr < 35):
                sources = _point_sources(gray, lights=False)
                chosen = "stars"
                if len(sources) < 3 and (is_night is True or bg < 90):
                    lit = _point_sources(gray, lights=True)
                    if lit:
                        sources, chosen = lit, "lights"
                    elif not sources:
                        chosen = "contrast"
                elif not sources:
                    lit = _point_sources(gray, lights=True) if (is_night is True or bg < 120) else []
                    if lit:
                        sources, chosen = lit, "lights"
                    else:
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
        result["sources"] = sources[:12]
        result["n_sources"] = len(sources)
        if sources:
            unsat = [s for s in sources if not s.get("saturated") and float(s.get("peak", 0.0)) < 244.0]
            pool = (unsat if unsat else sources)[:8]
            max_snr = max(float(s.get("snr", 0.0)) for s in pool)
            max_flux = max(float(s.get("flux", 0.0)) for s in pool)
            strong = [
                s for s in pool
                if float(s.get("snr", 0.0)) >= max_snr * 0.45 and float(s.get("flux", 0.0)) >= max_flux * 0.35
            ]
            if strong:
                pool = strong[:6]
            hfr = float(np.median([float(s["hfr"]) for s in pool]))
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


def find_best_star(
    img: np.ndarray,
    lights: bool = False,
    keep_mask: Optional[np.ndarray] = None,
) -> Optional[Dict[str, Any]]:
    """Well-exposed unsaturated point source in a (downscaled) frame, for picking the ROI automatically.
    Avoids saturated/near-clipped stars (whose flat-topped cores cannot measure HFR accurately) and
    penalises stars near the extreme frame edge where wide-angle lens coma is strong."""
    gray = _to_gray(img)
    h, w = gray.shape
    sources = _point_sources(gray, lights=lights, max_sources=40, keep_mask=keep_mask)
    if not sources and not lights:
        sources = _point_sources(gray, lights=True, max_sources=40, keep_mask=keep_mask)
    if not sources:
        return None

    def star_rank(s: Dict[str, Any]):
        sat = bool(s.get("saturated")) or float(s.get("peak", 0.0)) >= 244.0
        nx = s["x"] / max(w, 1)
        ny = s["y"] / max(h, 1)
        ex = max(0.0, 0.18 - nx, nx - 0.82)
        ey = max(0.0, 0.18 - ny, ny - 0.82)
        edge_pen = 1.0 + 12.0 * (ex + ey)
        return (sat, -float(s.get("flux", 0.0)) / edge_pen)

    sources.sort(key=star_rank)
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
        self.roi_mode = "auto"  # "auto" (automatic star/detail target) or "manual" (user-locked position)
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

    def configure(self, cx=None, cy=None, roi_size=None, method=None, smooth=None, roi_mode=None) -> None:
        changed = False
        if roi_mode is not None and roi_mode in ROI_MODES:
            self.roi_mode = roi_mode
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

    def measure(self, img: np.ndarray, is_night: Optional[bool] = None) -> Dict[str, Any]:
        """measure_focus() with auto mode held on one score family, so a single frame without stars
        (passing cloud, wind shake) does not throw away the history; it switches after 5 misses."""
        result = measure_focus(img, self.method, is_night=is_night)
        if self.method != "auto" or self.best_method is None or is_night is False:
            if is_night is False:
                self._family_misses = 0
            return result
        if FAMILY[result["method"]] == FAMILY[self.best_method] and result.get("score") is not None:
            self._family_misses = 0
            return result
        self._family_misses += 1
        if self._family_misses >= 5:
            self._family_misses = 0
            return result
        held = self.best_method if FAMILY[self.best_method] != "hfr" else "lights"
        alt = measure_focus(img, held, is_night=is_night)
        if alt.get("score") is None and held == "lights":
            alt = measure_focus(img, "stars", is_night=is_night)
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
            "roi_mode": self.roi_mode,
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


def find_focus_point(
    img: np.ndarray,
    grid_w: int = 12,
    sky_mask: Optional[np.ndarray] = None,
    is_night: Optional[bool] = None,
) -> Optional[Dict[str, Any]]:
    """Pick where to focus, as fractions of the frame: by night the best star (else the brightest point
    light), by day the tile with the most fine detail (buildings, rooftops, cloud edges), skipping blank
    sky and the outermost tiles (frame edge, nearby walls). sky_mask (1 = open sky, frame size) keeps the
    night search off buildings, their windows and lamps."""
    gray = _to_gray(img)
    h, w = gray.shape
    scale = min(1.0, 768.0 / w)
    small = cv2.resize(gray, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA) if scale < 1 else gray
    sh, sw = small.shape
    bright = float(np.percentile(small, 50))
    iqr = float(np.percentile(small, 75) - np.percentile(small, 25))
    try_stars = (is_night is True) or (is_night is not False and (bright < 60 or (bright < 155 and iqr < 40)))
    if try_stars:
        s_scale = min(1.0, 1152.0 / w)
        s_img = cv2.resize(gray, (int(w * s_scale), int(h * s_scale)), interpolation=cv2.INTER_AREA) if s_scale < 1 else gray
        s_h, s_w = s_img.shape
        keep = np.ones((s_h, s_w), dtype=bool)
        b = max(4, s_w // 25)
        keep[:b, :] = keep[-b:, :] = False
        keep[:, :b] = keep[:, -b:] = False
        has_sky_mask = False
        if sky_mask is not None:
            m = cv2.resize(sky_mask.astype(np.uint8), (s_w, s_h), interpolation=cv2.INTER_NEAREST) > 0
            m = cv2.erode(m.astype(np.uint8), np.ones((9, 9), np.uint8)) > 0  # not right at a roof edge
            m[:b, :] = m[-b:, :] = False
            m[:, :b] = m[:, -b:] = False
            if m.mean() > 0.05:
                keep = m
                has_sky_mask = True
        keep_unsat = keep
        if s_scale < 1.0:
            sat_native = cv2.resize((gray >= 242).astype(np.uint8), (s_w, s_h), interpolation=cv2.INTER_NEAREST)
            sat_native = cv2.dilate(sat_native, np.ones((7, 7), np.uint8)) > 0
            keep_unsat = keep & (~sat_native)
        star = find_best_star(s_img, lights=False, keep_mask=keep_unsat)
        if star is not None and is_night is None and bright >= 60 and (star["saturated"] or star["snr"] < 10.0):
            star = None
        if star is None and (is_night is True or bright < 60):
            star = find_best_star(s_img, lights=True, keep_mask=keep if not has_sky_mask else None)
        if star is not None:
            kind = "lights" if star["saturated"] else "stars"
            return {"cx": (star["x"] + 0.5) / s_w, "cy": (star["y"] + 0.5) / s_h, "method": kind, "detail": star}
    tile = max(32, sw // grid_w)
    best = None
    scores = []
    for ty in range(1, sh // tile - 1):
        for tx in range(1, sw // tile - 1):
            t = small[ty * tile:(ty + 1) * tile, tx * tile:(tx + 1) * tile]
            c = _contrast(t)
            scores.append(c)
            if best is None or c > best[0]:
                best = (c, tx, ty)
    if best is None or not scores:
        return None
    med = float(np.median(scores))
    if best[0] < max(med * 1.5, 1e-6):
        return None  # nothing stands out: blank sky, fog, uniform cloud
    c, tx, ty = best
    return {"cx": (tx + 0.5) * tile / sw, "cy": (ty + 0.5) * tile / sh, "method": "contrast",
            "detail": {"contrast": round(c, 4), "median_tile": round(med, 4)}}


def find_focus_tiles(img: np.ndarray, k: int = 8, grid_w: int = 12, border: bool = False,
                     min_sep: int = 2) -> List[Dict[str, Any]]:
    """The k most detailed tiles (as frame fractions), at least min_sep tiles apart, skipping the outermost
    tiles and anything not clearly above the frame's median detail. A daytime sweep measures them all at
    once, so a nearby wall among them cannot decide the focus."""
    gray = _to_gray(img)
    h, w = gray.shape
    scale = min(1.0, 768.0 / w)
    small = cv2.resize(gray, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA) if scale < 1 else gray
    sh, sw = small.shape
    tile = max(32, sw // grid_w)
    cells = []
    e = 0 if border else 1  # with border=True the outermost tiles count too (buildings often sit at the edge)
    ys = [ty * tile for ty in range(e, sh // tile - e)]
    if border and sh >= tile and (not ys or ys[-1] + tile < sh):
        ys.append(sh - tile)
    xs = [tx * tile for tx in range(e, sw // tile - e)]
    if border and sw >= tile and (not xs or xs[-1] + tile < sw):
        xs.append(sw - tile)
    for ty, y0 in enumerate(ys):
        for tx, x0 in enumerate(xs):
            cells.append((_contrast(small[y0:y0 + tile, x0:x0 + tile]), tx, ty, x0, y0))
    if not cells:
        return []
    med = float(np.median([c for c, *_ in cells]))
    picked: List[Tuple[float, int, int, int, int]] = []
    for c, tx, ty, x0, y0 in sorted(cells, reverse=True):
        if c < max(med * 1.2, 1e-6) or len(picked) >= k:
            break
        if any(max(abs(tx - px), abs(ty - py)) < min_sep for _, px, py, _, _ in picked):
            continue
        picked.append((c, tx, ty, x0, y0))
    return [{"cx": (x0 + 0.5 * tile) / sw, "cy": (y0 + 0.5 * tile) / sh, "contrast": round(c, 4)}
            for c, _, _, x0, y0 in picked]


def analyse_sweep(positions: List[float], scores: List[Optional[float]], lower: bool) -> Dict[str, Any]:
    """Judge one focus-vs-lens-position curve from a one-way sweep (positions ascending, in dioptres).

    ok=False with reason 'nothing' (no measurement), 'flat' (< 5 % change: blank sky, cloud, fog) or 'near'
    (still sharpening at the near end: a nearby object). Otherwise vertex is the peak of a least-squares
    parabola through the points within 3 steps of the 3-point-median peak, and spread the curve's relative
    range (how decisive it is)."""
    q = [float("-inf") if s is None else (-float(s) if lower else float(s)) for s in scores]
    finite = [v for v in q if np.isfinite(v)]
    if len(finite) < 3:
        return {"ok": False, "reason": "nothing"}
    spread = (max(finite) - min(finite)) / max(abs(max(finite)), 1e-9)
    if spread < 0.05:
        return {"ok": False, "reason": "flat", "spread": round(spread, 4)}
    med = []
    for i in range(len(q)):
        win = q[max(0, i - 1):i + 2]
        fin = [v for v in win if np.isfinite(v)]
        med.append(float(np.median(fin)) if np.isfinite(q[i]) and len(fin) >= min(2, len(win)) else float("-inf"))
    if not any(np.isfinite(v) for v in med):
        med = [float(np.median([v for v in q[max(0, i - 1):i + 2] if np.isfinite(v)] or [float("-inf")]))
               for i in range(len(q))]
    ib = int(np.argmax(med))
    if ib == len(q) - 1:
        return {"ok": False, "reason": "near", "spread": round(spread, 4), "peak_index": ib}
    w0, w1 = max(0, ib - 3), min(len(q), ib + 4)
    fx = np.array([positions[i] for i in range(w0, w1) if np.isfinite(q[i])])
    fy = np.array([q[i] for i in range(w0, w1) if np.isfinite(q[i])])
    vertex, fit = float(positions[ib]), None
    if len(fx) >= 4:
        x0 = float(positions[ib])
        a2, a1, a0 = np.polyfit(fx - x0, fy, 2)
        if a2 < 0:
            v = x0 - a1 / (2 * a2)
            if fx.min() - 1e-9 <= v <= fx.max() + 1e-9:
                vertex = float(v)
            fit = {"a": float(a2), "b": float(a1), "c": float(a0), "x0": x0, "from": float(fx.min()),
                   "to": float(fx.max()), "vertex": round(vertex, 4), "lower_is_better": bool(lower)}
    return {"ok": True, "vertex": round(vertex, 4), "fit": fit, "spread": round(spread, 4), "peak_index": ib,
            "peak_score": scores[ib]}
