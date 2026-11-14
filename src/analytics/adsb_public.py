"""
Public ADS-B network as a fallback aircraft source.

Order of use: the owner's own receiver (AdsbFeed) first; this feed only while the fallback switch is on and the
receiver is not delivering (not configured, not connected, or silent for more than RECEIVER_SILENT_S). Flightradar24
(src/analytics/fr24.py) is a separate optional source and is not touched here.

Providers (readsb JSON, `ac` list):
  adsb.fi  https://opendata.adsb.fi  - default, one request per POLL_S (limit 1 req/s; personal, non-commercial use;
                                       the data must be credited to adsb.fi with a link)
  adsb.lol https://api.adsb.lol      - failover after FAILOVER_AFTER failures of adsb.fi, one request per LOL_POLL_S
                                       (ODbL 1.0, 500 requests/day without a key), back to adsb.fi after RETURN_AFTER_S

Only the station position snapped to a 0.5 degree grid (about 55 km) is ever sent; the exact position stays local.
"""

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.analytics.adsb import FT, AircraftStore
from src.net import urlopen

logger = logging.getLogger("imx335.adsb_public")

RECEIVER_SILENT_S = 90.0     # a receiver with no message for this long is "not delivering"
RADIUS_NM = 150
POLL_S = 10.0                # adsb.fi
LOL_POLL_S = 180.0           # adsb.lol: 480 requests/day, under its 500/day limit
FAILOVER_AFTER = 3           # consecutive adsb.fi failures before adsb.lol is used
RETURN_AFTER_S = 600.0       # time on adsb.lol before adsb.fi is tried again
BACKOFF_MAX_S = 300.0
RETRY_AFTER_MAX_S = 900.0
TIMEOUT_S = 8.0
MAX_BYTES = 4 * 1024 * 1024
USER_AGENT = "zenivo-allsky"

PROVIDERS: Tuple[Dict[str, Any], ...] = (
    {"name": "adsb.fi", "url": "https://opendata.adsb.fi/api/v3/lat/{lat}/lon/{lon}/dist/{nm}", "poll_s": POLL_S,
     "link": "https://adsb.fi"},
    {"name": "adsb.lol", "url": "https://api.adsb.lol/v2/point/{lat}/{lon}/{nm}", "poll_s": LOL_POLL_S,
     "link": "https://adsb.lol"},
)


def snap_half_degree(value: float) -> float:
    """Latitude or longitude snapped to the 0.5 degree grid: all that leaves the station for public data services."""
    return round(float(value) * 2) / 2


def receiver_delivering(status: Optional[Dict[str, Any]]) -> bool:
    """True when the receiver feed is connected and has produced a message within RECEIVER_SILENT_S."""
    if not status or not status.get("connected"):
        return False
    age = status.get("last_message_age_s")
    return age is not None and age <= RECEIVER_SILENT_S


def choose_source(receiver_status: Optional[Dict[str, Any]], fallback_on: bool) -> str:
    """'receiver' | 'public' | 'none': which source the aircraft views should read right now.
    receiver_status is None when no receiver is configured."""
    if receiver_delivering(receiver_status):
        return "receiver"
    return "public" if fallback_on else "none"


