"""Large, naked-eye satellites (space stations and the CelesTrak "visual" list) for the live overlay.

TLEs come from CelesTrak (refreshed daily, cached on disk so the overlay keeps working offline); positions
are propagated with SGP4, turned into az/alt for the station and projected through the star-solved camera
pointing like aircraft. A satellite can only be seen when the sky is dark and it is lit by the sun (outside
the Earth's shadow).
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np

from src.analytics.adsb import _ecef, frame_xy_from_enu

GROUPS = ("stations", "visual")
TLE_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP={group}&FORMAT=tle"
REFRESH_S = 24 * 3600.0
EARTH_R_KM = 6371.0
MAX_SATS = 400


def _parse_tle(text: str, group: str) -> List[Dict[str, str]]:
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    out = []
    for i in range(0, len(lines) - 2, 3):
        name, l1, l2 = lines[i].strip(), lines[i + 1], lines[i + 2]
        if l1.startswith("1 ") and l2.startswith("2 "):
            out.append({"name": name, "l1": l1, "l2": l2, "group": group})
    return out


def _gmst_rad(jd: float) -> float:
    t = (jd - 2451545.0) / 36525.0
    g = 280.46061837 + 360.98564736629 * (jd - 2451545.0) + 0.000387933 * t * t - t ** 3 / 38710000.0
    return math.radians(g % 360.0)


def _sun_eci_dir(jd: float) -> np.ndarray:
    """Unit vector to the sun in the equatorial frame of date (low precision, ~0.01 deg)."""
    n = jd - 2451545.0
    L = math.radians((280.460 + 0.9856474 * n) % 360.0)
    g = math.radians((357.528 + 0.9856003 * n) % 360.0)
    lam = L + math.radians(1.915) * math.sin(g) + math.radians(0.020) * math.sin(2 * g)
    eps = math.radians(23.439 - 0.0000004 * n)
    return np.array([math.cos(lam), math.cos(eps) * math.sin(lam), math.sin(eps) * math.sin(lam)])


class SatelliteTracker:
    def __init__(self, cache_path: str):
        self.cache_path = cache_path
        self._lock = threading.Lock()
        self._sats: List[Any] = []  # (name, group, Satrec)
        self._fetched_at = 0.0
        self._fetching = False
        self._load_cache()

    def _load_cache(self) -> None:
        try:
            with open(self.cache_path) as fh:
                d = json.load(fh)
            self._set_tles(d.get("tles") or [], float(d.get("fetched_at") or 0.0))
        except Exception:
            pass

    def _set_tles(self, tles: List[Dict[str, str]], fetched_at: float) -> None:
        from sgp4.api import Satrec

        sats, seen = [], set()
        for t in tles:
            try:
                sat = Satrec.twoline2rv(t["l1"], t["l2"])
            except Exception:
                continue
            if sat.satnum in seen:
                continue
            seen.add(sat.satnum)
            sats.append((t["name"], t["group"], sat))
        with self._lock:
            self._sats, self._fetched_at = sats[:MAX_SATS], fetched_at

    def _fetch(self) -> None:
        try:
            tles: List[Dict[str, str]] = []
            for g in GROUPS:
                with urllib.request.urlopen(TLE_URL.format(group=g), timeout=20) as r:
                    tles += _parse_tle(r.read().decode("ascii", "replace"), g)
            if tles:
                now = time.time()
                self._set_tles(tles, now)
                os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
                with open(self.cache_path, "w") as fh:
                    json.dump({"fetched_at": now, "tles": tles}, fh)
        except Exception:
            self._fetched_at = max(self._fetched_at, time.time() - REFRESH_S + 1800.0)  # retry in 30 min
        finally:
            self._fetching = False

    def _maybe_refresh(self) -> None:
        if self._fetching or time.time() - self._fetched_at < REFRESH_S:
            return
        self._fetching = True
        threading.Thread(target=self._fetch, daemon=True, name="tle-fetch").start()

    def status(self) -> Dict[str, Any]:
        return {"count": len(self._sats), "fetched_at": self._fetched_at or None}

    def current(self, station: Dict[str, float], solution: Optional[Dict[str, Any]] = None,
                when: Optional[datetime] = None) -> Dict[str, Any]:
        """Satellites above the horizon now: az/alt, range, whether sunlit, and frame position."""
        from sgp4.api import jday

        self._maybe_refresh()
        when = (when or datetime.now(timezone.utc)).astimezone(timezone.utc)
        jd, fr = jday(when.year, when.month, when.day, when.hour, when.minute, when.second + when.microsecond / 1e6)
        jd_full = jd + fr
        gmst = _gmst_rad(jd_full)
        cg, sg = math.cos(gmst), math.sin(gmst)
        lat0, lon0 = station["latitude_deg"], station["longitude_deg"]
        obs = _ecef(lat0, lon0, float(station.get("elevation_m") or 0.0)) / 1000.0
        la, lo = math.radians(lat0), math.radians(lon0)
        sun = _sun_eci_dir(jd_full)
        # sun altitude at the station: its ECI direction rotated to ECEF, then to the local up vector
        sun_ecef = np.array([cg * sun[0] + sg * sun[1], -sg * sun[0] + cg * sun[1], sun[2]])
        up = np.array([math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la)])
        sun_alt = math.degrees(math.asin(float(np.clip(sun_ecef @ up, -1, 1))))
        out = []
        with self._lock:
            sats = list(self._sats)
        for name, group, sat in sats:
            err, r, _v = sat.sgp4(jd, fr)
            if err != 0:
                continue
            r = np.array(r)
            ecef = np.array([cg * r[0] + sg * r[1], -sg * r[0] + cg * r[1], r[2]])  # TEME -> ECEF (no polar motion)
            d = ecef - obs
            e = -math.sin(lo) * d[0] + math.cos(lo) * d[1]
            n = -math.sin(la) * math.cos(lo) * d[0] - math.sin(la) * math.sin(lo) * d[1] + math.cos(la) * d[2]
            u = math.cos(la) * math.cos(lo) * d[0] + math.cos(la) * math.sin(lo) * d[1] + math.sin(la) * d[2]
            rng = math.sqrt(e * e + n * n + u * u)
            alt = math.degrees(math.asin(u / rng))
            if alt < 0.0:
                continue
            along = float(r @ sun)
            sunlit = not (along < 0.0 and float(np.linalg.norm(r - along * sun)) < EARTH_R_KM)
            enu = np.array([e, n, u]) * 1000.0
            rec = {"name": name, "norad": int(sat.satnum), "group": group,
                   "az_deg": round(math.degrees(math.atan2(e, n)) % 360.0, 1), "alt_deg": round(alt, 1),
                   "range_km": round(rng), "height_km": round(float(np.linalg.norm(r)) - EARTH_R_KM),
                   "sunlit": sunlit, "visible": bool(sunlit and sun_alt < -3.0)}
            xy = frame_xy_from_enu(enu, solution)
            if xy:
                rec["frame_xy"] = xy
            out.append(rec)
        out.sort(key=lambda s: (not s.get("frame_xy"), not s["visible"], -s["alt_deg"]))
        return {"sun_alt_deg": round(sun_alt, 1), "tle_count": len(sats), "tle_age_h":
                round((time.time() - self._fetched_at) / 3600.0, 1) if self._fetched_at else None,
                "satellites": out}
