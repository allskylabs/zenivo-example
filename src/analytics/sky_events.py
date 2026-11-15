"""
Lightweight sky event detection: meteors, aircraft, satellites and flashes.

Each new frame is reduced to a ~640 px grayscale copy and compared with the previous one (dilated, so
star drift and twinkle do not show up). Bright straight streaks are found with a probabilistic Hough
transform on the few changed pixels, then classified across frames:

- meteor: a solid streak in one frame only (confirmed when the next frame shows no continuation)
- plane: a dashed streak (strobe / navigation lights) or one that continues along the same line in the
  next frame; with short exposures (bright city sky) an aircraft is only a dot per frame, so small moving
  dots are also tracked: 4+ positions on a straight line at a steady speed make a plane (or satellite)
- satellite: a solid, faint streak that continues along the same line
- flash: the whole sky brightens at an unchanged exposure (lightning, headlights on the dome)

Frames whose exposure or gain changed, or where too much of the sky changed (moving clouds), are skipped.

Broken cloud lit by the city drifts across as many bright bits that look like slow aircraft dots. Wind
moves them all the same way at the same speed, while aircraft each fly their own way: a dot track whose
velocity matches two or more other tracks of the last minutes is cloud, and while such a drift is under
way single-frame streaks (cloud edges lighting up) are not called meteors.
"""

import math
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

import cv2
import numpy as np

from src.pipeline.star_boost import sky_polygon_mask

EVENT_TYPES = ("meteor", "plane", "satellite", "clouds", "flash")


def _line_geom(x1: float, y1: float, x2: float, y2: float) -> Tuple[float, float, float, float]:
    """(angle in [0, 180), length, unit dx, unit dy)."""
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy) or 1.0
    return math.degrees(math.atan2(dy, dx)) % 180.0, length, dx / length, dy / length


def _angle_diff(a: float, b: float) -> float:
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d)


def _perp_dist(px: float, py: float, line: Tuple[float, float, float, float]) -> float:
    x1, y1, x2, y2 = line
    _, _, ux, uy = _line_geom(x1, y1, x2, y2)
    return abs((px - x1) * uy - (py - y1) * ux)


def _project(line: Tuple[float, float, float, float], ref: Tuple[float, float, float, float]) -> Tuple[float, float]:
    """Interval of `line`'s endpoints projected onto `ref`'s direction."""
    x1, y1, x2, y2 = ref
    _, _, ux, uy = _line_geom(x1, y1, x2, y2)
    a = (line[0] - x1) * ux + (line[1] - y1) * uy
    b = (line[2] - x1) * ux + (line[3] - y1) * uy
    return min(a, b), max(a, b)


def _collinear(a: Tuple[float, ...], b: Tuple[float, ...], max_angle: float, max_dist: float) -> bool:
    if _angle_diff(_line_geom(*a)[0], _line_geom(*b)[0]) > max_angle:
        return False
    return (_perp_dist((a[0] + a[2]) / 2, (a[1] + a[3]) / 2, b) <= max_dist
            and _perp_dist((b[0] + b[2]) / 2, (b[1] + b[3]) / 2, a) <= max_dist)


def _seg_dist_pt(px: float, py: float, seg: Tuple[float, ...]) -> float:
    """Distance from a point to a segment."""
    x1, y1, x2, y2 = seg
    dx, dy = x2 - x1, y2 - y1
    k = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / max(1e-9, dx * dx + dy * dy)))
    return math.hypot(px - (x1 + k * dx), py - (y1 + k * dy))


def _overlap_fraction(a: Tuple[float, ...], b: Tuple[float, ...]) -> float:
    """Overlap of two collinear segments along their line, as a fraction of the shorter one."""
    a0, a1 = _project(a, b)
    b0, b1 = _project(b, b)
    inter = min(a1, b1) - max(a0, b0)
    return max(0.0, inter) / max(1.0, min(a1 - a0, b1 - b0))


def _merge_segments(segs: List[Tuple[int, int, int, int]], max_gap: float) -> List[Tuple[float, float, float, float]]:
    """Merge collinear Hough segments (dashed aircraft trails come out in pieces) into whole lines."""
    segs = sorted(segs, key=lambda s: -math.hypot(s[2] - s[0], s[3] - s[1]))
    lines: List[List[float]] = []
    for s in segs:
        merged = False
        for ln in lines:
            ref = tuple(ln)
            if not _collinear(s, ref, 6.0, 4.0):
                continue
            r0, r1 = _project(ref, ref)
            s0, s1 = _project(s, ref)
            if s0 - r1 > max_gap or r0 - s1 > max_gap:
                continue
            lo, hi = min(r0, s0), max(r1, s1)
            _, _, ux, uy = _line_geom(*ref)
            ln[:] = [ref[0] + lo * ux, ref[1] + lo * uy, ref[0] + hi * ux, ref[1] + hi * uy]
            merged = True
            break
        if not merged:
            lines.append([float(v) for v in s])
    return [tuple(ln) for ln in lines]


