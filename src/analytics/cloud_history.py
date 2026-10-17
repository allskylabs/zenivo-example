"""
Cloud cover time series: one compact JSON line per analysed sample (every few minutes), so a year
is a few MB. Queries read the file once and keep it cached until it changes.
"""

import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

MAX_BYTES = 8 * 1024 * 1024   # beyond this the file is rewritten keeping the last KEEP_DAYS
KEEP_DAYS = 400


class CloudHistory:
    def __init__(self, path: str):
        self.path = path
        self.lock = threading.Lock()
        self._cache: List[Dict[str, Any]] = []
        self._cache_key: Optional[tuple] = None

    def append(self, rec: Dict[str, Any]) -> None:
        line = json.dumps(rec, separators=(",", ":")) + "\n"
        with self.lock:
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as fp:
                    fp.write(line)
                if os.path.getsize(self.path) > MAX_BYTES:
                    self._trim(time.time() - KEEP_DAYS * 86400)
            except OSError:
                pass

    def _trim(self, min_t: float) -> None:
        recs = [r for r in self._read_all() if r.get("t", 0) >= min_t]
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fp:
            for r in recs:
                fp.write(json.dumps(r, separators=(",", ":")) + "\n")
        os.replace(tmp, self.path)
        self._cache_key = None

    def _read_all(self) -> List[Dict[str, Any]]:
        try:
            st = os.stat(self.path)
        except OSError:
            return []
        key = (st.st_mtime_ns, st.st_size)
        if key != self._cache_key:
            recs = []
            with open(self.path, "r", encoding="utf-8") as fp:
                for line in fp:
                    try:
                        recs.append(json.loads(line))
                    except ValueError:
                        continue  # a torn last line after a power cut
            recs.sort(key=lambda r: r.get("t", 0))
            self._cache, self._cache_key = recs, key
        return self._cache

    def query(self, start_t: float, end_t: float, max_points: int = 600) -> List[Dict[str, Any]]:
        """Samples in [start_t, end_t]; averaged into time buckets when there are more than max_points."""
        with self.lock:
            recs = [r for r in self._read_all() if start_t <= r.get("t", 0) <= end_t]
        if len(recs) <= max_points:
            return recs
        width = (end_t - start_t) / max_points
        buckets: Dict[int, List[Dict[str, Any]]] = {}
        for r in recs:
            buckets.setdefault(int((r["t"] - start_t) // width), []).append(r)
        out = []
        for idx in sorted(buckets):
            group = buckets[idx]
            vals = [r["cc"] for r in group if r.get("cc") is not None]
            stars = [r.get("stars", 0) for r in group]
            out.append({
                "t": int(start_t + (idx + 0.5) * width),
                "cc": round(sum(vals) / len(vals), 1) if vals else None,
                "m": group[len(group) // 2].get("m"),
                "conf": round(sum(r.get("conf", 0.0) for r in group) / len(group), 2),
                "stars": int(round(sum(stars) / len(stars))),
                "n": len(group),
            })
        return out
