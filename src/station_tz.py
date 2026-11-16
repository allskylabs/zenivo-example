"""Station time zone: what every wall-clock time, observing day and file name of this station follows.

Setting values: "" (the operating system's zone, the default), "coords" (a fixed whole-hour offset from the
station's longitude) or an IANA name such as "America/Chicago" (daylight saving included). It is applied to the
process (TZ + tzset), so datetime.now() and astimezone() everywhere follow it, and published to the web UI so the
browser shows the same clock instead of its own.
"""
from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_OS_TZ = os.environ.get("TZ")  # as the service was started, to go back to it
_ZONE_DIR = "zoneinfo/"


def valid_name(name: str) -> bool:
    try:
        ZoneInfo(name)
        return bool(name) and not name.startswith("/") and ".." not in name
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return False


def os_zone_name() -> Optional[str]:
    """IANA name of the operating system's zone (TZ, /etc/timezone, the /etc/localtime link), if it can be told."""
    for cand in (_OS_TZ, ):
        if cand and valid_name(cand.lstrip(":")):
            return cand.lstrip(":")
    try:
        with open("/etc/timezone", encoding="utf-8") as fh:
            name = fh.read().strip()
        if name and valid_name(name):
            return name
    except OSError:
        pass
    try:
        target = os.path.realpath("/etc/localtime")
        if _ZONE_DIR in target:
            name = target.split(_ZONE_DIR, 1)[1]
            if valid_name(name):
                return name
    except OSError:
        pass
    return None


def coords_offset_hours(lon: float) -> int:
    return max(-12, min(14, int(round(float(lon) / 15.0))))


def _posix_fixed(hours: int) -> str:
    # POSIX TZ offsets are positive WEST of Greenwich
    return f"<{'+' if hours >= 0 else '-'}{abs(hours):02d}>{-hours}"


def apply(setting: str, lon: float) -> Dict[str, Any]:
    """Make the process follow `setting` and return {setting, name, offset_min} (name: IANA or None for a fixed offset)."""
    setting = (setting or "").strip()
    if setting == "coords":
        os.environ["TZ"] = _posix_fixed(coords_offset_hours(lon))
        name = None
    elif setting and valid_name(setting):
        os.environ["TZ"] = setting
        name = setting
    else:
        setting = ""
        if _OS_TZ is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = _OS_TZ
        name = os_zone_name()
    time.tzset()
    off = datetime.now().astimezone().utcoffset()
    return {"setting": setting, "name": name, "offset_min": int(off.total_seconds() // 60) if off else 0}
