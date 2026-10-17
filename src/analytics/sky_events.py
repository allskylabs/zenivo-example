"""
Lightweight sky event detection: meteors, aircraft, satellites and flashes.

Each new frame is reduced to a ~640 px grayscale copy and compared with the previous one (dilated, so
star drift and twinkle do not show up). Bright straight streaks are found with a probabilistic Hough
transform on the few changed pixels, then classified across frames:

- meteor: a solid streak in one frame only (confirmed when the next frame shows no continuation)
- plane: a dashed streak (strobe / navigation lights) or one that continues along the same line in the
  next frame
- satellite: a solid, faint streak that continues along the same line
- flash: the whole sky brightens at an unchanged exposure (lightning, headlights on the dome)

Frames whose exposure or gain changed, or where too much of the sky changed (moving clouds), are skipped.
"""

import math
import time
from typing import Any, Dict, List, Optional, Tuple

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
        self.frames_seen = 0      # frames given to process() since the last reset
        self.frames_compared = 0  # of those, compared with the previous frame
        self.reset()

    def reset(self) -> None:
        self.frames_seen = self.frames_compared = 0
        self._prev: Optional[np.ndarray] = None
        self._prev_ts = 0.0
        self._prev_key: Tuple[Any, ...] = ()
        self._prev_mean = 0.0
        self._pending: List[Dict[str, Any]] = []  # single-frame streaks waiting for the next frame
        self._tracks: List[Dict[str, Any]] = []   # moving objects seen in the previous frame
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
            b = max(2, w // 100)  # frame edge: vignetting, sensor edge artefacts
            m[:b, :] = 0
            m[-b:, :] = 0
            m[:, :b] = 0
            m[:, -b:] = 0
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
        out = [p["event"] for p in self._pending if "meteor" in enabled]
        for t in self._tracks:
            ev = self._finish_track(t, enabled)
            if ev:
                out.append(ev)
        self._pending, self._tracks = [], []
        return out

    @staticmethod
    def _finish_track(track: Dict[str, Any], enabled: set) -> Optional[Dict[str, Any]]:
        ev = track["event"]
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
        small, gray, scale = self._reduce(frame)
        mask = self._work_mask(gray.shape, sky_polygons)
        mean_now = float(cv2.mean(gray, mask)[0])

        key = (gray.shape, round(math.log(max(exposure_s, 1e-6)), 1), round(gain_db))
        prev, prev_mean = self._prev, self._prev_mean
        comparable = prev is not None and key == self._prev_key and 0 < ts - self._prev_ts <= self.max_gap_s
        self._prev, self._prev_ts, self._prev_key, self._prev_mean = gray, ts, key, mean_now

        if not comparable:
            return self._flush(enabled, ts)
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
        diff = cv2.bitwise_and(diff, mask)
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
                lines = _merge_segments([tuple(int(v) for v in s[0]) for s in segs[:80]], max_gap=w * 0.12)

        fat = cv2.dilate(binary, np.ones((3, 3), np.uint8))
        found: List[Dict[str, Any]] = []
        for ln in lines:
            m = self._measure(ln, fat, diff, thr)
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
                next_pending.append({"line": ln, "event": ev})

        for i, p in enumerate(self._pending):
            if i not in used_pending and "meteor" in enabled:
                events.append(p["event"])
        for i, t in enumerate(self._tracks):
            if i not in used_tracks:
                ev = self._finish_track(t, enabled)
                if ev:
                    events.append(ev)
        self._pending, self._tracks = next_pending, next_tracks
        return events

    def _measure(self, ln: Tuple[float, ...], fat: np.ndarray, diff: np.ndarray, thr: float) -> Optional[Dict[str, Any]]:
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
        side, side_dn = 0.0, 0.0
        for off in (-4, 4):
            sx = np.clip(np.round(xs - off * uy).astype(int), 0, w - 1)
            sy = np.clip(np.round(ys + off * ux).astype(int), 0, h - 1)
            side = max(side, float((fat[sy, sx] > 0).mean()))
            side_dn = max(side_dn, float(diff[sy, sx].mean()))
        if side > 0.5 * fill:
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
        # Drifting cloud texture lines up by chance but barely clears the threshold: demand a clear edge
        if not vals.size or float(vals.mean()) - side_dn < 1.5 * thr:
            return None
        return {
            "length": float(length),
            "fill": round(fill, 2),
            "dashes": runs,
            "dashed": runs >= 4 and fill < 0.85,
            "peak_dn": int(vals.max()) if vals.size else 0,
            "mean_dn": round(float(vals.mean()), 1) if vals.size else 0.0,
        }

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
