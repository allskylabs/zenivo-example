"""
Daytime moving objects: birds, insects, aircraft (and the rain that confuses them).

By day the sky is bright and the exposure is a few ms, so a flying thing is a small dark (sometimes bright) dot
that differs from a slowly adapting background. The tracker works on a ~640 px gray copy of each new frame:

  1. foreground = |frame - background| inside the sky mask (a running average; skipped and re-seeded when a big
     part of the frame changed: cloud edge, exposure step, shadow);
  2. small blobs are linked from frame to frame (constant-velocity prediction) into tracks;
  3. a finished track that really moved is classified from size, speed, straightness and size flicker
     (wing beats) as bird / insect / plane / object.

Rain is recognised from the drops on the dome: many small round contrasty blobs that appear and then stay exactly
where they are. While it rains, tracks are not reported (drops and streaks would be false objects).

The frame rate by day is set by `min_capture_interval_s` (2 s by default): that is enough for aircraft and large,
slow birds; insects and fast birds need ~0.4 s or less.
"""

import math
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from src.pipeline.star_boost import sky_polygon_mask


class DayObjectTracker:
    def __init__(self, work_width: int = 640):
        self.work_width = int(work_width)
        self.reset()

    # tuning ---------------------------------------------------------
    MIN_BLOB_AREA = 2            # px at work size
    MAX_BLOB_AREA = 700
    MIN_THRESHOLD_DN = 10.0
    NOISE_SIGMAS = 4.0
    MAX_CHANGED_FRAC = 0.04      # more of the sky than this changed: not objects (cloud, exposure, shadow)
    MAX_FRAME_GAP_S = 6.0        # longer gaps break tracks and reseed the background
    BG_TAU_S = 20.0              # background adaptation time constant
    MAX_CANDIDATES = 60
    MIN_POINTS = 4
    MIN_NET_PX = 15.0
    MIN_CONTRAST_DN = 18.0       # median peak difference from the background along the track
    MIN_SPEED_FRAC = 0.010       # frame widths per second (cloud drift is below this, aircraft / birds above)
    MAX_MISSES = 2
    # rain (drops on the dome)
    DROP_MIN_AREA, DROP_MAX_AREA = 8, 150
    DROP_MIN_AGE_S = 8.0         # a sharp round blob that stays this long where it is counts as stuck to the dome
    DROP_MIN_CONTRAST_DN = 12.0
    DROP_MIN_ROUND = 0.6         # blob area / bounding-box area (a round drop is ~0.78, cloud texture is ragged)
    DROP_NEW_WINDOW_S = 180.0    # drops first seen within this long ago are "new" (dirt on the dome is older)
    RAIN_MIN_NEW_DROPS = 10
    RAIN_HOLD_S = 600.0

    def reset(self) -> None:
        self._bg: Optional[np.ndarray] = None
        self._prev_ts = 0.0
        self._tracks: List[Dict[str, Any]] = []
        self._drops: List[Dict[str, Any]] = []
        self._rain_until = 0.0
        self._mask_key: Any = None
        self._mask: Optional[np.ndarray] = None
        self.frames = 0
        self.skipped = 0
        self.new_drops = 0
        self.stats: Dict[str, Any] = {}

    @property
    def raining(self) -> bool:
        return self._prev_ts < self._rain_until

    def status(self) -> Dict[str, Any]:
        return {"frames": self.frames, "skipped_changed": self.skipped, "tracks_open": len(self._tracks),
                "raining": self.raining, "new_drops": self.new_drops, **self.stats}

    # ------------------------------------------------------------------
    def _work(self, frame: np.ndarray) -> Tuple[np.ndarray, float]:
        h, w = frame.shape[:2]
        scale = min(1.0, self.work_width / float(w))
        small = cv2.resize(frame, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA) \
            if scale < 1.0 else frame
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY) if small.ndim == 3 else small
        return gray, scale

    def _sky(self, shape: Tuple[int, int], polygons: Optional[dict]) -> np.ndarray:
        key = (shape, repr(polygons))
        if key != self._mask_key:
            m = sky_polygon_mask(shape, polygons)
            m = np.full(shape, 1, np.uint8) if m is None else m.astype(np.uint8)
            m = cv2.erode(m, np.ones((13, 13), np.uint8))  # leaves swaying at the edge of the sky are not flying things
            b = max(2, shape[1] // 100)
            m[:b, :] = 0
            m[-b:, :] = 0
            m[:, :b] = 0
            m[:, -b:] = 0
            self._mask_key, self._mask = key, m
        return self._mask  # type: ignore[return-value]

    @staticmethod
    def _blobs(binary: np.ndarray, signed: np.ndarray, lo: int, hi: int) -> List[Dict[str, float]]:
        n, _, stats, cents = cv2.connectedComponentsWithStats(binary, connectivity=8)
        out = []
        for i in range(1, n):
            a = int(stats[i, cv2.CC_STAT_AREA])
            if lo <= a <= hi:
                x, y = float(cents[i][0]), float(cents[i][1])
                out.append({"x": x, "y": y, "area": float(a),
                            "pol": -1.0 if float(signed[int(round(y)), int(round(x))]) < 0 else 1.0,
                            "dn": abs(float(signed[int(round(y)), int(round(x))]))})
        return out

    def _round_blobs(self, binary: np.ndarray, signed: np.ndarray) -> List[Dict[str, float]]:
        n, _, stats, cents = cv2.connectedComponentsWithStats(binary, connectivity=8)
        out = []
        for i in range(1, n):
            a = int(stats[i, cv2.CC_STAT_AREA])
            bw, bh = int(stats[i, cv2.CC_STAT_WIDTH]), int(stats[i, cv2.CC_STAT_HEIGHT])
            if self.DROP_MIN_AREA <= a <= self.DROP_MAX_AREA and a >= self.DROP_MIN_ROUND * bw * bh \
                    and 0.5 <= bw / float(max(1, bh)) <= 2.0:
                out.append({"x": float(cents[i][0]), "y": float(cents[i][1]), "area": float(a)})
        return out

    # ------------------------------------------------------------------
    def process(self, frame: np.ndarray, ts: float, sky_polygons: Optional[dict] = None) -> List[Dict[str, Any]]:
        """Feed one new frame; returns finished tracks (dicts, positions in frame fractions)."""
        if frame is None or frame.size == 0:
            return []
        gray, scale = self._work(frame)
        h, w = gray.shape
        mask = self._sky(gray.shape, sky_polygons)
        self.frames += 1
        dt = ts - self._prev_ts if self._prev_ts else 0.0
        self._prev_ts = ts
        g = gray.astype(np.float32)

        if self._bg is None or self._bg.shape != g.shape or not (0.0 < dt <= self.MAX_FRAME_GAP_S):
            self._bg = g.copy()
            return self._flush(ts, w, h)

        diff = cv2.GaussianBlur(g - self._bg, (0, 0), 1.0)
        sky = mask > 0
        d_sky = diff[sky]
        noise = 1.4826 * float(np.median(np.abs(d_sky - np.median(d_sky)))) if d_sky.size else 0.0
        thr = max(self.MIN_THRESHOLD_DN, self.NOISE_SIGMAS * noise)
        fg = ((np.abs(diff) > thr) & sky).astype(np.uint8)
        changed = float(fg.sum()) / max(1.0, float(sky.sum()))

        # Drops stuck to the dome are tracked on every frame, also while the sky itself is changing
        self._update_drops(g, sky, ts)

        alpha = 1.0 - math.exp(-dt / self.BG_TAU_S)
        if changed > self.MAX_CHANGED_FRAC:
            self.skipped += 1
            self._bg = g.copy()  # something big changed: the old background is no reference any more
            return self._flush(ts, w, h)
        self._bg = (1.0 - alpha) * self._bg + alpha * g

        blobs = self._blobs(fg, diff, self.MIN_BLOB_AREA, self.MAX_BLOB_AREA)
        blobs.sort(key=lambda b: -b["dn"] * b["area"])
        blobs = blobs[: self.MAX_CANDIDATES]
        finished = self._link(blobs, ts, dt, w)
        if self.raining:
            return []
        return [self._describe(t, w, h) for t in finished if self._real(t)]

    # ------------------------------------------------------------------
    def _link(self, blobs: List[Dict[str, float]], ts: float, dt: float, w: int) -> List[Dict[str, Any]]:
        unused = list(range(len(blobs)))
        base_gate = 0.16 * w * max(0.5, min(dt, 3.0)) / 2.0
        for t in self._tracks:
            p = t["pts"]
            if len(p) >= 2:
                dts = max(1e-3, p[-1][0] - p[-2][0])
                vx, vy = (p[-1][1] - p[-2][1]) / dts, (p[-1][2] - p[-2][2]) / dts
                px, py = p[-1][1] + vx * dt, p[-1][2] + vy * dt
                gate = 8.0 + 0.5 * math.hypot(vx, vy) * dt
            else:
                px, py, gate = p[-1][1], p[-1][2], base_gate
            best, bd = None, 1e9
            for i in unused:
                d = math.hypot(blobs[i]["x"] - px, blobs[i]["y"] - py)
                if d < gate and d < bd:
                    best, bd = i, d
            if best is None:
                t["misses"] += 1
            else:
                b = blobs[best]
                unused.remove(best)
                t["misses"] = 0
                p.append((ts, b["x"], b["y"], b["area"], b["pol"], b["dn"]))
        done = [t for t in self._tracks if t["misses"] > self.MAX_MISSES]
        self._tracks = [t for t in self._tracks if t["misses"] <= self.MAX_MISSES]
        for i in unused:
            b = blobs[i]
            self._tracks.append({"pts": [(ts, b["x"], b["y"], b["area"], b["pol"], b["dn"])], "misses": 0})
        return done

    def _flush(self, ts: float, w: int, h: int) -> List[Dict[str, Any]]:
        done, self._tracks = self._tracks, []
        if self.raining:
            return []
        return [self._describe(t, w, h) for t in done if self._real(t)]

    def _real(self, t: Dict[str, Any]) -> bool:
        p = t["pts"]
        if len(p) < self.MIN_POINTS:
            return False
        net = math.hypot(p[-1][1] - p[0][1], p[-1][2] - p[0][2])
        dur = max(1e-3, p[-1][0] - p[0][0])
        # Slow drift (a cloud wisp creeping along) is not a flying thing
        contrast = float(np.median([q[5] for q in p]))  # faint, wide patches are cloud brightness, not an object
        return net >= self.MIN_NET_PX and net / dur >= self.MIN_SPEED_FRAC * self.work_width \
            and contrast >= self.MIN_CONTRAST_DN

    def _describe(self, t: Dict[str, Any], w: int, h: int) -> Dict[str, Any]:
        p = t["pts"]
        dur = max(1e-3, p[-1][0] - p[0][0])
        path = sum(math.hypot(p[i][1] - p[i - 1][1], p[i][2] - p[i - 1][2]) for i in range(1, len(p)))
        net = math.hypot(p[-1][1] - p[0][1], p[-1][2] - p[0][2])
        straight = net / path if path > 0 else 0.0
        areas = np.array([q[3] for q in p])
        area = float(np.median(areas))
        cv_area = float(areas.std() / max(1.0, areas.mean()))
        speed = (path / dur) / float(w)  # frame widths per second
        dark = sum(1 for q in p if q[4] < 0) / len(p)
        # Classification: an aircraft flies a dead-straight line at constant speed for a good while; a bird is
        # bigger, wobbles (wing beats change its size) and turns; an insect is a tiny dot that jitters about
        if straight >= 0.97 and speed <= 0.12 and dur >= 6.0 and cv_area < 0.35 and area <= 45:
            cls = "plane"
        elif area <= 20 and (straight < 0.85 or speed > 0.05):
            cls = "insect"
        elif 20 < area <= 300 and (cv_area >= 0.2 or straight < 0.97 or speed > 0.12):
            cls = "bird"
        else:
            cls = "object"
        step = max(1, len(p) // 30)
        pts = [[round(q[0] - p[0][0], 2), round(q[1] / w, 4), round(q[2] / h, 4)] for q in p[::step]]
        return {"t": round(p[0][0], 1), "t1": round(p[-1][0], 1), "cls": cls, "n": len(p), "area": round(area, 1),
                "speed": round(speed, 4), "straight": round(straight, 3), "dark": round(dark, 2),
                "area_cv": round(cv_area, 2), "pts": pts}

    # ------------------------------------------------------------------
    def _update_drops(self, g: np.ndarray, sky: np.ndarray, ts: float) -> None:
        """Small round contrasty blobs (difference of Gaussians) that stay put: drops on the dome."""
        dog = cv2.GaussianBlur(g, (0, 0), 1.0) - cv2.GaussianBlur(g, (0, 0), 3.5)
        vals = dog[sky]
        if vals.size == 0:
            return
        noise = 1.4826 * float(np.median(np.abs(vals - np.median(vals))))
        thr = max(self.DROP_MIN_CONTRAST_DN, 6.0 * noise)
        binary = ((np.abs(dog) > thr) & sky).astype(np.uint8)
        cur = self._round_blobs(binary, dog)[:400]
        prev = self._drops
        used = set()
        nxt: List[Dict[str, Any]] = []
        for b in cur:
            m = None
            for i, d in enumerate(prev):
                if i not in used and abs(d["x"] - b["x"]) <= 2.0 and abs(d["y"] - b["y"]) <= 2.0:
                    m = i
                    break
            if m is None:
                nxt.append({"x": b["x"], "y": b["y"], "age": 1, "first": ts})
            else:
                used.add(m)
                d = prev[m]
                nxt.append({"x": b["x"], "y": b["y"], "age": d["age"] + 1, "first": d["first"]})
        self._drops = nxt
        new = sum(1 for d in nxt if self.DROP_MIN_AGE_S <= ts - d["first"] <= self.DROP_NEW_WINDOW_S)
        self.new_drops = new
        if new >= self.RAIN_MIN_NEW_DROPS:
            self._rain_until = ts + self.RAIN_HOLD_S
