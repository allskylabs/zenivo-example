"""
Activity log: a human-readable record of what the station is doing and whether it worked.

Every entry has a level (info / success / warning / error), a category (system, camera, capture,
archive, timelapse, products, storage, sync, calibration, station) and a message. Entries are kept
in a ring buffer for the web UI and appended as JSON lines to a size-rotated file
(data/logs/activity.jsonl, 2 MB x 5 by default). Identical messages repeated within a few minutes
are folded into one entry with a repeat counter so a flapping error cannot flood the log.
"""

import json
import logging
import os
import threading
import time
from collections import deque
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Any, Deque, Dict, List, Optional

LEVELS = ("info", "success", "warning", "error")
_RANK = {"info": 0, "success": 0, "warning": 1, "error": 2}
_PY_LEVEL = {"info": logging.INFO, "success": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR}

logger = logging.getLogger("allsky.activity")


class ActivityLog:
    DEDUPE_WINDOW_S = 300.0

    def __init__(self, log_dir: Optional[str] = None, max_entries: int = 1000,
                 max_bytes: int = 2_000_000, backup_count: int = 5):
        self._lock = threading.Lock()
        self._entries: Deque[Dict[str, Any]] = deque(maxlen=max_entries)
        self._next_id = 1
        self._file_logger: Optional[logging.Logger] = None
        self.log_path: Optional[str] = None
        if log_dir:
            self._open_file(log_dir, max_bytes, backup_count)

    def _open_file(self, log_dir: str, max_bytes: int, backup_count: int) -> None:
        os.makedirs(log_dir, exist_ok=True)
        self.log_path = os.path.join(log_dir, "activity.jsonl")
        self._load_tail(self.log_path)
        handler = RotatingFileHandler(self.log_path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        flog = logging.getLogger(f"allsky.activity.file.{id(self)}")
        flog.propagate = False
        flog.setLevel(logging.INFO)
        flog.handlers = [handler]
        self._file_logger = flog

    def _load_tail(self, path: str) -> None:
        """Reload the most recent entries so the UI keeps its history across restarts."""
        if not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()[-self._entries.maxlen:]
        except OSError:
            return
        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            try:
                entry["_t"] = datetime.fromisoformat(entry.get("last_ts") or entry["ts"]).timestamp()
            except (KeyError, ValueError):
                entry["_t"] = 0.0
            entry["id"] = self._next_id
            self._next_id += 1
            self._entries.append(entry)

    # ------------------------------------------------------------------
    def log(self, level: str, category: str, message: str, **details: Any) -> Dict[str, Any]:
        level = level if level in LEVELS else "info"
        now = time.time()
        with self._lock:
            for prev in reversed(self._entries):
                if now - prev.get("_t", 0) > self.DEDUPE_WINDOW_S:
                    break
                if prev["level"] == level and prev["category"] == category and prev["message"] == message:
                    prev["repeat"] = prev.get("repeat", 1) + 1
                    prev["last_ts"] = datetime.now().astimezone().isoformat()
                    prev["_t"] = now
                    return prev
            entry = {
                "id": self._next_id,
                "ts": datetime.now().astimezone().isoformat(),
                "level": level,
                "category": category,
                "message": message,
                "_t": now,
            }
            if details:
                entry["details"] = {k: v for k, v in details.items() if v is not None}
            self._next_id += 1
            self._entries.append(entry)
        logger.log(_PY_LEVEL[level], "[%s] %s: %s", level.upper(), category, message)
        if self._file_logger is not None:
            try:
                self._file_logger.info(json.dumps({k: v for k, v in entry.items() if k not in ("id", "_t")}, ensure_ascii=False, default=str))
            except Exception:
                pass
        return entry

    def info(self, category: str, message: str, **details: Any):
        return self.log("info", category, message, **details)

    def success(self, category: str, message: str, **details: Any):
        return self.log("success", category, message, **details)

    def warning(self, category: str, message: str, **details: Any):
        return self.log("warning", category, message, **details)

    def error(self, category: str, message: str, **details: Any):
        return self.log("error", category, message, **details)

    # ------------------------------------------------------------------
    def query(self, since_id: int = 0, limit: int = 200, level: Optional[str] = None,
              category: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            items = [e for e in self._entries if e["id"] > since_id]
        if category:
            items = [e for e in items if e["category"] == category]
        if level == "success":
            items = [e for e in items if e["level"] == "success"]
        elif level in _RANK:
            # minimum severity: "warning" -> warnings + errors, "error" -> errors only
            items = [e for e in items if _RANK[e["level"]] >= _RANK[level]]
        out = [{k: v for k, v in e.items() if k != "_t"} for e in items[-max(1, limit):]]
        out.reverse()  # newest first
        return out

    def summary(self) -> Dict[str, Any]:
        cutoff = time.time() - 86400
        with self._lock:
            recent = [e for e in self._entries if e.get("_t", 0) >= cutoff]
            last_id = self._entries[-1]["id"] if self._entries else 0
        counts = {lvl: 0 for lvl in LEVELS}
        for e in recent:
            counts[e["level"]] += e.get("repeat", 1)
        return {"last_id": last_id, "counts_24h": counts, "log_file": self.log_path}


_activity: Optional[ActivityLog] = None
_activity_lock = threading.Lock()


def get_activity_log() -> ActivityLog:
    """Process-wide activity log (memory only until `init_activity_log` gives it a directory)."""
    global _activity
    with _activity_lock:
        if _activity is None:
            _activity = ActivityLog()
        return _activity


def init_activity_log(log_dir: str, **kwargs: Any) -> ActivityLog:
    global _activity
    with _activity_lock:
        _activity = ActivityLog(log_dir=log_dir, **kwargs)
        return _activity
