"""
Sky traffic log: aircraft and satellites inside the camera's field of view, kept for replay.

One JSON line per sample in data/traffic/YYYY-MM-DD.jsonl (only samples with something in view):
  {"t": epoch_s, "a": [[icao, callsign, reg, type, lat, lon, alt_m, speed_kmh, heading, az, el, x, y], ...],
   "s": [[norad, name, az, el, x, y, range_km, sunlit], ...]}
x, y are frame fractions of the star-solved camera (as sky_position / the satellite tracker give them).
Positions are the fixes at the sample time, so a video or the live page can redraw them exactly; compare with
the events archive to name the aircraft behind a streak.

Disk use is bounded two ways: files older than `retention_days` are deleted, and the oldest files go first
until the folder is below `max_mb`.
"""

import json
import os
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

DEFAULTS: Dict[str, Any] = {"enabled": 1, "interval_s": 5.0, "retention_days": 14, "max_mb": 150}
PRUNE_EVERY_S = 3600.0


class TrafficLog:
    def __init__(self, folder: str, cfg: Optional[Dict[str, Any]] = None):
        self.folder = folder
        self._lock = threading.Lock()
        self._last_prune = 0.0
        self.cfg = dict(DEFAULTS)
        self.configure(cfg)

    def configure(self, cfg: Optional[Dict[str, Any]]) -> None:
        for k, default in DEFAULTS.items():
            try:
                v = (cfg or {}).get(k, self.cfg.get(k, default))
                self.cfg[k] = type(default)(v)
            except (TypeError, ValueError):
                self.cfg[k] = default
        self.cfg["interval_s"] = max(1.0, self.cfg["interval_s"])
        self.cfg["retention_days"] = max(1, self.cfg["retention_days"])
        self.cfg["max_mb"] = max(5, self.cfg["max_mb"])

    # ------------------------------------------------------------------
    def _path(self, ts: float) -> str:
        return os.path.join(self.folder, datetime.fromtimestamp(ts).strftime("%Y-%m-%d") + ".jsonl")

    def append(self, ts: float, aircraft: List[List[Any]], satellites: List[List[Any]]) -> bool:
        """Store one sample (nothing is written when both lists are empty). Returns True when written."""
        if not aircraft and not satellites:
            return False
        line = json.dumps({"t": round(ts, 1), "a": aircraft, "s": satellites}, separators=(",", ":"))
        with self._lock:
            os.makedirs(self.folder, exist_ok=True)
            with open(self._path(ts), "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            if ts - self._last_prune >= PRUNE_EVERY_S:
                self._last_prune = ts
                self._prune(ts)
        return True

    def append_record(self, ts: float, rec: Dict[str, Any]) -> None:
        """Store one free-form record (it carries its own "t"); same retention as the samples."""
        line = json.dumps(rec, separators=(",", ":"))
        with self._lock:
            os.makedirs(self.folder, exist_ok=True)
            with open(self._path(ts), "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            if ts - self._last_prune >= PRUNE_EVERY_S:
                self._last_prune = ts
                self._prune(ts)

    def _files(self) -> List[str]:
        try:
            return sorted(f for f in os.listdir(self.folder) if f.endswith(".jsonl") and len(f) == 16)
        except OSError:
            return []

    def _prune(self, now: float) -> int:
        """Delete files older than the retention, then the oldest until under the size cap. Returns files removed."""
        removed = 0
        keep_from = datetime.fromtimestamp(now - self.cfg["retention_days"] * 86400.0).strftime("%Y-%m-%d") + ".jsonl"
        files = self._files()
        for f in list(files):
            if f < keep_from:
                self._remove(f)
                files.remove(f)
                removed += 1
        cap = self.cfg["max_mb"] * 1024 * 1024
        sizes = {f: self._size(f) for f in files}
        total = sum(sizes.values())
        while files[:-1] and total > cap:  # never delete today's (newest) file
            f = files.pop(0)
            total -= sizes.get(f, 0)
            self._remove(f)
            removed += 1
        return removed

    def prune(self, now: Optional[float] = None) -> int:
        with self._lock:
            return self._prune(time.time() if now is None else now)

    def _size(self, name: str) -> int:
        try:
            return os.path.getsize(os.path.join(self.folder, name))
        except OSError:
            return 0

    def _remove(self, name: str) -> None:
        try:
            os.remove(os.path.join(self.folder, name))
        except OSError:
            pass

    # ------------------------------------------------------------------
    def query(self, t0: float, t1: float, limit: int = 2000) -> List[Dict[str, Any]]:
        """Samples with t0 <= t <= t1, oldest first (at most `limit`)."""
        out: List[Dict[str, Any]] = []
        d0 = datetime.fromtimestamp(t0).strftime("%Y-%m-%d")
        d1 = datetime.fromtimestamp(t1).strftime("%Y-%m-%d")
        for f in self._files():
            if not (d0 <= f[:10] <= d1):
                continue
            try:
                with open(os.path.join(self.folder, f), encoding="utf-8") as fh:
                    for ln in fh:
                        try:
                            s = json.loads(ln)
                        except ValueError:
                            continue  # a torn last line after a crash
                        if t0 <= s.get("t", 0) <= t1:
                            out.append(s)
                            if len(out) >= limit:
                                return out
            except OSError:
                continue
        return out

    def status(self) -> Dict[str, Any]:
        files = self._files()
        return {**self.cfg, "files": len(files), "size_mb": round(sum(self._size(f) for f in files) / 1048576.0, 2),
                "oldest": files[0][:10] if files else None, "newest": files[-1][:10] if files else None}
