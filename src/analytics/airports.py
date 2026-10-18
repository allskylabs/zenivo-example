"""
Airports and runways around the station for the 3D flights map.

Source: OurAirports (public domain, not OpenStreetMap-derived). The two CSVs (~17 MB) are downloaded once
into the cache directory; the airports and runways within RADIUS_KM of the station are kept as a small
JSON file, rebuilt when the station moves or the data is older than a month.
"""

import csv
import io
import json
import math
import os
import threading
import time
import urllib.request
from typing import Any, Dict, List, Optional

BASE = "https://davidmegginson.github.io/ourairports-data"
USER_AGENT = "allsky-ng (+https://allsky.hoan.uk)"
RADIUS_KM = 450.0
MAX_AGE_S = 30 * 24 * 3600.0
TYPES = {"large_airport", "medium_airport", "small_airport"}


def _km(lat0: float, lon0: float, lat: float, lon: float) -> float:
    dx = (lon - lon0) * 111.32 * math.cos(math.radians(lat0))
    dy = (lat - lat0) * 110.57
    return math.hypot(dx, dy)


def _f(v: str) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def build(airports_csv: str, runways_csv: str, lat0: float, lon0: float, radius_km: float = RADIUS_KM) -> Dict[str, Any]:
    """Airports (large/medium/small, open) and runways with both thresholds known, near (lat0, lon0)."""
    airports: Dict[str, Dict[str, Any]] = {}
    for r in csv.DictReader(io.StringIO(airports_csv)):
        if r.get("type") not in TYPES:
            continue
        lat, lon = _f(r.get("latitude_deg")), _f(r.get("longitude_deg"))
        if lat is None or lon is None or _km(lat0, lon0, lat, lon) > radius_km:
            continue
        airports[r["ident"]] = {"ident": r["ident"], "type": r["type"].split("_")[0], "name": r.get("name") or "",
                                "iata": r.get("iata_code") or "", "lat": round(lat, 5), "lon": round(lon, 5),
                                "elev_m": round((_f(r.get("elevation_ft")) or 0.0) * 0.3048)}
    runways: List[Dict[str, Any]] = []
    for r in csv.DictReader(io.StringIO(runways_csv)):
        if r.get("airport_ident") not in airports or r.get("closed") == "1":
            continue
        le = (_f(r.get("le_latitude_deg")), _f(r.get("le_longitude_deg")))
        he = (_f(r.get("he_latitude_deg")), _f(r.get("he_longitude_deg")))
        if None in le or None in he:
            continue
        runways.append({"airport": r["airport_ident"], "le": [round(le[0], 6), round(le[1], 6)],
                        "he": [round(he[0], 6), round(he[1], 6)],
                        "width_m": round((_f(r.get("width_ft")) or 100.0) * 0.3048, 1),
                        "ids": f"{r.get('le_ident') or ''}/{r.get('he_ident') or ''}",
                        "lighted": r.get("lighted") == "1"})
    with_rwy = {rw["airport"] for rw in runways}
    # Keep small airfields only when they have a mapped runway (most are farm strips without one)
    out_airports = [a for a in airports.values() if a["type"] != "small" or a["ident"] in with_rwy]
    return {"airports": out_airports, "runways": runways}


class Airports:
    def __init__(self, cache_dir: str):
        self.cache_dir = cache_dir
        self.path = os.path.join(cache_dir, "airports_near.json")
        self._lock = threading.Lock()
        self._busy = False
        self._data: Optional[Dict[str, Any]] = None

    def get(self, lat0: float, lon0: float) -> Dict[str, Any]:
        """Cached airports/runways near the station; starts a background (re)build when needed."""
        with self._lock:
            if self._data is None:
                try:
                    with open(self.path, encoding="utf-8") as f:
                        self._data = json.load(f)
                except (OSError, ValueError):
                    self._data = {}
            d = self._data
            fresh = (d.get("station") == [round(lat0, 3), round(lon0, 3)] and time.time() - d.get("ts", 0) < MAX_AGE_S)
            if not fresh and not self._busy:
                self._busy = True
                threading.Thread(target=self._rebuild, args=(lat0, lon0), name="airports", daemon=True).start()
        return {"airports": d.get("airports", []), "runways": d.get("runways", []), "ready": bool(d.get("ts"))}

    def _rebuild(self, lat0: float, lon0: float) -> None:
        try:
            texts = []
            for name in ("airports.csv", "runways.csv"):
                req = urllib.request.Request(f"{BASE}/{name}", headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=60) as r:
                    texts.append(r.read().decode("utf-8", "replace"))
            d = build(texts[0], texts[1], lat0, lon0)
            d.update(station=[round(lat0, 3), round(lon0, 3)], ts=round(time.time()))
            os.makedirs(self.cache_dir, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(d, f, separators=(",", ":"))
            os.replace(tmp, self.path)
            with self._lock:
                self._data = d
        except Exception:
            pass  # offline: try again on the next request
        finally:
            with self._lock:
                self._busy = False
