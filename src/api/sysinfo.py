"""Host load for the Admin page (CPU, RAM, disk, temperature) read from /proc and /sys: no psutil, nothing heavy for the Pi."""
from __future__ import annotations

import glob
import shutil
import threading
import time
from typing import Any, Dict, Optional, Tuple

_lock = threading.Lock()
_last: Optional[Tuple[float, int, int]] = None   # (monotonic time, idle ticks, total ticks)


def _cpu_ticks(text: str) -> Tuple[int, int]:
    """(idle, total) jiffies from the first 'cpu' line of /proc/stat (idle includes iowait)."""
    parts = [int(x) for x in text.splitlines()[0].split()[1:]]
    return parts[3] + (parts[4] if len(parts) > 4 else 0), sum(parts[:8])


def parse_meminfo(text: str) -> Dict[str, int]:
    out = {}
    for line in text.splitlines():
        k, _, v = line.partition(":")
        if v.split():
            out[k] = int(v.split()[0])   # kB
    return out


def cpu_percent(read=lambda: open("/proc/stat").read(), sleep=time.sleep) -> Optional[float]:
    """CPU busy share since the previous call; the first call (or one after a long gap) measures over 0.25 s."""
    global _last
    try:
        with _lock:
            now = time.monotonic()
            if _last is None or now - _last[0] > 60.0:
                idle0, total0 = _cpu_ticks(read())
                sleep(0.25)
                prev = (now, idle0, total0)
                now = time.monotonic()
            else:
                prev = _last
            idle, total = _cpu_ticks(read())
            _last = (now, idle, total)
        dt = total - prev[2]
        return round(100.0 * (1.0 - (idle - prev[1]) / dt), 1) if dt > 0 else 0.0
    except (OSError, ValueError, IndexError):
        return None


def temperature_c() -> Optional[float]:
    """Hottest thermal zone (Pi SoC, x86 package) in Celsius, None when the host exposes none."""
    best = None
    for path in glob.glob("/sys/class/thermal/thermal_zone*/temp"):
        try:
            v = int(open(path).read().strip()) / 1000.0
        except (OSError, ValueError):
            continue
        if 0 < v < 150 and (best is None or v > best):
            best = v
    return None if best is None else round(best, 1)


def resources(data_dir: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {"cpu_pct": cpu_percent(), "temp_c": temperature_c()}
    try:
        mi = parse_meminfo(open("/proc/meminfo").read())
        total, avail = mi["MemTotal"], mi.get("MemAvailable", mi.get("MemFree", 0))
        out.update(ram_pct=round(100.0 * (total - avail) / total, 1), ram_used_mb=round((total - avail) / 1024), ram_total_mb=round(total / 1024))
    except (OSError, KeyError, ZeroDivisionError):
        out.update(ram_pct=None, ram_used_mb=None, ram_total_mb=None)
    try:
        du = shutil.disk_usage(data_dir)
        out.update(disk_pct=round(100.0 * du.used / du.total, 1), disk_free_gb=round(du.free / 1e9, 1), disk_total_gb=round(du.total / 1e9, 1))
    except OSError:
        out.update(disk_pct=None, disk_free_gb=None, disk_total_gb=None)
    try:
        out["load1"] = round(float(open("/proc/loadavg").read().split()[0]), 2)
    except (OSError, ValueError, IndexError):
        out["load1"] = None
    return out
