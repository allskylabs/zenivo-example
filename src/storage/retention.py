"""
Disk retention for the AllSky data directory.

Rules, applied in order:
1. Age: archived frame days older than `frames_keep_days`, dated keograms / startrails / timelapses
   older than `products_keep_days`, and files in extra directories (e.g. hoan.uk's ingest archive)
   older than their own `keep_days` are deleted. Stale `.latest_tmp_*` encoder leftovers go too.
2. Size: while the data directory is larger than `max_data_gb`, or the filesystem has less than
   `min_free_gb` free, the oldest archived frame day is deleted, then the oldest dated product.
   Today's frames and the `latest.*` products are never deleted by the size rule.
"""

import logging
import os
import re
import shutil
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger("allsky.retention")

DEFAULT_POLICY: Dict[str, Any] = {
    "frames_keep_days": 7,       # raw archived frames (data/frames/<YYYY-MM-DD>/)
    "products_keep_days": 60,    # dated keograms, startrails, timelapse videos
    "max_data_gb": 8.0,          # hard cap for the whole data directory
    "min_free_gb": 3.0,          # always leave this much free on the filesystem
    "archive_interval_s": 30.0,  # seconds between archived frames
    "timelapse_refresh_s": 1800.0,  # seconds between live timelapse re-encodes (each costs tens of CPU-s)
    "extra_dirs": [],            # [{"path": "...", "keep_days": 3}]
}

_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def merge_policy(cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    policy = dict(DEFAULT_POLICY)
    for key, val in (cfg or {}).items():
        if key in DEFAULT_POLICY and val is not None:
            policy[key] = val
    return policy


def dir_size_bytes(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def _parse_date(name: str):
    m = _DATE_RE.match(name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d").date()
    except ValueError:
        return None


def _remove(path: str) -> int:
    """Delete a file or directory tree, returning the bytes freed."""
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            size = dir_size_bytes(path)
            shutil.rmtree(path, ignore_errors=True)
        else:
            size = os.lstat(path).st_size
            os.remove(path)
        return size
    except OSError:
        return 0


class RetentionManager:
    def __init__(self, data_dir: str, policy: Optional[Dict[str, Any]] = None):
        self.data_dir = os.path.abspath(data_dir)
        self.frames_dir = os.path.join(self.data_dir, "frames")
        uploads = os.path.join(self.data_dir, "uploads", "allsky")
        self.product_dirs = [os.path.join(uploads, d) for d in ("keograms", "startrails", "timelapses", "events")]
        self.policy = merge_policy(policy)
        self.last_report: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    def _frame_days(self) -> List[Tuple[Any, str]]:
        if not os.path.isdir(self.frames_dir):
            return []
        days = []
        for name in os.listdir(self.frames_dir):
            d = _parse_date(name)
            path = os.path.join(self.frames_dir, name)
            if d and os.path.isdir(path):
                days.append((d, path))
        return sorted(days)

    def _dated_products(self) -> List[Tuple[Any, str]]:
        items = []
        for pdir in self.product_dirs:
            if not os.path.isdir(pdir):
                continue
            for name in os.listdir(pdir):
                d = _parse_date(name)
                if d:
                    items.append((d, os.path.join(pdir, name)))
        return sorted(items)

    def _free_bytes(self) -> int:
        try:
            return shutil.disk_usage(self.data_dir).free
        except OSError:
            return 1 << 62

    def usage(self) -> Dict[str, Any]:
        frames = self._frame_days()
        try:
            du = shutil.disk_usage(self.data_dir)
            disk = {"total_gb": round(du.total / 1e9, 2), "used_gb": round(du.used / 1e9, 2), "free_gb": round(du.free / 1e9, 2)}
        except OSError:
            disk = {}
        return {
            "data_dir": self.data_dir,
            "data_gb": round(dir_size_bytes(self.data_dir) / 1e9, 3),
            "frames_gb": round(dir_size_bytes(self.frames_dir) / 1e9, 3) if os.path.isdir(self.frames_dir) else 0.0,
            "frame_days": [d.isoformat() for d, _ in frames],
            "frame_files": sum(len(os.listdir(p)) for _, p in frames),
            "products_gb": round(sum(dir_size_bytes(p) for p in self.product_dirs if os.path.isdir(p)) / 1e9, 3),
            "disk": disk,
            "policy": dict(self.policy),
            "last_cleanup": dict(self.last_report),
        }

    # ------------------------------------------------------------------
    def run(self, today=None) -> Dict[str, Any]:
        p = self.policy
        today = today or datetime.now().astimezone().date()
        deleted: List[str] = []
        freed = 0

        def drop(path: str) -> None:
            nonlocal freed
            freed += _remove(path)
            deleted.append(path)

        # 1. Age-based rules
        frame_cutoff = today - timedelta(days=max(1, int(p["frames_keep_days"])))
        for d, path in self._frame_days():
            if d < frame_cutoff:
                drop(path)
        prod_cutoff = today - timedelta(days=max(1, int(p["products_keep_days"])))
        for d, path in self._dated_products():
            if d < prod_cutoff:
                drop(path)
        for pdir in self.product_dirs:
            if os.path.isdir(pdir):
                for name in os.listdir(pdir):
                    path = os.path.join(pdir, name)
                    if name.startswith(".latest_tmp_") and time.time() - os.path.getmtime(path) > 3600:
                        drop(path)
        for extra in p.get("extra_dirs") or []:
            for path in self._expired_in_extra_dir(extra.get("path", ""), float(extra.get("keep_days", 7))):
                drop(path)

        # 2. Size / free-space rules (oldest first, never today's frames or latest.* products)
        max_bytes = float(p["max_data_gb"]) * 1e9
        min_free = float(p["min_free_gb"]) * 1e9
        data_bytes = dir_size_bytes(self.data_dir)
        candidates = [(d, path) for d, path in self._frame_days() if d < today]
        candidates += [(d, path) for d, path in self._dated_products() if d < today]
        candidates.sort()
        for _d, path in candidates:
            if data_bytes <= max_bytes and self._free_bytes() >= min_free:
                break
            size = _remove(path)
            freed += size
            data_bytes -= size
            deleted.append(path)

        self.last_report = {
            "at": datetime.now().astimezone().isoformat(),
            "deleted_count": len(deleted),
            "freed_mb": round(freed / 1e6, 1),
            "deleted": deleted[:50],
        }
        if deleted:
            logger.warning("Retention removed %d item(s), freed %.1f MB", len(deleted), freed / 1e6)
        return self.last_report

    @staticmethod
    def _expired_in_extra_dir(root: str, keep_days: float) -> Iterable[str]:
        """Dated sub-directories (YYYY-MM-DD*) older than keep_days, else plain files by mtime."""
        if not root or not os.path.isdir(root):
            return []
        cutoff_date = datetime.now().astimezone().date() - timedelta(days=max(1, int(keep_days)))
        cutoff_ts = time.time() - keep_days * 86400
        out = []
        for name in os.listdir(root):
            path = os.path.join(root, name)
            d = _parse_date(name)
            if d is not None:
                if d < cutoff_date:
                    out.append(path)
            elif os.path.isfile(path) and os.path.getmtime(path) < cutoff_ts:
                out.append(path)
        return out