def parse_readsb(payload: Any, now: float) -> List[Dict[str, Any]]:
    """readsb / tar1090 JSON (`{"ac": [...]}`) to position records; aircraft without a position or a valid
    ICAO hex are skipped. Altitude in metres ("ground" = 0), speed in knots, vertical rate in ft/min."""
    out: List[Dict[str, Any]] = []
    rows = payload.get("ac") if isinstance(payload, dict) else None
    for r in rows if isinstance(rows, list) else []:
        try:
            icao = str(r["hex"]).strip().upper()
            lat, lon = float(r["lat"]), float(r["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if len(icao) != 6 or any(c not in "0123456789ABCDEF" for c in icao) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue  # "~" prefixed (non-ICAO) addresses and garbage
        alt = r.get("alt_baro", r.get("alt_geom"))
        if alt == "ground":
            alt_m = 0.0
        else:
            try:
                alt_m = float(alt if alt is not None else r["alt_geom"]) * FT
            except (KeyError, TypeError, ValueError):
                alt_m = 0.0
        rec: Dict[str, Any] = {"icao": icao, "callsign": str(r.get("flight") or "").strip(), "lat": lat, "lon": lon,
                               "alt_m": alt_m, "t": now - _num(r.get("seen_pos"), _num(r.get("seen"), 0.0))}
        for key, src in (("speed_kt", "gs"), ("track_deg", "track"), ("vrate_fpm", "baro_rate")):
            v = _num(r.get(src), None)
            if v is not None:
                rec[key] = v
        out.append(rec)
    return out


def _num(v: Any, default: Optional[float]) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


class FetchError(OSError):
    """A failed request; retry_after_s is the server's Retry-After (429/503) when it sent one."""

    def __init__(self, message: str, retry_after_s: Optional[float] = None):
        super().__init__(message)
        self.retry_after_s = retry_after_s


def http_fetch(url: str) -> Any:
    """GET `url` as JSON with the shared TLS context, a short timeout and a size cap."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urlopen(req, timeout=TIMEOUT_S) as r:
            raw = r.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        ra = _num(exc.headers.get("Retry-After") if exc.headers else None, None)
        raise FetchError(f"HTTP {exc.code}", ra) from None
    if len(raw) > MAX_BYTES:
        raise FetchError("response is too large")
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        raise FetchError("response is not JSON") from None


class PublicAdsbFeed(AircraftStore):
    """Polls a public readsb-compatible API around the station. Same read interface as AdsbFeed
    (status / tracks / current). One thread; it polls only while wanted() and needed() are both true."""

    def __init__(self, station: Callable[[], Tuple[float, float]], wanted: Optional[Callable[[], bool]] = None,
                 needed: Optional[Callable[[], bool]] = None, fetch: Callable[[str], Any] = http_fetch,
                 clock: Callable[[], float] = time.time):
        super().__init__()
        self.station, self._fetch, self._clock = station, fetch, clock
        self.wanted = wanted or (lambda: True)
        self.needed = needed or (lambda: True)   # false while the receiver is delivering
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._provider = 0
        self._provider_since = 0.0
        self._fails = 0
        self.polls = 0
        self.last_ok_ts = 0.0
        self.last_error: Optional[str] = None

    # ------------------------------------------------------------------ thread
    def start(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="adsb-public", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def active(self) -> bool:
        """Poll right now? Needs the feed wanted, the fallback needed and a station position that is set."""
        try:
            lat, lon = self.station()
            return bool(self.wanted() and self.needed() and (float(lat) or float(lon)))
        except Exception:
            return False

    def _run(self) -> None:
        next_at = 0.0
        while not self._stop.is_set():
            try:
                if not self.active():
                    with self._lock:
                        self._ac.clear()   # stale public data must not outlive the need for it
                    next_at = 0.0
                    self._stop.wait(5.0)
                    continue
                now = self._clock()
                if now < next_at:
                    self._stop.wait(min(5.0, next_at - now))   # re-checks active() every 5 s: stops within one cycle
                    continue
                next_at = now + self.step()
            except Exception:  # never leave the thread
                logger.debug("public ADS-B loop error", exc_info=True)
                self._stop.wait(30.0)

    # ------------------------------------------------------------------ one request
    def step(self) -> float:
        """One poll of the current provider; returns the seconds to wait before the next one."""
        now = self._clock()
        if self._provider != 0 and now - self._provider_since >= RETURN_AFTER_S:
            self._provider, self._fails = 0, 0   # try adsb.fi again
        prov = PROVIDERS[self._provider]
        lat, lon = self.station()
        url = prov["url"].format(lat=f"{snap_half_degree(lat):.1f}", lon=f"{snap_half_degree(lon):.1f}", nm=RADIUS_NM)
        self.polls += 1
        try:
            recs = parse_readsb(self._fetch(url), now)
        except Exception as exc:
            return self._failed(exc, prov, now)
        self._ingest(recs, now)
        self._fails, self.last_error, self.last_ok_ts = 0, None, now
        return float(prov["poll_s"])

    def _failed(self, exc: Exception, prov: Dict[str, Any], now: float) -> float:
        self.last_error = f"{prov['name']}: {str(exc)[:100]}"
        self._fails += 1
        if self._provider == 0 and self._fails >= FAILOVER_AFTER:
            self._provider, self._provider_since, self._fails = 1, now, 0
            return 0.0   # first adsb.lol request right away, then one per LOL_POLL_S
        delay = min(BACKOFF_MAX_S, float(prov["poll_s"]) * 2 ** (self._fails - 1))
        ra = getattr(exc, "retry_after_s", None)
        if ra is not None:
            delay = min(RETRY_AFTER_MAX_S, max(delay, ra))
        return delay

    def _ingest(self, recs: List[Dict[str, Any]], now: float) -> None:
        with self._lock:
            for r in recs:
                ac = self._entry(r["icao"], now)
                if ac is None:
                    continue
                if r["callsign"]:
                    ac["callsign"] = r["callsign"]
                for k in ("speed_kt", "track_deg", "vrate_fpm"):
                    if k in r:
                        ac[k] = r[k]
                if not ac["pos"] or r["t"] > ac["pos"][-1][0]:
                    self._add_fix(ac, r["t"], r["lat"], r["lon"], r["alt_m"])
        self._prune(now)   # takes the lock itself

    # ------------------------------------------------------------------ read interface
    @property
    def provider(self) -> Dict[str, str]:
        p = PROVIDERS[self._provider]
        return {"name": p["name"], "link": p["link"]}

    def status(self) -> Dict[str, Any]:
        with self._lock:
            n = len(self._ac)
            with_pos = sum(1 for a in self._ac.values() if a["pos"])
        age = round(self._clock() - self.last_ok_ts, 1) if self.last_ok_ts else None
        return {"provider": self.provider["name"], "connected": age is not None and age < 2 * LOL_POLL_S + 60,
                "aircraft": n, "with_position": with_pos, "messages": self.polls, "last_error": self.last_error,
                "last_ok": self.last_ok_ts or None, "last_message_age_s": age}
