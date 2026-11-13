"""
Who is that aircraft? Type, registration, airline and route for the 3D flights map.

The SBS feed only gives the ICAO hex and callsign. A background thread looks each new hex up on hexdb.io
(type code, registration) and each callsign's route, at most a couple of requests per second and only for
aircraft someone is looking at; answers are cached on disk so a regular is never asked for twice. The
airline (IATA code for its logo, name) comes from the callsign's ICAO prefix via the OpenFlights airline
list, downloaded once.
"""

import csv
import io
import json
import os
import queue
import re
import threading
import time
import urllib.request

from src.net import urlopen as net_urlopen
from typing import Any, Dict, Optional, Tuple

HEXDB = "https://hexdb.io"
AIRLINES_URL = "https://raw.githubusercontent.com/jpatokal/openflights/master/data/airlines.dat"
from src.version import __version__
USER_AGENT = f"Zenivo/{__version__}"
ROUTE_TTL_S = 12 * 3600.0
MISS_TTL_S = 7 * 24 * 3600.0  # unknown hex: ask again after a week
CALLSIGN_RE = re.compile(r"^([A-Z]{3})\d")
# OpenFlights is stale for some carriers over Vietnam: missing, or without their IATA code
AIRLINE_OVERRIDES = {
    "VJC": ("VJ", "VietJet Air"), "BAV": ("QH", "Bamboo Airways"), "VAG": ("VU", "Vietravel Airlines"),
    "PIC": ("BL", "Pacific Airlines"), "VFC": ("0V", "VASCO"), "HVN": ("VN", "Vietnam Airlines"),
}


def _get(url: str, timeout: float = 8.0) -> Optional[bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with net_urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception:
        return None


def parse_airlines(text: str) -> Dict[str, Tuple[str, str]]:
    """OpenFlights airlines.dat -> {ICAO: (IATA, name)}, active airlines winning over defunct ones."""
    out: Dict[str, Tuple[str, str]] = {}
    active: Dict[str, bool] = {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 8:
            continue
        name, iata, icao, is_active = row[1], row[3].strip(), row[4].strip().upper(), row[7] == "Y"
        if len(icao) != 3 or not icao.isalpha() or iata in ("", "-", "\\N") or len(iata) != 2:
            continue
        if icao not in out or (is_active and not active[icao]):
            out[icao], active[icao] = (iata, name), is_active
    return out


class AircraftInfo:
    def __init__(self, cache_dir: str):
        self.cache_dir = cache_dir
        self.db_path = os.path.join(cache_dir, "aircraft_db.json")
        self.airlines_path = os.path.join(cache_dir, "airlines.dat")
        self._lock = threading.Lock()
        self._db: Dict[str, Dict[str, Any]] = {"hex": {}, "route": {}}
        self._airlines: Optional[Dict[str, Tuple[str, str]]] = None
        self._queue: "queue.Queue[Tuple[str, str]]" = queue.Queue(maxsize=500)
        self._queued: set = set()
        self._dirty = False
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        try:
            with open(self.db_path, encoding="utf-8") as f:
                d = json.load(f)
            self._db["hex"].update(d.get("hex") or {})
            self._db["route"].update(d.get("route") or {})
        except (OSError, ValueError):
            pass

    # ------------------------------------------------------------------
    def describe(self, icao_hex: str, callsign: Optional[str]) -> Dict[str, Any]:
        """What is known now about an aircraft; unknown parts are queued for lookup."""
        now = time.time()
        out: Dict[str, Any] = {}
        hx = icao_hex.upper()
        with self._lock:
            rec = self._db["hex"].get(hx)
            route = self._db["route"].get(callsign) if callsign else None
        if rec is None or (not rec.get("type") and now - rec.get("ts", 0) > MISS_TTL_S):
            self._enqueue("hex", hx)
        elif rec.get("type"):
            out["type"] = rec["type"]
            if rec.get("reg"):
                out["reg"] = rec["reg"]
            if rec.get("model"):
                out["model"] = rec["model"]
        m = CALLSIGN_RE.match(callsign or "")
        if m:
            al = self._airline(m.group(1))
            if al:
                out["airline_iata"], out["airline"] = al
            if route is None or now - route.get("ts", 0) > ROUTE_TTL_S:
                self._enqueue("route", callsign)
            elif route.get("route"):
                out["route"] = route["route"]
        return out

    def _airline(self, icao3: str) -> Optional[Tuple[str, str]]:
        if icao3 in AIRLINE_OVERRIDES:
            return AIRLINE_OVERRIDES[icao3]
        if self._airlines is None:
            self._enqueue("airlines", "")
            return None
        return self._airlines.get(icao3)

    def _enqueue(self, kind: str, key: str) -> None:
        with self._lock:
            if (kind, key) in self._queued:
                return
            self._queued.add((kind, key))
        try:
            self._queue.put_nowait((kind, key))
        except queue.Full:
            with self._lock:
                self._queued.discard((kind, key))
            return
        self._ensure_thread()

    def _ensure_thread(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="aircraft-info", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._save()

    # ------------------------------------------------------------------
    def _run(self) -> None:
        last_save = time.time()
        while not self._stop.is_set():
            try:
                kind, key = self._queue.get(timeout=30.0)
            except queue.Empty:
                self._save()
                return  # idle: the thread is restarted by the next lookup
            try:
                if kind == "airlines":
                    self._load_airlines()
                elif kind == "hex":
                    self._lookup_hex(key)
                elif kind == "route":
                    self._lookup_route(key)
            finally:
                with self._lock:
                    self._queued.discard((kind, key))
            if time.time() - last_save > 60.0:
                self._save()
                last_save = time.time()
            self._stop.wait(0.5)  # be gentle with hexdb.io

    def _load_airlines(self) -> None:
        text = None
        try:
            with open(self.airlines_path, encoding="utf-8") as f:
                text = f.read()
        except OSError:
            raw = _get(AIRLINES_URL, timeout=20.0)
            if raw:
                text = raw.decode("utf-8", "replace")
                try:
                    os.makedirs(self.cache_dir, exist_ok=True)
                    with open(self.airlines_path, "w", encoding="utf-8") as f:
                        f.write(text)
                except OSError:
                    pass
        self._airlines = parse_airlines(text) if text else {}

    def _lookup_hex(self, hx: str) -> None:
        raw = _get(f"{HEXDB}/api/v1/aircraft/{hx}")
        rec: Dict[str, Any] = {"ts": round(time.time())}
        if raw:
            try:
                d = json.loads(raw)
                rec.update(type=(d.get("ICAOTypeCode") or "").upper(), reg=d.get("Registration") or "",
                           model=" ".join(x for x in (d.get("Manufacturer"), d.get("Type")) if x))
            except ValueError:
                pass
        elif raw is None:
            rec["ts"] -= MISS_TTL_S - 3600  # network trouble: retry in an hour, not a week
        with self._lock:
            self._db["hex"][hx] = rec
            self._dirty = True

    def _lookup_route(self, callsign: str) -> None:
        raw = _get(f"{HEXDB}/api/v1/route/icao/{callsign}")
        route = ""
        if raw:
            try:
                route = (json.loads(raw).get("route") or "").strip()
            except ValueError:
                pass
        with self._lock:
            self._db["route"][callsign] = {"route": route, "ts": round(time.time())}
            self._dirty = True

    def _save(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            data = json.dumps(self._db, separators=(",", ":"))
            self._dirty = False
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            tmp = self.db_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(data)
            os.replace(tmp, self.db_path)
        except OSError:
            pass