class SkyEventDetector:
    """Stateful two-frame streak detector. Feed every new frame to process(); it returns finished events."""

    def __init__(
        self,
        work_width: int = 640,
        min_length_frac: float = 0.035,   # shortest streak, as a fraction of the frame width (~22 px at 640)
        min_threshold_dn: float = 8.0,    # floor of the brightening threshold (8-bit DN)
        noise_sigmas: float = 4.0,        # threshold = max(floor, noise_sigmas * robust noise of the difference)
        max_changed_frac: float = 0.02,   # skip frames where more of the sky than this changed (clouds, dawn)
        max_gap_s: float = 120.0,         # frames further apart than this are not compared
        flash_ratio: float = 1.6,         # mean sky brightness jump that counts as a flash
    ):
        self.work_width = int(work_width)
        self.min_length_frac = min_length_frac
        self.min_threshold_dn = min_threshold_dn
        self.noise_sigmas = noise_sigmas
        self.max_changed_frac = max_changed_frac
        self.max_gap_s = max_gap_s
        self.flash_ratio = flash_ratio
        self._meteor_spots: Deque[Tuple[float, float, float]] = deque(maxlen=500)  # survives reset()
        self._track_id = 0
        self.frames_seen = 0      # frames given to process() since the last reset
        self.frames_compared = 0  # of those, compared with the previous frame
        self.reset()

    METEOR_MIN_ELONGATION = 5.0
    OUTSIDE_SENSITIVITY_DIV = 4     # brightening outside the sky mask is divided by this before every test
    BRIGHT_STREAK_PEAK_DN = 90.0    # streak peak (work-size DN above the previous frame) ...
    BRIGHT_STREAK_CONTRAST = 2.5    # ... and line mean at least this many times the mean beside it: skip the "thin" test
    CLOUDY_SPECKS = 8.0          # running mean of brightening specks per frame above which the sky counts as cloudy
    REPEAT_RADIUS_FRAC = 0.03   # ~20 px at 640: a "meteor" again within this of an earlier one is a glint
    REPEAT_WINDOW_S = 3 * 3600.0
    # Common drift (wind-blown cloud): velocities of dot tracks within this window, compared pairwise
    DRIFT_WINDOW_S = 600.0
    DRIFT_MAX_ANGLE = 25.0      # degrees between the two directions
    DRIFT_MAX_SPEED_DIFF = 0.4  # relative to the faster one
    DRIFT_MIN_OTHERS = 2        # this many other tracks moving alike => cloud
    DRIFT_ACTIVE_S = 300.0      # a drift seen this recently makes the sky count as cloudy

    def _cloudy(self, ts: Optional[float] = None) -> bool:
        """Broken cloud drifting over: many specks brighten every frame (clear sky: ~1 per frame, cloud: 25),
        or several dot tracks just moved alike. Single-frame streaks are then mostly cloud edges lighting up,
        and a meteor would be hidden anyway."""
        if self._speck_ema >= self.CLOUDY_SPECKS:
            return True
        if ts is None or not self._drift:
            return False
        recent = [d for d in self._drift if ts - d[0] <= self.DRIFT_ACTIVE_S]
        return any(self._drift_matches(d[1], d[2], ts, d[3], recent) >= self.DRIFT_MIN_OTHERS for d in recent)

    @staticmethod
    def _velocity(pts: List[Tuple[Any, ...]]) -> Tuple[float, float]:
        dt = max(1e-3, pts[-1][2] - pts[0][2])
        return (pts[-1][0] - pts[0][0]) / dt, (pts[-1][1] - pts[0][1]) / dt

    def _drift_matches(self, vx: float, vy: float, ts: float, tid: int,
                       entries: Optional[List[Tuple[float, float, float, int]]] = None) -> int:
        """How many other tracks of the drift window moved like (vx, vy) (work px/s)."""
        s1 = math.hypot(vx, vy)
        if s1 < 1e-6:
            return 0
        others = set()
        for t0, ux, uy, other in (self._drift if entries is None else entries):
            if other == tid or other in others or abs(ts - t0) > self.DRIFT_WINDOW_S:
                continue
            s2 = math.hypot(ux, uy)
            if s2 < 1e-6 or abs(s1 - s2) > self.DRIFT_MAX_SPEED_DIFF * max(s1, s2):
                continue
            cos = (vx * ux + vy * uy) / (s1 * s2)
            if math.degrees(math.acos(max(-1.0, min(1.0, cos)))) <= self.DRIFT_MAX_ANGLE:
                others.add(other)
        return len(others)

    def _new_dot_track(self, pts: List[Tuple[Any, ...]]) -> Dict[str, Any]:
        self._track_id += 1
        return {"pts": pts, "misses": 0, "blinks": 0, "id": self._track_id}

    def _meteor_ok(self, p: Dict[str, Any]) -> bool:
        """Final checks before a single-frame streak is called a meteor: a sharp line, and not at a spot
        where single-frame streaks already appeared (lens smudges and raindrops glint again and again)."""
        ev = p["event"]
        if self._cloudy(float(ev["ts"])):
            self.point_stats["meteors_rejected_cloudy_sky"] = self.point_stats.get("meteors_rejected_cloudy_sky", 0) + 1
            p["reject"] = "cloudy"
            return False
        ln = p["line"]
        w = self._mask.shape[1] if self._mask is not None else self.work_width
        mx, my, ts = (ln[0] + ln[2]) / 2, (ln[1] + ln[3]) / 2, float(ev["ts"])
        spots = self._meteor_spots
        while spots and ts - spots[0][0] > self.REPEAT_WINDOW_S:
            spots.popleft()
        r = self.REPEAT_RADIUS_FRAC * w
        repeat = any(math.hypot(mx - x, my - y) <= r for _, x, y in spots)
        spots.append((ts, mx, my))
        # On the path of an aircraft being tracked as moving dots: that aircraft, not a meteor
        on_plane = any(len(t["pts"]) >= 2 and any(_seg_dist_pt(q[0], q[1], ln) <= r for q in t["pts"][-3:])
                       for t in self._points)
        if on_plane:
            self.point_stats["meteors_rejected_plane"] = self.point_stats.get("meteors_rejected_plane", 0) + 1
            return False
        if repeat:
            self.point_stats["meteors_rejected_repeat"] = self.point_stats.get("meteors_rejected_repeat", 0) + 1
            return False
        if p.get("elong", 99.0) < self.METEOR_MIN_ELONGATION:
            self.point_stats["meteors_rejected_blob"] = self.point_stats.get("meteors_rejected_blob", 0) + 1
            p["reject"] = "blob"
            return False
        return True

    def _pending_event(self, p: Dict[str, Any], enabled: set) -> Optional[Dict[str, Any]]:
        """Result of a single-frame streak that was not matched again: a meteor if it passes the checks.
        A very bright but fuzzy one (aircraft strobe lighting a cloud deck: a halo around the dashes) is no
        meteor, which is sharp, so it becomes an aircraft event."""
        if "meteor" in enabled and self._meteor_ok(p):
            return p["event"]
        ev = p["event"]
        if "plane" in enabled and int(ev.get("peak_dn", 0)) >= self.BRIGHT_STREAK_PEAK_DN:
            fuzzy = p.get("elong", 99.0) < self.METEOR_MIN_ELONGATION
            # Under cloud that only drifted along (not specks brightening everywhere) a fuzzy bright streak is still
            # an aircraft lighting the deck; sharp ones stay rejected there (cloud edge / meteor hidden)
            if p.get("reject") == "blob" or (p.get("reject") == "cloudy" and fuzzy and self._speck_ema < self.CLOUDY_SPECKS):
                ev["type"] = "plane"
                return ev
        return None

    def reset(self) -> None:
        self.frames_seen = self.frames_compared = 0
        self.point_stats: Dict[str, Any] = {"frames": 0, "skipped": 0, "candidates": 0, "max_candidates": 0,
                                            "last_thr": None, "tracks_rejected_static": 0}
        self._prev: Optional[np.ndarray] = None
        self._prev_ts = 0.0
        self._prev_key: Tuple[Any, ...] = ()
        self._prev_exp = 0.0
        self._prev_gain = 0.0
        self._last_exposure_s = 0.0
        self._prev_mean = 0.0
        self._pending: List[Dict[str, Any]] = []  # single-frame streaks waiting for the next frame
        self._tracks: List[Dict[str, Any]] = []   # moving objects seen in the previous frame
        self._points: List[Dict[str, Any]] = []   # moving dots (short exposures): seeds and tracks
        self._recent: Deque[Tuple[float, np.ndarray]] = deque(maxlen=16)  # work-size frames
        self._speck_ema = 0.0
        self._drift: Deque[Tuple[float, float, float, int]] = deque(maxlen=400)  # (ts, vx, vy, track id)
        self._mask_key: Any = None
        self._mask: Optional[np.ndarray] = None

    # ------------------------------------------------------------------
    def _reduce(self, frame: np.ndarray) -> Tuple[np.ndarray, np.ndarray, float]:
        h, w = frame.shape[:2]
        scale = min(1.0, self.work_width / float(w))
        small = cv2.resize(frame, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA) \
            if scale < 1.0 else frame
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY) if small.ndim == 3 else small
        if small.ndim == 2:
            small = cv2.cvtColor(small, cv2.COLOR_GRAY2BGR)
        return small, gray, scale

    def _work_mask(self, shape: Tuple[int, int], sky_polygons: Optional[dict]) -> np.ndarray:
        key = (shape, repr(sky_polygons))
        if key != self._mask_key:
            h, w = shape
            m = sky_polygon_mask(shape, sky_polygons)
            m = np.full((h, w), 255, np.uint8) if m is None else m * 255
            valid = np.full((h, w), 255, np.uint8)
            b = max(2, w // 100)  # frame edge: vignetting, sensor edge artefacts
            for a in (valid, m):
                a[:b, :] = 0
                a[-b:, :] = 0
                a[:, :b] = 0
                a[:, -b:] = 0
            # Houses / trees: still watched, but a streak there has to be several times brighter (wind-blown leaves)
            self._outside = ((valid > 0) & (m == 0))
            self._valid = valid
            self._mask_key, self._mask = key, m
        return self._mask

    def _sky_wide(self, gray: np.ndarray, prev: np.ndarray, mask: np.ndarray) -> bool:
        """True when most of the sky brightened at once (a flash), not just a passing cloud bank."""
        cells = (16, 12)
        a = cv2.resize(gray, cells, interpolation=cv2.INTER_AREA).astype(np.float32)
        b = cv2.resize(prev, cells, interpolation=cv2.INTER_AREA).astype(np.float32)
        m = cv2.resize(mask, cells, interpolation=cv2.INTER_AREA) > 127
        if not m.any():
            return False
        return float((a[m] > b[m] * 1.3 + 5.0).mean()) >= 0.7

    def _flush(self, enabled: set, ts: float) -> List[Dict[str, Any]]:
        """Finish everything that has not continued into the current frame."""
        out = [ev for ev in (self._pending_event(p, enabled) for p in self._pending) if ev]
        for t in self._tracks:
            ev = self._finish_track(t, enabled)
            if ev:
                out.append(ev)
        for t in self._points:
            ev = self._finish_points(t, enabled)
            if ev:
                out.append(ev)
        self._pending, self._tracks, self._points = [], [], []
        return out

    def _finish_track(self, track: Dict[str, Any], enabled: set) -> Optional[Dict[str, Any]]:
        ev = track["event"]
        if int(ev.get("frames", 1)) <= 1 and self._cloudy(float(ev["ts"])) and int(ev.get("peak_dn", 0)) < 90:
            self.point_stats["streaks_rejected_cloudy_sky"] = self.point_stats.get("streaks_rejected_cloudy_sky", 0) + 1
            return None
        if track["dashed"] or ev["peak_dn"] >= 60:
            ev["type"] = "plane"
        else:
            ev["type"] = "satellite"
        return ev if ev["type"] in enabled else None

    # ------------------------------------------------------------------
    def process(
        self,
        frame: np.ndarray,
        enabled: set,
        ts: Optional[float] = None,
        exposure_s: float = 0.0,
        gain_db: float = 0.0,
        sky_polygons: Optional[dict] = None,
    ) -> List[Dict[str, Any]]:
        """Analyse one frame (sky_polygons: the station sky mask, {"sky": [...], "obstruction": [...]}
        in frame fractions). Returns finished events (dicts); image crops are attached under
        "_crop" (full-resolution BGR) and "_overview" (work-size BGR with the streak drawn)."""
        if frame is None or frame.size == 0 or not enabled:
            return []
        ts = time.time() if ts is None else float(ts)
        self.frames_seen += 1
        self._last_exposure_s = float(exposure_s)
        small, gray, scale = self._reduce(frame)
        mask = self._work_mask(gray.shape, sky_polygons)
        mean_now = float(cv2.mean(gray, mask)[0])

        key = (gray.shape, round(math.log(max(exposure_s, 1e-6)), 1), round(gain_db))
        prev, prev_mean = self._prev, self._prev_mean
        prev_exp, prev_gain = self._prev_exp, self._prev_gain
        same_shape = prev is not None and gray.shape == prev.shape
        dt_ok = 0 < ts - self._prev_ts <= self.max_gap_s
        exp_ok = (exposure_s <= 0 and prev_exp <= 0) or (
            exposure_s > 0 and prev_exp > 0 and abs(math.log(max(exposure_s, 1e-6) / max(prev_exp, 1e-6))) <= 0.30
        )
        gain_ok = abs(gain_db - prev_gain) <= 3.0
        comparable = same_shape and dt_ok and exp_ok and gain_ok
        self._prev, self._prev_ts, self._prev_key, self._prev_mean = gray, ts, key, mean_now
        self._prev_exp, self._prev_gain = float(exposure_s), float(gain_db)

        if not comparable:
            if not same_shape or not dt_ok:
                return self._flush(enabled, ts)
            return []
        self.frames_compared += 1

        events: List[Dict[str, Any]] = []
        if mean_now > prev_mean * self.flash_ratio + 8.0 and self._sky_wide(gray, prev, mask):
            events.extend(self._flush(enabled, ts))
            if "flash" in enabled:
                events.append({
                    "type": "flash", "ts": ts, "start_ts": ts, "frames": 1,
                    "sky_mean_before": round(prev_mean, 1), "sky_mean": round(mean_now, 1),
                    "_overview": small.copy(),
                })
            return events

        streak_types = {"meteor", "plane", "satellite"} & enabled
        if not streak_types:
            return events

        # Brightening only, against the dilated previous frame: drifting / twinkling stars cancel out
        diff = cv2.subtract(gray, cv2.dilate(prev, np.ones((3, 3), np.uint8)))
        diff = cv2.bitwise_and(diff, self._valid)
        if self._outside.any():
            # Outside the sky mask the brightening counts 1/K as much: only a very bright streak gets through
            diff[self._outside] = (diff[self._outside] // self.OUTSIDE_SENSITIVITY_DIV).astype(np.uint8)
        d = gray[::4, ::4].astype(np.int16) - prev[::4, ::4].astype(np.int16)
        mad = float(np.median(np.abs(d - np.median(d))))
        thr = max(self.min_threshold_dn, self.noise_sigmas * 1.4826 * mad)
        _, binary = cv2.threshold(diff, thr, 255, cv2.THRESH_BINARY)
        nz = cv2.countNonZero(binary)
        if nz > self.max_changed_frac * binary.size:
            return events + self._flush(enabled, ts)  # clouds / moonrise: nothing reliable this frame

        w = gray.shape[1]
        min_len = max(12, int(self.min_length_frac * w))
        lines: List[Tuple[float, float, float, float]] = []
        raw_binary = binary
        if nz >= min_len // 2:
            # Drop 1-2 px specks (noise, hot pixels) so they cannot line up by chance
            n, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
            if n > 1:
                keep = (stats[:, cv2.CC_STAT_AREA] >= 3).astype(np.uint8) * 255
                keep[0] = 0
                binary = keep[labels]
            # A gap of ~1.5 x min_len bridges aircraft strobe dots
            segs = cv2.HoughLinesP(binary, 1, np.pi / 180, threshold=max(10, min_len // 2),
                                   minLineLength=min_len, maxLineGap=max(6, int(min_len * 1.5)))
            if segs is not None:
                lines = _merge_segments([tuple(int(v) for v in s) for s in np.asarray(segs).reshape(-1, 4)[:80]], max_gap=w * 0.12)

        fat = cv2.dilate(binary, np.ones((3, 3), np.uint8))
        found: List[Dict[str, Any]] = []
        for ln in lines:
            m = self._measure(ln, fat, diff, thr, prev)
            if m is None or m["length"] < min_len:
                continue
            found.append({"line": ln, **m})

        # Match against what the previous frame left open
        next_pending: List[Dict[str, Any]] = []
        next_tracks: List[Dict[str, Any]] = []
        used_pending, used_tracks = set(), set()
        tol = max(4.0, w * 0.012)
        for f in found:
            ln = f["line"]
            match = None
            for i, t in enumerate(self._tracks):
                if i not in used_tracks and _collinear(ln, t["line"], 8.0, tol):
                    match = ("track", i)
                    break
            if match is None:
                for i, p in enumerate(self._pending):
                    if i not in used_pending and _collinear(ln, p["line"], 8.0, tol):
                        match = ("pending", i)
                        break
            if match is not None:
                kind, i = match
                prev_obj = self._tracks[i] if kind == "track" else self._pending[i]
                (used_tracks if kind == "track" else used_pending).add(i)
                if _overlap_fraction(ln, prev_obj["line"]) >= 0.6:
                    continue  # same place in both frames: a static artefact, not a moving object
                ev = prev_obj["event"]
                ev["frames"] = int(ev.get("frames", 1)) + 1
                ev["end_ts"] = ts
                ev["peak_dn"] = max(ev["peak_dn"], f["peak_dn"])
                ev["length_px"] = round(ev["length_px"] + f["length"] / scale, 1)
                next_tracks.append({"line": ln, "event": ev, "dashed": prev_obj.get("dashed", False) or f["dashed"]})
                continue
            ev = self._new_event(f, ts, scale, frame, small)
            if f["dashed"]:
                next_tracks.append({"line": ln, "event": ev, "dashed": True})
            else:
                next_pending.append({"line": ln, "event": ev, "elong": f.get("elong", 99.0)})

        for i, p in enumerate(self._pending):
            if i not in used_pending:
                ev = self._pending_event(p, enabled)
                if ev:
                    events.append(ev)
        for i, t in enumerate(self._tracks):
            if i not in used_tracks:
                ev = self._finish_track(t, enabled)
                if ev:
                    events.append(ev)
        self._pending, self._tracks = next_pending, next_tracks
        if {"plane", "satellite"} & enabled:
            events += self._track_points(raw_binary, diff, thr, [f["line"] for f in found], ts, scale, small, enabled)
        return events

    # ------------------------------------------------------------------
    # Moving dots: with exposures far shorter than the frame interval an aircraft is a dot per frame
    # ------------------------------------------------------------------
    POINT_MIN_POINTS = 4       # positions needed before a dot track counts
    POINT_MAX_CANDIDATES = 40  # only the brightest specks are tracked
    POINT_MAX_BLOBS = 3000     # more changed blobs than this: hopelessly noisy frame, skip it
    # Aircraft dot vs drifting cloud (track medians at the 640 px work size; measured on real planes:
    # sharpness 22-63, area 4-8 px, background texture 1.6-3 DN; cloud bits mostly fail one of these)
    DOT_MIN_SHARPNESS = 15.0
    DOT_MAX_AREA = 10.0
    DOT_SHARP_ANY_SIZE = 30.0
    DOT_FAINT_PEAK = 28        # brightest dot below this (DN above the sky) ...
    DOT_FAINT_MIN_POINTS = 6   # ... needs at least this many positions
    DOT_MAX_BG_TEXTURE = 5.0
    # A star (Pleiades, Vega...) twinkles and drifts at the sidereal rate (~0.05 px/s at full resolution); an
    # aircraft or satellite crosses the dome at several px/s and is gone in minutes. Slower or longer than this
    # is a star, not a flyer.
    DOT_MIN_SPEED_PX_S = 0.5
    DOT_MAX_DURATION_S = 1200.0
    POINT_MAX_TRACKS = 120

    def _point_candidates(self, binary: np.ndarray, diff: np.ndarray, thr: float,
                          lines: List[Tuple[float, ...]]) -> Optional[List[Tuple[float, float, int, Tuple[float, int, float]]]]:
        """Small or strobe-bloomed bright blobs (x, y, peak, shape) in the brightening image, not on a streak."""
        n, labels, stats, cents = cv2.connectedComponentsWithStats(binary, connectivity=8)
        if n <= 1:
            return []
        if n > self.POINT_MAX_BLOBS:
            return None
        h, w = binary.shape
        base_max_area = max(12, int(binary.size * 3e-4))
        bright_max_area = max(80, int(binary.size * 3e-3))
        base_max_dim = max(8, w // 40)
        bright_max_dim = max(28, w // 14)
        min_peak = max(2.0 * thr, thr + 10.0)
        out: List[Tuple[float, float, int, Tuple[float, int, float]]] = []
        for i in range(1, n):
            x, y, bw, bh, area = stats[i]
            if area > bright_max_area or max(bw, bh) > bright_max_dim:
                continue
            peak = int(diff[y:y + bh, x:x + bw][labels[y:y + bh, x:x + bw] == i].max())
            if peak < min_peak:
                continue
            if area > base_max_area or max(bw, bh) > base_max_dim:
                # Allow larger optical bloom only for bright strobes/lights with compact aspect ratio
                if peak < 40 or max(bw, bh) > 4 * max(2, min(bw, bh)):
                    continue
            cx, cy = float(cents[i][0]), float(cents[i][1])
            if any(_perp_dist(cx, cy, ln) < 5 and min(_project((cx, cy, cx, cy), ln)) >= -5
                   and max(_project((cx, cy, cx, cy), ln)) <= _line_geom(*ln)[1] + 5 for ln in lines):
                continue  # part of a streak found above
            out.append((cx, cy, peak, self._dot_shape(cx, cy, peak, diff, thr, int(area), int(bw), int(bh))))
        if len(out) > self.POINT_MAX_CANDIDATES:
            out = sorted(out, key=lambda c: -c[2])[:self.POINT_MAX_CANDIDATES]
        return out

    def _dot_shape(self, cx: float, cy: float, peak: int, diff: np.ndarray, thr: float,
                   blob_area: int = 0, bw: int = 3, bh: int = 3) -> Tuple[float, int, float]:
        """(sharpness, area, background texture) of a dot. Uses an adaptive core radius for bloomed strobes so
        a bright aircraft strobe is compared against the sky around its halo rather than its own bloom."""
        h, w = diff.shape
        xi, yi = int(round(cx)), int(round(cy))
        r_in = max(1, min(7, int(round(math.sqrt(max(1, blob_area) / math.pi)))))
        r_out = r_in + 4
        win_ring = diff[max(0, yi - r_out):yi + r_out + 1, max(0, xi - r_out):xi + r_out + 1].astype(np.float32)
        ring = win_ring.copy()
        cy0, cx0 = yi - max(0, yi - r_out), xi - max(0, xi - r_out)
        ring[max(0, cy0 - r_in):cy0 + r_in + 1, max(0, cx0 - r_in):cx0 + r_in + 1] = np.nan
        sharp = float(peak) / max(1.0, float(np.nanmean(ring))) if np.isfinite(ring).any() else 0.0
        win9 = diff[max(0, yi - 4):yi + 5, max(0, xi - 4):xi + 5]
        area = int((win9 > thr).sum())
        bgstd = 0.0
        if self._prev is not None:
            bg_in = max(3, r_in + 1)
            bg_out = max(8, bg_in + 5)
            g = self._prev[max(0, yi - bg_out):yi + bg_out + 1, max(0, xi - bg_out):xi + bg_out + 1].astype(np.float32)
            gy, gx = yi - max(0, yi - bg_out), xi - max(0, xi - bg_out)
            g[max(0, gy - bg_in):gy + bg_in + 1, max(0, gx - bg_in):gx + bg_in + 1] = np.nan
            if np.isfinite(g).any():
                bgstd = float(np.nanstd(g))
        return round(sharp, 2), area, round(bgstd, 2)

    def _stack_track_frame(self, t: Dict[str, Any], small: np.ndarray, scale: float) -> None:
        if "stack" not in t:
            times = {p[2] for p in t["pts"]}
            t["stack"] = small.copy()
            for rts, rimg in self._recent:
                if rts in times and rimg.shape == small.shape:
                    np.maximum(t["stack"], rimg, out=t["stack"])
        else:
            np.maximum(t["stack"], small, out=t["stack"])
        t["scale"] = scale

    def _track_points(self, binary: np.ndarray, diff: np.ndarray, thr: float, lines: List[Tuple[float, ...]],
                      ts: float, scale: float, small: np.ndarray, enabled: set) -> List[Dict[str, Any]]:
        self._recent.append((ts, small))
        cands = self._point_candidates(binary, diff, thr, lines)
        n_specks = self.POINT_MAX_CANDIDATES if cands is None else len(cands)
        self._speck_ema = 0.9 * self._speck_ema + 0.1 * n_specks
        ps = self.point_stats
        ps["frames"] += 1
        ps["specks_per_frame"] = round(self._speck_ema, 1)
        if cands is None:
            ps["skipped"] += 1
        else:
            ps["candidates"] += len(cands)
            ps["max_candidates"] = max(ps["max_candidates"], len(cands))
        ps["last_thr"] = round(float(thr), 1)
        w = binary.shape[1]
        min_step = 1.5 if (0.0 < self._last_exposure_s <= 2.0) else 2.5
        max_step = w * 0.18 if (0.0 < self._last_exposure_s <= 2.0) else w * 0.15
        events: List[Dict[str, Any]] = []
        used: set = set()
        alive: List[Dict[str, Any]] = []
        # Established tracks (2+ points) first: predict along the line at the same speed
        for t in sorted(self._points, key=lambda t: -len(t["pts"])):
            if len(t["pts"]) < 2:
                continue
            match = None
            if cands is not None:
                (x0, y0, t0), (x1, y1, t1) = t["pts"][-2][:3], t["pts"][-1][:3]
                k = (ts - t1) / max(1e-3, t1 - t0)
                px, py = x1 + (x1 - x0) * k, y1 + (y1 - y0) * k
                r = max(4.0, 0.3 * math.hypot(px - x1, py - y1))
                best = r
                for j, (cx, cy, _, _) in enumerate(cands):
                    d = math.hypot(cx - px, cy - py)
                    if j not in used and d <= best:
                        match, best = j, d
            if match is not None:
                used.add(match)
                cx, cy, pk, shape = cands[match]
                t["pts"].append((cx, cy, ts, pk, shape))
                if t["misses"]:
                    t["blinks"] += 1
                t["misses"] = 0
                if len(t["pts"]) == 3:
                    # Every track that gets going (cloud bits too, even if rejected later) maps the drift
                    self._drift.append((ts, *self._velocity(t["pts"]), t["id"]))
                req_stack_pts = 2 if (0.0 < self._last_exposure_s <= 2.0 and max(p[3] for p in t["pts"]) >= 65) else self.POINT_MIN_POINTS
                if len(t["pts"]) >= req_stack_pts:
                    self._stack_track_frame(t, small, scale)
                alive.append(t)
                continue
            t["misses"] += 1
            max_m = 2 if len(t["pts"]) >= self.POINT_MIN_POINTS else 1
            if 0.0 < self._last_exposure_s <= 2.0 and len(t["pts"]) >= 2 and max(p[3] for p in t["pts"]) >= 65:
                max_m = 2
            if t["misses"] <= max_m:
                alive.append(t)
            else:
                ev = self._finish_points(t, enabled)
                if ev:
                    events.append(ev)
        # Seeds from the previous frame pair up with any dot a plausible step away
        if cands is not None:
            for s in self._points:
                if len(s["pts"]) != 1:
                    continue
                seed = s["pts"][0]
                sx, sy = seed[0], seed[1]
                paired = False
                k_seed = 1 + int(s.get("misses", 0))
                step_hi = min(w * 0.35, max_step * k_seed)
                min_cand_pk = 40 if (0.0 < self._last_exposure_s <= 2.0 and seed[3] >= 65) else 0
                for j, (cx, cy, pk, shape) in enumerate(cands):
                    if j not in used and pk >= min_cand_pk and min_step <= math.hypot(cx - sx, cy - sy) <= step_hi:
                        nt = self._new_dot_track([seed, (cx, cy, ts, pk, shape)])
                        if s.get("misses", 0) > 0:
                            nt["blinks"] = 1
                        self._stack_track_frame(nt, small, scale)
                        alive.append(nt)
                        paired = True
                if not paired and 0.0 < self._last_exposure_s <= 2.0 and seed[3] >= 65:
                    s["misses"] = int(s.get("misses", 0)) + 1
                    if s["misses"] <= 2:
                        alive.append(s)
                    elif 0.0 < self._last_exposure_s <= 1.0:
                        ev = self._finish_points(s, enabled)
                        if ev:
                            events.append(ev)
            for j, (cx, cy, pk, shape) in enumerate(cands):
                if j not in used:
                    nt = self._new_dot_track([(cx, cy, ts, pk, shape)])
                    nt["frame_cands"] = len(cands)
                    nt["stack"] = small.copy()
                    nt["scale"] = scale
                    alive.append(nt)
        alive.sort(key=lambda t: -len(t["pts"]))
        self._points = alive[:self.POINT_MAX_TRACKS]
        return events

    def _finish_points(self, t: Dict[str, Any], enabled: set) -> Optional[Dict[str, Any]]:
        pts = t["pts"]
        if not pts:
            return None
        # Single-frame intense strobe / short streak during short exposures (duty cycle < 15%)
        if len(pts) == 1:
            if not (0.0 < self._last_exposure_s <= 1.0) or "plane" not in enabled or "stack" not in t:
                return None
            small, scale = t["stack"], t["scale"]
            sh, sw = small.shape[:2]
            cx, cy, ts, pk, shape = pts[0]
            sharp, area, bgstd = shape
            if (pk < 120 or area < 12 or sharp < 18.0 or bgstd > 14.0
                    or int(t.get("frame_cands", 99)) > 3 or self._cloudy(ts)):
                return None
            spots = self._meteor_spots
            while spots and ts - spots[0][0] > self.REPEAT_WINDOW_S:
                spots.popleft()
            r = self.REPEAT_RADIUS_FRAC * sw
            if any(math.hypot(cx - x, cy - y) <= r for _, x, y in spots):
                return None
            spots.append((ts, cx, cy))
            pad = 20
            cx0, cx1 = max(0, int(round(cx)) - pad), min(sw, int(round(cx)) + pad)
            cy0, cy1 = max(0, int(round(cy)) - pad), min(sh, int(round(cy)) + pad)
            crop = small[cy0:cy1, cx0:cx1].copy()
            overview = small.copy()
            m = 10
            cv2.rectangle(overview, (int(round(cx)) - m, int(round(cy)) - m),
                          (int(round(cx)) + m, int(round(cy)) + m), (0, 200, 255), 1, cv2.LINE_AA)
            return {
                "type": "plane",
                "ts": ts,
                "start_ts": ts,
                "end_ts": ts,
                "frames": 1,
                "length_px": round(math.sqrt(area) / scale, 1),
                "peak_dn": int(pk),
                "mean_dn": float(pk),
                "dashes": 1,
                "moving_dot": True,
                "speed_px_s": 0.0,
                "line": [round(v, 4) for v in (cx / sw, cy / sh, cx / sw, cy / sh)],
                "bbox": [round(cx0 / scale), round(cy0 / scale), round((cx1 - cx0) / scale), round((cy1 - cy0) / scale)],
                "_crop": crop,
                "_overview": overview,
            }

        peaks = [p[3] for p in pts]
        shapes = [p[4] for p in pts if len(p) > 4]
        med_bgstd = float(np.median([q[2] for q in shapes])) if shapes else 0.0
        min_pts = self.POINT_MIN_POINTS
        if 0.0 < self._last_exposure_s <= 2.0:
            strobe_pts = sum(1 for p in pts if p[3] >= 65)
            if strobe_pts >= 2 and med_bgstd <= 3.5 and not self._cloudy(pts[-1][2]):
                min_pts = 2
            elif max(peaks) >= 40 and strobe_pts >= 2:
                min_pts = 3
        if len(pts) < min_pts or "stack" not in t:
            return None
        small, scale = t["stack"], t["scale"]
        # A real aircraft travels: the dots must get somewhere, roughly in a line (a glinting lens smudge
        # wobbles in place)
        net = math.hypot(pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1])
        path_len = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))
        min_net = 10.0 if len(pts) <= 3 else 12.0
        min_straight = 0.75 if len(pts) <= 3 else 0.6
        if net < min_net or net < min_straight * path_len:
            self.point_stats["tracks_rejected_static"] += 1
            return None
        # Sidereal drift: a twinkling star that wanders a few px over hours is not an aircraft
        dur = max(1e-3, pts[-1][2] - pts[0][2])
        if dur > self.DOT_MAX_DURATION_S or path_len / t["scale"] / dur < self.DOT_MIN_SPEED_PX_S:
            self.point_stats["tracks_rejected_star"] = self.point_stats.get("tracks_rejected_star", 0) + 1
            return None
        # Moving with the wind alongside other tracks: drifting cloud
        vx, vy = self._velocity(pts)
        is_short_strobe = 0.0 < self._last_exposure_s <= 2.0 and max(peaks) >= 65
        if not is_short_strobe and self._drift_matches(vx, vy, pts[-1][2], t.get("id", -1)) >= self.DRIFT_MIN_OTHERS:
            self.point_stats["tracks_rejected_drift"] = self.point_stats.get("tracks_rejected_drift", 0) + 1
            return None
        # Faint and short: with many specks about (cloud), four of them line up at a steady spacing by chance;
        # a real faint aircraft keeps going (real planes peak at +31 DN or more, faint ones last 10+ frames)
        if max(p[3] for p in pts) < self.DOT_FAINT_PEAK and len(pts) < self.DOT_FAINT_MIN_POINTS:
            self.point_stats["tracks_rejected_faint"] = self.point_stats.get("tracks_rejected_faint", 0) + 1
            return None
        # Drifting cloud: its bright bits move like a slow dot but are soft, large and on lumpy sky
        shapes = [p[4] for p in pts if len(p) > 4]
        if shapes:
            sharp = float(np.median([q[0] for q in shapes]))
            area = float(np.median([q[1] for q in shapes]))
            bgstd = float(np.median([q[2] for q in shapes]))
            soft_blob = area > self.DOT_MAX_AREA and sharp < self.DOT_SHARP_ANY_SIZE  # a bright close plane is big but sharp
            if sharp < self.DOT_MIN_SHARPNESS or soft_blob or bgstd > self.DOT_MAX_BG_TEXTURE:
                self.point_stats["tracks_rejected_cloud"] = self.point_stats.get("tracks_rejected_cloud", 0) + 1
                return None
        peaks = [p[3] for p in pts]
        blinking = t["blinks"] > 0 or max(peaks) > 2.5 * max(1, min(peaks))
        etype = "plane" if blinking or max(peaks) >= 60 else "satellite"
        if etype not in enabled:
            return None
        sh, sw = small.shape[:2]
        poly = np.array([[round(p[0]), round(p[1])] for p in pts], np.int32)
        xs, ys = poly[:, 0], poly[:, 1]
        pad = 16
        cx0, cx1 = max(0, int(xs.min()) - pad), min(sw, int(xs.max()) + pad)
        cy0, cy1 = max(0, int(ys.min()) - pad), min(sh, int(ys.max()) + pad)
        crop = small[cy0:cy1, cx0:cx1].copy()  # clean light trail, nothing drawn on it
        overview = small.copy()
        m = 10  # thin box around the path, clear of the dots
        cv2.rectangle(overview, (int(xs.min()) - m, int(ys.min()) - m), (int(xs.max()) + m, int(ys.max()) + m),
                      (0, 200, 255), 1, cv2.LINE_AA)
        path = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))
        x1, y1, x2, y2 = pts[0][0], pts[0][1], pts[-1][0], pts[-1][1]
        return {
            "type": etype,
            "ts": pts[-1][2],
            "start_ts": pts[0][2],
            "end_ts": pts[-1][2],
            "frames": len(pts),
            "length_px": round(path / scale, 1),
            "peak_dn": int(max(peaks)),
            "mean_dn": round(float(np.mean(peaks)), 1),
            "dashes": int(t["blinks"]),
            "moving_dot": True,
            "speed_px_s": round(path / scale / max(1e-3, pts[-1][2] - pts[0][2]), 1),
            "line": [round(v, 4) for v in (x1 / sw, y1 / sh, x2 / sw, y2 / sh)],
            "bbox": [round(cx0 / scale), round(cy0 / scale), round((cx1 - cx0) / scale), round((cy1 - cy0) / scale)],
            "_crop": crop,
            "_overview": overview,
        }

    def _measure(self, ln: Tuple[float, ...], fat: np.ndarray, diff: np.ndarray, thr: float,
                 prev: Optional[np.ndarray] = None) -> Optional[Dict[str, Any]]:
        """Sample along the line: fill fraction, dash count, thinness and brightness."""
        x1, y1, x2, y2 = ln
        _, length, ux, uy = _line_geom(x1, y1, x2, y2)
        n = max(8, int(length))
        t = np.linspace(0.0, length, n)
        h, w = fat.shape
        xs = np.clip(np.round(x1 + t * ux).astype(int), 0, w - 1)
        ys = np.clip(np.round(y1 + t * uy).astype(int), 0, h - 1)
        on = fat[ys, xs] > 0
        fill = float(on.mean())
        if fill < 0.15:
            return None
        # A streak is thin: 4 px either side of it should be mostly dark (cloud edges are not)
        side, side_dn, prev_side = 0.0, 0.0, []
        for off in (-4, 4):
            sx = np.clip(np.round(xs - off * uy).astype(int), 0, w - 1)
            sy = np.clip(np.round(ys + off * ux).astype(int), 0, h - 1)
            side = max(side, float((fat[sy, sx] > 0).mean()))
            side_dn = max(side_dn, float(diff[sy, sx].mean()))
            if prev is not None:
                prev_side.append(float(np.median(prev[sy, sx])))
        if side > 0.5 * fill:
            # A very bright, clearly sharper streak keeps its place even when the cloud / glow around it also
            # brightened (aircraft strobe lighting overcast sky: line ~55 DN, surroundings ~18 DN, side 0.7)
            v = diff[ys, xs][on]
            if not (v.size and float(v.max()) >= self.BRIGHT_STREAK_PEAK_DN
                    and float(v.mean()) >= self.BRIGHT_STREAK_CONTRAST * max(side_dn, 1.0)):
                return None
        # Dashes: on-runs separated by gaps of 3+ samples (aircraft strobes); short gaps are noise
        runs, gap, in_run = 0, 99, False
        for v in on:
            if v:
                if not in_run and gap >= 3:
                    runs += 1
                in_run, gap = True, 0
            else:
                in_run = False
                gap += 1
        if fill < 0.3 and runs < 4:
            return None  # sparse and not regularly dashed: chance alignment of specks
        vals = diff[ys, xs][on]
        # Strobe dots on a trail that never drops below the threshold between them (long exposure: faint solid
        # line with a bright dot per flash): count the regular peaks along the line instead of the gaps
        pulses = self._strobe_pulses(diff, xs, ys, vals, thr)
        # Drifting cloud texture lines up by chance but barely clears the threshold: demand a clear edge
        if not vals.size or float(vals.mean()) - side_dn < 1.5 * thr:
            return None
        # A meteor or aircraft appears on empty, smooth sky. Reject what was already there in the previous
        # frame and just glinted brighter (lens smudge, raindrop track, wire): brighter than its surroundings,
        # or lying on textured background whose two sides differ (smooth sky matches to ~1 DN at 4 px)
        if prev_side:
            prev_on = float(np.median(prev[ys, xs][on]))
            limit = max(6.0, 0.4 * float(vals.mean()))
            if prev_on - max(prev_side) > limit or abs(prev_side[0] - prev_side[1]) > limit:
                return None
            # Lying across cloud: the sky either side is lumpy (trimmed spread, so single stars do not count)
            band = []
            for off in (-8, -6, 6, 8):
                bx = np.clip(np.round(xs - off * uy).astype(int), 0, w - 1)
                by = np.clip(np.round(ys + off * ux).astype(int), 0, h - 1)
                a = prev[by, bx].astype(np.float32)
                k = min(31, len(a) | 1)
                trend = np.convolve(np.pad(a, k // 2, mode="edge"), np.ones(k, np.float32) / k, mode="valid")
                band.append(a - trend)  # without the slow sky gradient along the line
            band = np.sort(np.concatenate(band))
            cut = len(band) // 20  # trim 5% each end: the odd star, not broken cloud (many samples)
            texture = float(band[cut:len(band) - cut].std()) if len(band) > 2 * cut + 2 else 0.0
            if texture > self.DOT_MAX_BG_TEXTURE:
                self.point_stats["streaks_rejected_cloud"] = self.point_stats.get("streaks_rejected_cloud", 0) + 1
                return None
        return {
            "length": float(length),
            "fill": round(fill, 2),
            "dashes": max(runs, pulses),
            "dashed": (runs >= 4 and fill < 0.85) or pulses >= self.STROBE_MIN_PULSES,
            "peak_dn": int(vals.max()) if vals.size else 0,
            "mean_dn": round(float(vals.mean()), 1) if vals.size else 0.0,
            "elong": self._elongation(ln, diff, thr),
        }

    STROBE_MIN_PULSES = 5      # regular brightness peaks along a trail that make it an aircraft's strobe line
    STROBE_MIN_DIP = 0.25      # a peak must stand this fraction of the trail's mean brightness above the dips
    STROBE_MAX_SPACING_CV = 0.5
    STROBE_MIN_LINE_PX = 120   # shorter trails: pixel-stair ripple of a thin line looks like pulses

    def _strobe_pulses(self, diff: np.ndarray, xs: np.ndarray, ys: np.ndarray, vals: np.ndarray, thr: float) -> int:
        """Number of evenly spaced brightness peaks along a trail (0 when irregular): the flashes of an aircraft's
        strobe / beacon in one long exposure, or a meteor that flickers a few times, which stays irregular."""
        if len(xs) < self.STROBE_MIN_LINE_PX or not vals.size:
            return 0
        h, w = diff.shape
        prof = np.zeros(len(xs), np.float32)
        for dx, dy in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)):
            prof = np.maximum(prof, diff[np.clip(ys + dy, 0, h - 1), np.clip(xs + dx, 0, w - 1)].astype(np.float32))
        prof = np.convolve(prof, np.ones(3, np.float32) / 3.0, mode="same")
        need = self.STROBE_MIN_DIP * float(vals.mean())
        # a peak counts once the profile has risen `need` above the last dip and then fallen `need` below the top
        peaks, cand, low = [], None, float(prof[0])
        for i, v in enumerate(prof.tolist()):
            if cand is None:
                low = min(low, v)
                if v - low >= need:
                    cand = (i, v)
            elif v > cand[1]:
                cand = (i, v)
            elif cand[1] - v >= need:
                peaks.append(cand[0])
                cand, low = None, v
        if len(peaks) < self.STROBE_MIN_PULSES:
            return 0
        gaps = np.diff(peaks).astype(np.float64)
        if gaps.mean() <= 0 or float(gaps.std() / gaps.mean()) > self.STROBE_MAX_SPACING_CV:
            return 0
        return len(peaks)

    @staticmethod
    def _elongation(ln: Tuple[float, ...], diff: np.ndarray, thr: float) -> float:
        """Length / width of the brightened pixels around a streak (intensity-weighted moments). A meteor is a
        sharp line (high); a glint on a lens smudge or raindrop is a fuzzy blob (low)."""
        h, w = diff.shape
        x0, x1 = max(0, int(min(ln[0], ln[2])) - 6), min(w, int(max(ln[0], ln[2])) + 7)
        y0, y1 = max(0, int(min(ln[1], ln[3])) - 6), min(h, int(max(ln[1], ln[3])) + 7)
        sub = diff[y0:y1, x0:x1]
        ys, xs = np.nonzero(sub > thr)
        if len(xs) < 5:
            return 0.0
        wt = sub[ys, xs].astype(np.float64)
        wt /= wt.sum()
        mx, my = float((xs * wt).sum()), float((ys * wt).sum())
        cxx = float((((xs - mx) ** 2) * wt).sum())
        cyy = float((((ys - my) ** 2) * wt).sum())
        cxy = float(((xs - mx) * (ys - my) * wt).sum())
        d = math.sqrt((cxx - cyy) ** 2 + 4 * cxy * cxy)
        l1, l2 = (cxx + cyy + d) / 2, (cxx + cyy - d) / 2
        return round(math.sqrt(l1 / max(l2, 0.25)), 2)

    @staticmethod
    def _new_event(f: Dict[str, Any], ts: float, scale: float, frame: np.ndarray, small: np.ndarray) -> Dict[str, Any]:
        x1, y1, x2, y2 = f["line"]
        h, w = frame.shape[:2]
        fx = [x1 / scale, x2 / scale]
        fy = [y1 / scale, y2 / scale]
        pad = max(24, int(0.03 * w))
        cx0, cx1 = max(0, int(min(fx)) - pad), min(w, int(max(fx)) + pad)
        cy0, cy1 = max(0, int(min(fy)) - pad), min(h, int(max(fy)) + pad)
        crop = frame[cy0:cy1, cx0:cx1]
        if max(crop.shape[:2]) > 480:
            k = 480.0 / max(crop.shape[:2])
            crop = cv2.resize(crop, (max(1, int(crop.shape[1] * k)), max(1, int(crop.shape[0] * k))), interpolation=cv2.INTER_AREA)
        else:
            crop = crop.copy()
        overview = small.copy()
        m = 8  # box around the streak, leaving the streak itself visible
        cv2.rectangle(overview, (int(min(x1, x2)) - m, int(min(y1, y2)) - m), (int(max(x1, x2)) + m, int(max(y1, y2)) + m),
                      (0, 200, 255), 1, cv2.LINE_AA)
        return {
            "type": "plane" if f["dashed"] else "meteor",
            "ts": ts,
            "start_ts": ts,
            "end_ts": ts,
            "frames": 1,
            "length_px": round(f["length"] / scale, 1),
            "peak_dn": f["peak_dn"],
            "mean_dn": f["mean_dn"],
            "dashes": f["dashes"],
            "line": [round(v, 4) for v in (x1 / small.shape[1], y1 / small.shape[0], x2 / small.shape[1], y2 / small.shape[0])],
            "bbox": [cx0, cy0, cx1 - cx0, cy1 - cy0],
            "_crop": crop,
            "_overview": overview,
        }
