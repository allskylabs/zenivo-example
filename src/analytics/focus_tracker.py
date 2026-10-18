"""
Night focus tracking for motorised lenses.

A lens' nominal infinity is not where stars are sharpest, and the best position drifts with temperature
during the night. After a focus sweep has found the real best position, this nudges the lens by +-delta
on two ordinary captures every interval, compares the median star HFR of the three frames and moves the
lens toward the sharper side (parabola vertex, at most delta per cycle, at most max_offset from the swept
position). It costs three star measurements per interval and never stops the normal capture.
"""

from collections import deque
from typing import Any, Callable, Deque, Dict, Optional

import numpy as np

from src.analytics.focus import measure_focus

# An all-sky camera looks at things metres to kilometres away: a focus nearer than 1 m (1 dioptre) is a
# window frame, a railing or dust on the dome, never the sky. Such results are not kept as sky focus.
SKY_MAX_DIOPTRES = 1.0


def star_hfr(frame: np.ndarray, crop: int = 1024, min_stars: int = 8) -> Optional[float]:
    """Median HFR of stars in a central native-resolution crop, or None with too few stars (cloud, dawn)."""
    h, w = frame.shape[:2]
    cw, ch = min(crop, w), min(crop, h)
    x0, y0 = (w - cw) // 2, (h - ch) // 2
    res = measure_focus(frame[y0:y0 + ch, x0:x0 + cw], "stars")
    if res.get("score") is None or res.get("n_sources", 0) < min_stars:
        return None
    return float(res["score"])


class FocusTracker:
    """State machine driven once per normal capture: idle -> plus -> minus -> decide -> idle."""

    def __init__(self, delta: float = 0.04, interval_s: float = 1200.0, max_offset: float = 0.3,
                 noise_frac: float = 0.015):
        self.enabled = True
        self.delta = delta
        self.interval_s = interval_s
        self.max_offset = max_offset
        self.noise_frac = noise_frac
        self.base: Optional[float] = None       # position found by the last sweep / manual focus
        self.phase: Optional[str] = None
        self.kind: Optional[str] = None
        self.p0: Optional[float] = None
        self.scores: Dict[str, Optional[float]] = {}
        self.next_ts = 0.0
        self.log: Deque[Dict[str, Any]] = deque(maxlen=48)

    def reset(self, base: Optional[float], now: float = 0.0) -> None:
        self.base = base
        self.phase = None
        self.scores = {}
        self.next_ts = now + self.interval_s if now else 0.0

    def _clamp(self, pos: float, lo: float, hi: float) -> float:
        if self.base is not None:
            pos = min(self.base + self.max_offset, max(self.base - self.max_offset, pos))
        return round(min(hi, max(lo, pos)), 4)

    DAY_NOISE_FRAC = 0.04  # daytime contrast moves with sun and drifting cloud: demand a clear win

    def step(self, now: float, frame: np.ndarray, reported: Optional[float], target: Optional[float],
             lens_range, is_night: bool, temp_c: Optional[float] = None,
             day_metric: Optional[Callable[[np.ndarray], Optional[float]]] = None) -> Optional[float]:
        """Feed one captured frame. Returns a lens position to move to, or None to leave the lens alone.

        At night the measure is the median star HFR of a central crop. By day, when day_metric is given
        (lower = sharper, e.g. inverse contrast of a detailed tile), the same probe runs on that instead."""
        lo, hi = float(lens_range[0]), float(lens_range[1])
        if not self.enabled or self.base is None or target is None:
            return None
        kind = "stars" if is_night else ("day" if day_metric is not None else None)
        if self.phase is not None and kind != self.kind:
            self.phase = None  # dawn/dusk mid-cycle: put the lens back
            return self.p0
        if kind is None:
            return None
        metric = star_hfr if kind == "stars" else day_metric

        def arrived(pos):
            return reported is None or abs(float(reported) - pos) < 0.006

        if self.phase is None:
            if now < self.next_ts or not arrived(target):
                return None
            s0 = metric(frame)
            if s0 is None:
                self.next_ts = now + 300.0  # cloudy, too few stars or no detail: try again in 5 min
                return None
            self.p0, self.scores, self.phase, self.kind = target, {"0": s0}, "plus", kind
            return self._clamp(self.p0 + self.delta, lo, hi)

        if self.phase == "plus":
            want = self._clamp(self.p0 + self.delta, lo, hi)
            if not arrived(want):
                return None
            self.scores["+"] = metric(frame)
            self.phase = "minus"
            return self._clamp(self.p0 - self.delta, lo, hi)

        # phase == "minus"
        want = self._clamp(self.p0 - self.delta, lo, hi)
        if not arrived(want):
            return None
        self.scores["-"] = metric(frame)
        self.phase = None
        self.next_ts = now + self.interval_s
        s0, sp, sm = self.scores["0"], self.scores.get("+"), self.scores.get("-")
        new = self.p0
        noise = self.noise_frac if kind == "stars" else self.DAY_NOISE_FRAC
        if sp is not None and sm is not None:
            d = self.delta
            curvature = sm - 2 * s0 + sp
            if curvature > 0:
                offset = d * (sm - sp) / (2 * curvature)  # vertex of the parabola through the three scores
            else:
                offset = d if sp < sm else -d
            offset = float(np.clip(offset, -d, d))
            best_side = min(sp, sm)
            # Move only when a probed side was really sharper (by day nothing else: drifting cloud and
            # changing light would otherwise walk the lens), or at night the fitted vertex is clearly off-centre
            if best_side < s0 * (1 - noise) or (kind == "stars" and curvature > 0 and abs(offset) > d * 0.5):
                new = self._clamp(self.p0 + offset, lo, hi)
        self.log.append({
            "t": round(now, 1), "temp_c": temp_c, "from": self.p0, "to": new, "kind": kind,
            "hfr" if kind == "stars" else "score": {k: (round(v, 4) if v is not None else None) for k, v in self.scores.items()},
        })
        return new

    def state(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "base": self.base,
            "phase": self.phase,
            "next_in_s": None,
            "delta": self.delta,
            "interval_s": self.interval_s,
            "max_offset": self.max_offset,
            "log": list(self.log)[-12:],
        }


