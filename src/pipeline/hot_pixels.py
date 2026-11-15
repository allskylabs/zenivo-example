"""
Automatic hot-pixel map from the live night frames.

Stars drift with the sky (~4 px per 90 s at the IMX335 fisheye scale); hot and warm sensor pixels stay on
the same pixel. The tracker samples a frame now and then (never per frame), keeps bright point-like
peaks that sit on exactly the same pixel over at least `min_span_s`, and repairs those spots in every
long-exposure frame by filling a small patch with the median of the ring around it. Master darks miss
pixels that turn hot later (temperature, ageing) or at other gains; this catches them without a dark.
"""

import json
import os
import time
from typing import Any, Dict, Optional, Tuple

import cv2
import numpy as np

PATCH = 2  # repaired half-size: 5x5 covers the 3x3 demosaic/JPEG spread of one hot pixel
RING = 3   # ring at half-size 3 (7x7 border) supplies the replacement value


def find_peaks(frame: np.ndarray, min_excess: float = 18.0, max_points: int = 3000) -> np.ndarray:
    """Sharp local maxima well above their 5x5 median, as (N, 3) x, y, excess sorted brightest first."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    if gray.dtype != np.uint8:
        gray = cv2.convertScaleAbs(gray, alpha=255.0 / max(1.0, float(gray.max())))
    excess = cv2.subtract(gray, cv2.medianBlur(gray, 5))
    peak = (excess >= min_excess) & (excess == cv2.dilate(excess, np.ones((3, 3), np.uint8)))
    h, w = gray.shape
    b = RING + 1
    peak[:b, :] = peak[-b:, :] = False
    peak[:, :b] = peak[:, -b:] = False
    ys, xs = np.nonzero(peak)
    if len(xs) == 0:
        return np.zeros((0, 3))
    vals = excess[ys, xs].astype(np.float64)
    order = np.argsort(-vals)[:max_points]
    return np.column_stack([xs[order], ys[order], vals[order]]).astype(np.float64)


class StaticPointTracker:
    """Learns hot pixels from sampled frames (native sensor orientation) and repairs them."""

    def __init__(self, path: Optional[str] = None, sample_interval_s: float = 90.0, min_span_s: float = 600.0,
                 min_hits: int = 6, max_misses_confirmed: int = 20, max_points: int = 2000):
        self.path = path
        self.sample_interval_s = sample_interval_s
        self.min_span_s = min_span_s
        self.min_hits = min_hits
        self.max_misses_confirmed = max_misses_confirmed
        self.max_points = max_points
        self.shape: Optional[Tuple[int, int]] = None
        self.candidates: Dict[Tuple[int, int], Dict[str, float]] = {}
        self.confirmed: Dict[Tuple[int, int], Dict[str, float]] = {}
        self.last_sample_ts = 0.0
        self._idx: Optional[Tuple[np.ndarray, np.ndarray]] = None
        self._load()

    # ------------------------------------------------------------------ persistence
    def _load(self) -> None:
        if not self.path or not os.path.isfile(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.shape = tuple(data["shape"]) if data.get("shape") else None
            for x, y, seen in data.get("points", []):
                self.confirmed[(int(x), int(y))] = {"first": float(seen), "last": float(seen), "hits": self.min_hits,
                                                    "misses": 0}
        except (OSError, ValueError, KeyError, TypeError):
            self.confirmed = {}

    def _save(self) -> None:
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"shape": list(self.shape) if self.shape else None,
                           "points": [[x, y, round(p["last"])] for (x, y), p in self.confirmed.items()]}, f)
            os.replace(tmp, self.path)
        except OSError:
            pass

    # ------------------------------------------------------------------ learning
    def due(self, now: float) -> bool:
        return now - self.last_sample_ts >= self.sample_interval_s

    def sample(self, frame: np.ndarray, now: Optional[float] = None, sky_mask: Optional[np.ndarray] = None) -> int:
        """Feed one unrepaired frame; returns how many hot pixels are confirmed now.

        Every peak is tracked (a passing cloud must not reset the count); sky_mask only decides which
        static points get repaired, so lamps on buildings stay."""
        now = time.time() if now is None else now
        self.last_sample_ts = now
        shape = tuple(frame.shape[:2])
        if self.shape != shape:
            # Another resolution or camera: the old pixel positions mean nothing here
            self.shape, self.candidates, self.confirmed, self._idx = shape, {}, {}, None
        peaks = find_peaks(frame)
        seen = set()
        for x, y, _ in peaks:
            ix, iy = int(x), int(y)
            # A hot pixel's peak can hop by one pixel between frames (noise, JPEG); stars move > 3 px
            key = next(((ix + dx, iy + dy) for dx in (0, -1, 1) for dy in (0, -1, 1)
                        if (ix + dx, iy + dy) in self.confirmed or (ix + dx, iy + dy) in self.candidates), (ix, iy))
            seen.add(key)
            rec = self.confirmed.get(key) or self.candidates.setdefault(key, {"first": now, "hits": 0, "misses": 0})
            rec.update(last=now, hits=rec["hits"] + 1, misses=0)
        changed = False
        for key in list(self.candidates):
            rec = self.candidates[key]
            if key in seen:
                on_sky = sky_mask is None or bool(sky_mask[key[1], key[0]])
                if on_sky and rec["hits"] >= self.min_hits and now - rec["first"] >= self.min_span_s \
                        and len(self.confirmed) < self.max_points:
                    self.confirmed[key] = self.candidates.pop(key)
                    changed = True
            else:
                rec["misses"] += 1
                if rec["misses"] >= 3:
                    del self.candidates[key]
        for key in list(self.confirmed):
            if key not in seen:
                self.confirmed[key]["misses"] += 1
                if self.confirmed[key]["misses"] >= self.max_misses_confirmed:
                    del self.confirmed[key]  # cooled down or gone: stop painting over it
                    changed = True
        if changed:
            self._idx = None
            self._save()
        return len(self.confirmed)

    # ------------------------------------------------------------------ repair
    def _indices(self) -> Optional[Tuple[np.ndarray, np.ndarray]]:
        if self._idx is None and self.confirmed and self.shape:
            h, w = self.shape
            pts = np.array(list(self.confirmed), dtype=np.int64)
            r = np.arange(-RING, RING + 1)
            ring = [(dx, dy) for dy in r for dx in r if max(abs(dx), abs(dy)) == RING]
            patch = [(dx, dy) for dy in range(-PATCH, PATCH + 1) for dx in range(-PATCH, PATCH + 1)]
            to_flat = lambda offs: (np.clip(pts[:, 1:2] + [o[1] for o in offs], 0, h - 1) * w
                                    + np.clip(pts[:, 0:1] + [o[0] for o in offs], 0, w - 1))
            self._idx = (to_flat(patch), to_flat(ring))
        return self._idx

    def repair(self, frame: np.ndarray) -> np.ndarray:
        """Fill every confirmed hot pixel with its surroundings (in place when possible)."""
        idx = self._indices()
        if idx is None or tuple(frame.shape[:2]) != self.shape:
            return frame
        patch, ring = idx
        out = frame if frame.flags.writeable and frame.flags.c_contiguous else np.ascontiguousarray(frame).copy()
        flat = out.reshape(-1, out.shape[2]) if out.ndim == 3 else out.reshape(-1, 1)
        fill = np.median(flat[ring], axis=1).astype(flat.dtype)  # (N, C)
        flat[patch] = fill[:, None, :]
        return out

    def status(self) -> Dict[str, Any]:
        return {"hot_pixels": len(self.confirmed), "candidates": len(self.candidates),
                "last_sample_ts": self.last_sample_ts or None}

    def mask(self, shape: Tuple[int, ...]) -> Optional[np.ndarray]:
        """Confirmed hot pixels scaled to `shape`, grown by 2 px (True = hot), for the plate solver."""
        if not self.confirmed or not self.shape:
            return None
        h, w = shape[:2]
        pts = np.array(list(self.confirmed), dtype=np.float64)
        out = np.zeros((h, w), np.uint8)
        out[np.minimum(h - 1, (pts[:, 1] * h / self.shape[0]).astype(int)),
            np.minimum(w - 1, (pts[:, 0] * w / self.shape[1]).astype(int))] = 1
        return cv2.dilate(out, np.ones((5, 5), np.uint8)).astype(bool)
