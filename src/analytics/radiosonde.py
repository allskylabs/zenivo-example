"""Radiosonde (weather balloon) feed from SondeHub: balloons near the station, extrapolated to the present."""
from __future__ import annotations

import json
import math
import threading
import time
import urllib.request

from src.net import urlopen as net_urlopen
from src.version import __version__
from datetime import datetime
from typing import Any, Dict, List

SONDEHUB_URL = "https://api.v2.sondehub.org/sondes?lat={lat}&lon={lon}&distance={dist}"
REFRESH_S = 20.0
MAX_AGE_S = 600.0       # a sonde not heard for 10 minutes has landed or gone silent
MAX_EXTRAP_S = 90.0
EARTH_R = 6371000.0


class RadiosondeFeed:
    def __init__(self, radius_km: float = 300.0):
        self.radius_m = int(radius_km * 1000)
        self._sondes: List[Dict[str, Any]] = []
        self._fetched = 0.0
        self._busy = False
        self._error = ""
        self._lock = threading.Lock()

    def _fetch(self, lat: float, lon: float) -> None:
        try:
            req = urllib.request.Request(SONDEHUB_URL.format(lat=lat, lon=lon, dist=self.radius_m),
                                         headers={"User-Agent": f"Zenivo/{__version__}"})
            with net_urlopen(req, timeout=15) as r:
                raw = json.loads(r.read().decode("utf-8"))
            out = []
            for s in raw.values():
                try:
                    t = datetime.fromisoformat(s["datetime"].replace("Z", "+00:00")).timestamp()
                    out.append({
                        "serial": s["serial"], "type": s.get("type") or s.get("subtype") or "", "t": t,
                        "lat": float(s["lat"]), "lon": float(s["lon"]), "alt": float(s["alt"]),
                        "vel_h": float(s.get("vel_h") or 0.0), "vel_v": float(s.get("vel_v") or 0.0),
                        "heading": float(s.get("heading") or 0.0), "freq": s.get("frequency"),
                        "temp": s.get("temp"), "burst_timer": s.get("burst_timer"),
                    })
                except (KeyError, ValueError, TypeError):
                    continue
            with self._lock:
                self._sondes, self._error = out, ""
        except Exception as exc:  # network hiccup: keep the last list
            with self._lock:
                self._error = str(exc)[:120]
        finally:
            with self._lock:
                self._fetched, self._busy = time.time(), False

    def current(self, station_lat: float, station_lon: float, at: float | None = None) -> Dict[str, Any]:
        now = time.time()
        with self._lock:
            if now - self._fetched > REFRESH_S and not self._busy:
                self._busy = True
                threading.Thread(target=self._fetch, args=(station_lat, station_lon), daemon=True).start()
            sondes, err = list(self._sondes), self._error
        at = now if at is None else at
        res = []
        for s in sondes:
            age = now - s["t"]
            if age > MAX_AGE_S:
                continue
            dt = max(0.0, min(MAX_EXTRAP_S, at - s["t"]))
            d = s["vel_h"] * dt / EARTH_R
            hd = math.radians(s["heading"])
            lat = s["lat"] + math.degrees(d * math.cos(hd))
            lon = s["lon"] + math.degrees(d * math.sin(hd) / max(0.05, math.cos(math.radians(s["lat"]))))
            res.append({**s, "lat": lat, "lon": lon, "alt": s["alt"] + s["vel_v"] * dt, "age_s": round(age)})
        return {"sondes": res, "error": err}
