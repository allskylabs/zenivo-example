"""
Night focus tracking for motorised lenses.

A lens' nominal infinity is not where stars are sharpest, and the best position drifts with temperature
during the night. After a focus sweep has found the real best position, this nudges the lens by +-delta
on two ordinary captures every interval, compares the median star HFR of the three frames and moves the
lens toward the sharper side (parabola vertex, at most delta per cycle, at most max_offset from the swept
position). It costs three star measurements per interval and never stops the normal capture.
"""

from collections import deque
from typing import Any, Deque, Dict, Optional

import numpy as np

from src.analytics.focus import measure_focus


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

    def step(self, now: float, frame: np.ndarray, reported: Optional[float], target: Optional[float],
             lens_range, is_night: bool, temp_c: Optional[float] = None) -> Optional[float]:
        """Feed one captured frame. Returns a lens position to move to, or None to leave the lens alone."""
        lo, hi = float(lens_range[0]), float(lens_range[1])
        if not self.enabled or self.base is None or target is None:
            return None
        if not is_night:
            if self.phase is not None:  # dawn mid-cycle: put the lens back
                self.phase = None
                return self.p0
            return None

        def arrived(pos):
            return reported is None or abs(float(reported) - pos) < 0.006

        if self.phase is None:
            if now < self.next_ts or not arrived(target):
                return None
            s0 = star_hfr(frame)
            if s0 is None:
                self.next_ts = now + 300.0  # cloudy or too few stars: try again in 5 min
                return None
            self.p0, self.scores, self.phase = target, {"0": s0}, "plus"
            return self._clamp(self.p0 + self.delta, lo, hi)

        if self.phase == "plus":
            want = self._clamp(self.p0 + self.delta, lo, hi)
            if not arrived(want):
                return None
            self.scores["+"] = star_hfr(frame)
            self.phase = "minus"
            return self._clamp(self.p0 - self.delta, lo, hi)

        # phase == "minus"
        want = self._clamp(self.p0 - self.delta, lo, hi)
        if not arrived(want):
            return None
        self.scores["-"] = star_hfr(frame)
        self.phase = None
        self.next_ts = now + self.interval_s
        s0, sp, sm = self.scores["0"], self.scores.get("+"), self.scores.get("-")
        new = self.p0
        if sp is not None and sm is not None:
            d = self.delta
            curvature = sm - 2 * s0 + sp
            if curvature > 0:
                offset = d * (sm - sp) / (2 * curvature)  # vertex of the parabola through the three HFRs
            else:
                offset = d if sp < sm else -d
            offset = float(np.clip(offset, -d, d))
            best_side = min(sp, sm)
            # Move only when a probed side was really sharper, or the fitted vertex is clearly off-centre
            if best_side < s0 * (1 - self.noise_frac) or (curvature > 0 and abs(offset) > d * 0.5):
                new = self._clamp(self.p0 + offset, lo, hi)
        self.log.append({
            "t": round(now, 1), "temp_c": temp_c, "from": self.p0, "to": new,
            "hfr": {k: (round(v, 3) if v is not None else None) for k, v in self.scores.items()},
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

    def __init__(self, samples=None):
        self.samples = [dict(s) for s in (samples or []) if s.get("temp_c") is not None]

    def add(self, temp_c: Optional[float], pos: float, source: str, t: float) -> None:
        if temp_c is None:
            return
        self.samples.append({"t": round(t, 1), "temp_c": round(float(temp_c), 2), "pos": round(float(pos), 4),
                             "source": source})
        self.samples = self.samples[-self.MAX_SAMPLES:]

    def fit(self) -> Dict[str, Any]:
        temps = np.array([s["temp_c"] for s in self.samples], np.float64)
        pos = np.array([s["pos"] for s in self.samples], np.float64)
        span = float(temps.max() - temps.min()) if len(temps) else 0.0
        out = {"n": len(temps), "span_c": round(span, 2), "slope": 0.0, "learned": False}
        if len(temps) >= self.MIN_SAMPLES and span >= self.MIN_SPAN_C:
            slope, _ = np.polyfit(temps, pos, 1)
            resid = pos - np.polyval(np.polyfit(temps, pos, 1), temps)
            out.update({
                "slope": round(float(np.clip(slope, -self.MAX_SLOPE, self.MAX_SLOPE)), 5),
                "learned": True,
                "rms_d": round(float(np.sqrt(np.mean(resid ** 2))), 4),
            })
        return out

    def target(self, anchor_pos: float, anchor_temp: Optional[float], temp_c: Optional[float]) -> float:
        if anchor_temp is None or temp_c is None:
            return anchor_pos
        return anchor_pos + self.fit()["slope"] * (float(temp_c) - float(anchor_temp))