class TempCompensation:
    """Learned focus-vs-temperature model for a motorised lens.

    Every verified focus (sweep, night tracking check, or a manual set in auto mode) is a sample
    (sensor temperature, lens position). With at least MIN_SAMPLES spanning MIN_SPAN_C, a least-squares
    line gives the drift in dioptres per degree; the lens then follows temperature from the latest
    verified position (the anchor) without any test frames. Until then the slope is 0 (no compensation).
    """

    MAX_SAMPLES = 80
    MIN_SAMPLES = 3
    MIN_SPAN_C = 3.0
    MAX_SLOPE = 0.02  # D per degree C; anything steeper is noise or a disturbed mount
    MAX_RMS_D = 0.05  # a fit worse than this explains nothing: keep the lens where it was verified
    OUTLIER_D = 0.15  # samples this far from the median position belong to another focus (re-mount, near object)

    def __init__(self, samples=None):
        self.samples = [dict(s) for s in (samples or [])
                        if s.get("temp_c") is not None and s.get("pos") is not None and s["pos"] <= SKY_MAX_DIOPTRES]

    def add(self, temp_c: Optional[float], pos: float, source: str, t: float) -> None:
        if temp_c is None or pos > SKY_MAX_DIOPTRES:
            return
        self.samples.append({"t": round(t, 1), "temp_c": round(float(temp_c), 2), "pos": round(float(pos), 4),
                             "source": source})
        self.samples = self.samples[-self.MAX_SAMPLES:]

    def fit(self) -> Dict[str, Any]:
        pos_all = np.array([s["pos"] for s in self.samples], np.float64)
        keep = [s for s in self.samples
                if len(pos_all) and abs(s["pos"] - float(np.median(pos_all))) <= self.OUTLIER_D]
        temps = np.array([s["temp_c"] for s in keep], np.float64)
        pos = np.array([s["pos"] for s in keep], np.float64)
        span = float(temps.max() - temps.min()) if len(temps) else 0.0
        out = {"n": len(temps), "n_rejected": len(self.samples) - len(keep), "span_c": round(span, 2),
               "slope": 0.0, "learned": False}
        if len(temps) >= self.MIN_SAMPLES and span >= self.MIN_SPAN_C:
            coef = np.polyfit(temps, pos, 1)
            rms = float(np.sqrt(np.mean((pos - np.polyval(coef, temps)) ** 2)))
            out["rms_d"] = round(rms, 4)
            if rms <= self.MAX_RMS_D:
                out.update({"slope": round(float(np.clip(coef[0], -self.MAX_SLOPE, self.MAX_SLOPE)), 5), "learned": True})
        return out

    def target(self, anchor_pos: float, anchor_temp: Optional[float], temp_c: Optional[float]) -> float:
        if anchor_temp is None or temp_c is None:
            return anchor_pos
        return anchor_pos + self.fit()["slope"] * (float(temp_c) - float(anchor_temp))
