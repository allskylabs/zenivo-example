"""
Flightradar24 Global & Enterprise live flight feed integration.

Combines global Flightradar24 live zone feeds (and optional Enterprise account authentication / subscriptionKey)
with the local SBS-1 ADS-B receiver so the 3D Globe displays real-time aircraft worldwide while preserving
sub-second local antenna precision around the observatory.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import deque
import gzip
import http.cookiejar
import json
import logging
import time
import threading
import urllib.error
import urllib.parse
import urllib.request

from src.net import https_handler, pooled_get
from typing import Any, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

FT = 0.3048
KT_KMH = 1.852
FEED_URL = "https://data-cloud.flightradar24.com/zones/fcgi/feed.js"
CLICK_URL = "https://data-live.flightradar24.com/clickhandler/"
LOGIN_URL = "https://www.flightradar24.com/user/login"

FR24_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip",
    "Origin": "https://www.flightradar24.com",
    "Referer": "https://www.flightradar24.com/",
}

# Complementary world zones for full-globe 3D view (max 1,500 aircraft per zone -> up to 6,000 global)
GLOBAL_ZONES = [
    ("asia_pac", "58,-45,65,180"),
    ("eur_mea", "72,-38,-25,65"),
    ("americas", "72,-58,-175,-25"),
]


def _http_json(url: str, timeout: float = 8.0, data: Optional[bytes] = None,
               extra_headers: Optional[Dict[str, str]] = None,
               cookie_jar: Optional[http.cookiejar.CookieJar] = None) -> Tuple[Dict[str, Any], Dict[str, str]]:
    headers = dict(FR24_HEADERS)
    if extra_headers:
        headers.update(extra_headers)
    if cookie_jar is None and data is None:
        # hot path (feed / click-handler GETs): shared SSL context + keep-alive pool
        _, raw, _h = pooled_get(url, headers, timeout)
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        return json.loads(raw.decode("utf-8", "replace")), {}
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
    handlers = [https_handler()]
    if cookie_jar is not None:
        handlers.append(urllib.request.HTTPCookieProcessor(cookie_jar))
    opener = urllib.request.build_opener(*handlers)
    with opener.open(req, timeout=timeout) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding", "").lower() == "gzip" or raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        cookies = {}
        if cookie_jar is not None:
            for c in cookie_jar:
                cookies[c.name] = c.value
        return json.loads(raw.decode("utf-8", "replace")), cookies


class Fr24Feed:
    """Thread-safe Flightradar24 global & regional live aircraft fetcher with Enterprise login support."""

    TRAIL_KEEP_S = 900.0
    TRAIL_STEP_S = 5.0
    MAX_TRAIL_AC = 3500
    VIEWPORT_TTL_S = 2.8
    GLOBAL_TTL_S = 4.5
    DETAIL_TTL_S = 15.0

    def __init__(self, enabled: bool = True, email: str = "", password: str = "", token: str = ""):
        self.enabled = bool(enabled)
        self.email = (email or "").strip()
        self.password = password or ""
        self.token = (token or "").strip()
        self.subscription_key: str = self.token
        self.account_tier: str = "enterprise" if self.token else "public"
        self.logged_in: bool = bool(self.token)
        self.last_error: Optional[str] = None
        self.last_fetch_ts: float = 0.0
        self.global_total: int = 0
        self._lock = threading.Lock()
        self._refreshing: set = set()
        self._cache: Dict[str, Tuple[float, List[Dict[str, Any]], int]] = {}
        self._detail_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._trails: Dict[str, Deque[Tuple[float, float, float, float]]] = {}
        self._full_trails: Dict[str, Tuple[float, List[List[float]]]] = {}

        if self.email and self.password and not self.token:
            threading.Thread(target=self._safe_login, name="fr24-login", daemon=True).start()

    def _safe_login(self) -> None:
        try:
            self.login(self.email, self.password)
        except Exception as exc:
            logger.error("[Analytics:Fr24Feed] FR24 background login failed: %s", exc, exc_info=True)
            self.last_error = str(exc)

    def login(self, email: str, password: str) -> Dict[str, Any]:
        """Authenticate with Flightradar24 account (Enterprise/Business/Gold) to obtain subscriptionKey/_frPl."""
        jar = http.cookiejar.CookieJar()
        form = urllib.parse.urlencode({
            "email": email.strip(),
            "password": password,
            "remember": "true",
            "type": "web",
        }).encode("utf-8")
        try:
            res, cookies = _http_json(
                LOGIN_URL,
                timeout=10.0,
                data=form,
                extra_headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
                cookie_jar=jar,
            )
        except Exception as exc:
            logger.error("[Analytics:Fr24Feed] FR24 login request error: %s", exc, exc_info=True)
            self.last_error = f"Login failed: {exc}"
            raise ValueError(self.last_error) from exc

        user_data = res.get("userData") or {}
        sub_key = cookies.get("_frPl") or user_data.get("subscriptionKey") or user_data.get("accessToken") or ""
        if not res.get("success") and not sub_key:
            msg = res.get("message") or "Invalid Flightradar24 email or password"
            self.last_error = str(msg)
            raise ValueError(self.last_error)

        with self._lock:
            self.email = email.strip()
            self.password = password
            self.subscription_key = str(sub_key)
            self.token = self.subscription_key
            self.logged_in = True
            self.account_tier = "enterprise"
            self.last_error = None
            self._cache.clear()
        return self.status()

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "logged_in": self.logged_in,
            "authenticated": self.logged_in,
            "email": self.email,
            "has_token": bool(self.subscription_key),
            "account_tier": "enterprise" if self.logged_in else "public",
            "global_total": self.global_total,
            "last_fetch_age_s": round(time.time() - self.last_fetch_ts, 1) if self.last_fetch_ts else None,
            "last_error": self.last_error,
        }

    # ------------------------------------------------------------------
    def _parse_row(self, k: str, v: list, now: float, trail_s: float = 0.0) -> Optional[Dict[str, Any]]:
        if not isinstance(v, list) or len(v) < 14:
            return None
        try:
            lat = float(v[1])
            lon = float(v[2])
            trk = float(v[3] or 0)
            alt_ft = float(v[4] or 0)
            spd_kt = float(v[5] or 0)
        except (TypeError, ValueError):
            return None
        if bool(v[14]) and alt_ft <= 0 and spd_kt < 15:
            return None  # parked on ground
        raw_hex = str(v[0] or "").strip().upper()
        icao = raw_hex if (len(raw_hex) == 6 and raw_hex != "000000") else f"FR{k.upper()}"
        ac_type = str(v[8] or "").strip().upper()
        reg = str(v[9] or "").strip().upper()
        fix_ts = float(v[10] or now)
        age_s = max(0.0, min(60.0, now - fix_ts))
        orig = str(v[11] or "").strip().upper()
        dest = str(v[12] or "").strip().upper()
        fl_iata = str(v[13] or "").strip().upper()
        airline_iata = fl_iata[:2] if (len(fl_iata) >= 3 and fl_iata[:2].isalnum() and fl_iata[2].isdigit()) else ""
        vrate_fpm = float(v[15] or 0) if len(v) > 15 and v[15] not in (None, "") else 0.0
        callsign = str((v[16] if len(v) > 16 else "") or fl_iata or "").strip().upper()
        airline_icao = str((v[18] if len(v) > 18 else "") or "").strip().upper()
        route = f"{orig}-{dest}" if (orig and dest) else (orig or dest or "")
        alt_m = alt_ft * FT
        rec: Dict[str, Any] = {
            "fr24_id": k,
            "icao": icao,
            "callsign": callsign,
            "lat": lat,
            "lon": lon,
            "altitude_m": alt_m,
            "speed_kt": spd_kt,
            " speed_kmh": round(spd_kt * KT_KMH),
            "speed_kmh": round(spd_kt * KT_KMH),
            "track_deg": trk,
            "heading_deg": round(trk) % 360,
            "vrate_fpm": vrate_fpm,
            "climb_m_min": round(vrate_fpm * FT),
            "age_s": age_s,
            "type": ac_type,
            "reg": reg,
            "route": route,
            "airline_iata": airline_iata,
            "airline_icao": airline_icao,
            "radar": str(v[7] or ""),
        }
        rec.pop(" speed_kmh", None)
        if trail_s > 0:
            rec["trail"] = [[round(lat, 5), round(lon, 5), round(alt_m), round(age_s, 1)]]
        return rec

    def _fetch_zone(self, bounds: str) -> Tuple[List[Dict[str, Any]], int]:
        params: Dict[str, Any] = {
            "bounds": bounds,
            "faa": "1",
            "satellite": "1",
            "mlat": "1",
            "flarm": "1",
            "adsb": "1",
            "gnd": "0",
            "air": "1",
            "vehicles": "0",
            "estimated": "1",
            "maxage": "14400",
            "gliders": "0",
            "stats": "0",
            "limit": "1500",
        }
        if self.subscription_key:
            params["enc"] = self.subscription_key
        url = f"{FEED_URL}?{urllib.parse.urlencode(params)}"
        data, _ = _http_json(url, timeout=7.5)
        full_count = int(data.get("full_count") or 0)
        now = time.time()
        flights: List[Dict[str, Any]] = []
        for k, v in data.items():
            parsed = self._parse_row(k, v, now)
            if parsed is not None:
                flights.append(parsed)
        return flights, full_count

    def _do_fetch(self, q_bounds: str, is_global: bool, station_lat: float, station_lon: float) -> Tuple[List[Dict[str, Any]], int]:
        now = time.time()
        if is_global:
            local_box = f"{min(85.0, round(station_lat + 14, 1))},{max(-85.0, round(station_lat - 14, 1))},{max(-180.0, round(station_lon - 18, 1))},{min(180.0, round(station_lon + 18, 1))}"
            zones = [("local", local_box)] + GLOBAL_ZONES
            merged: Dict[str, Dict[str, Any]] = {}
            max_total = 0
            with ThreadPoolExecutor(max_workers=4) as pool:
                futs = {pool.submit(self._fetch_zone, b): name for name, b in zones}
                for fut in as_completed(futs, timeout=9.0):
                    try:
                        flist, cnt = fut.result()
                        max_total = max(max_total, cnt)
                        for f in flist:
                            if f["icao"] not in merged:
                                merged[f["icao"]] = f
                    except Exception as z_err:
                        logger.error("[Analytics:Fr24Feed] Zone fetch error (%s): %s", futs[fut], z_err)
            flights = list(merged.values())
            full_count = max_total
        else:
            flights, full_count = self._fetch_zone(q_bounds)

        with self._lock:
            self.last_fetch_ts = now
            self.last_error = None
            if full_count > 0:
                self.global_total = full_count
            self._update_trails_locked(flights, now, station_lat, station_lon, is_global)
            self._cache[q_bounds] = (now, flights, self.global_total)
            return flights, self.global_total

    def _refresh_bg(self, q_bounds: str, is_global: bool, station_lat: float, station_lon: float) -> None:
        try:
            self._do_fetch(q_bounds, is_global, station_lat, station_lon)
        except Exception as exc:
            logger.error("[Analytics:Fr24Feed] Background FR24 refresh error for %s: %s", q_bounds, exc)
            with self._lock:
                self.last_error = str(exc)
        finally:
            with self._lock:
                self._refreshing.discard(q_bounds)

    def current(self, bounds: Optional[str] = None, station_lat: float = 21.0538,
                station_lon: float = 105.9095, trail_s: float = 0.0) -> Tuple[List[Dict[str, Any]], int]:
        """Return live FR24 aircraft for `bounds` ('north,south,west,east' or 'global') plus global_total."""
        if not self.enabled:
            return [], 0

        is_global = not bounds or bounds == "global"
        q_bounds = "global"
        if not is_global and bounds:
            try:
                n, s, w, e = [float(x) for x in bounds.split(",")[:4]]
                n = max(-85.0, min(85.0, n))
                s = max(-85.0, min(85.0, s))
                w = max(-180.0, min(180.0, w))
                e = max(-180.0, min(180.0, e))
                if n < s:
                    n, s = s, n
                if (n - s) > 75.0 or (e - w) > 135.0:
                    is_global = True
                else:
                    q_bounds = f"{round(n, 1)},{round(s, 1)},{round(w, 1)},{round(e, 1)}"
            except ValueError:
                is_global = True

        ttl = self.GLOBAL_TTL_S if is_global else self.VIEWPORT_TTL_S
        now = time.time()
        with self._lock:
            cached = self._cache.get(q_bounds)
            if cached:
                age = now - cached[0]
                if age < ttl:
                    return self._attach_trails(cached[1], trail_s, now, is_global, station_lat, station_lon), cached[2]
                if age < 90.0:
                    if q_bounds not in self._refreshing:
                        self._refreshing.add(q_bounds)
                        threading.Thread(
                            target=self._refresh_bg,
                            args=(q_bounds, is_global, station_lat, station_lon),
                            name=f"fr24-bg-{q_bounds[:12]}",
                            daemon=True,
                        ).start()
                    return self._attach_trails(cached[1], trail_s, now, is_global, station_lat, station_lon), cached[2]

        try:
            flights, total = self._do_fetch(q_bounds, is_global, station_lat, station_lon)
            with self._lock:
                return self._attach_trails(flights, trail_s, now, is_global, station_lat, station_lon), total
        except Exception as exc:
            logger.error("[Analytics:Fr24Feed] FR24 feed error for %s: %s", q_bounds, exc, exc_info=True)
            with self._lock:
                self.last_error = str(exc)
                if cached:
                    return self._attach_trails(cached[1], trail_s, now, is_global, station_lat, station_lon), cached[2]
            return [], self.global_total

    def _update_trails_locked(self, flights: List[Dict[str, Any]], now: float,
                              station_lat: float = 21.0538, station_lon: float = 105.9095,
                              is_global: bool = False) -> None:
        for f in flights:
            if is_global and (abs(f["lat"] - station_lat) > 10.0 or abs(f["lon"] - station_lon) > 12.0):
                continue
            icao = f["icao"]
            q = self._trails.get(icao)
            if q is None:
                if len(self._trails) >= self.MAX_TRAIL_AC:
                    oldest = min(self._trails.items(), key=lambda kv: kv[1][-1][0] if kv[1] else 0)[0]
                    self._trails.pop(oldest, None)
                q = self._trails[icao] = deque(maxlen=int(self.TRAIL_KEEP_S / self.TRAIL_STEP_S) + 8)
            if not q or now - q[-1][0] >= self.TRAIL_STEP_S:
                q.append((now, f["lat"], f["lon"], f["altitude_m"]))
            while q and now - q[0][0] > self.TRAIL_KEEP_S:
                q.popleft()

    def _attach_trails(self, flights: List[Dict[str, Any]], trail_s: float, now: float,
                       is_global: bool = False, station_lat: float = 21.0538,
                       station_lon: float = 105.9095) -> List[Dict[str, Any]]:
        if trail_s <= 0:
            return flights
        out: List[Dict[str, Any]] = []
        for f in flights:
            icao = f["icao"]
            if is_global and icao not in self._full_trails and (abs(f["lat"] - station_lat) > 6.0 or abs(f["lon"] - station_lon) > 6.5):
                out.append(f)
                continue
            rec = dict(f)
            full = self._full_trails.get(icao)
            if full and now - full[0] < 120.0:
                rec["trail"] = full[1] + [[round(f["lat"], 5), round(f["lon"], 5), round(f["altitude_m"]), 0.0]]
            else:
                q = self._trails.get(icao)
                if q and len(q) >= 2:
                    rec["trail"] = [
                        [round(lat, 5), round(lon, 5), round(alt), round(now - pt, 1)]
                        for pt, lat, lon, alt in q
                        if now - pt <= trail_s
                    ]
                else:
                    rec["trail"] = [[round(f["lat"], 5), round(f["lon"], 5), round(f["altitude_m"]), 0.0]]
            out.append(rec)
        return out

    # ------------------------------------------------------------------
    def flight_detail(self, fr24_id: str) -> Dict[str, Any]:
        """Fetch full Flightradar24 flight details + complete historical trail from departure airport."""
        fid = (fr24_id or "").strip()
        if not fid or not fid.isalnum():
            raise ValueError("Invalid Flightradar24 flight ID")
        now = time.time()
        with self._lock:
            cached = self._detail_cache.get(fid)
            if cached and now - cached[0] < self.DETAIL_TTL_S:
                return cached[1]

        params: Dict[str, str] = {"version": "1.5", "flight": fid}
        if self.subscription_key:
            params["enc"] = self.subscription_key
        url = f"{CLICK_URL}?{urllib.parse.urlencode(params)}"
        try:
            d, _ = _http_json(url, timeout=8.0)
        except Exception as exc:
            logger.error("[Analytics:Fr24Feed] clickhandler failed for %s: %s", fid, exc, exc_info=True)
            raise ValueError(f"Flightradar24 detail lookup failed: {exc}") from exc

        ac = d.get("aircraft") or {}
        model = ac.get("model") or {}
        airline = d.get("airline") or {}
        al_code = airline.get("code") or {}
        airport = d.get("airport") or {}
        orig = airport.get("origin") or {}
        dest = airport.get("destination") or {}
        orig_code = (orig.get("code") or {}).get("iata") or (orig.get("code") or {}).get("icao") or ""
        dest_code = (dest.get("code") or {}).get("iata") or (dest.get("code") or {}).get("icao") or ""
        orig_city = ((orig.get("position") or {}).get("region") or {}).get("city") or orig.get("name") or ""
        dest_city = ((dest.get("position") or {}).get("region") or {}).get("city") or dest.get("name") or ""

        imgs = ac.get("images") or {}
        img_list = imgs.get("medium") or imgs.get("thumbnails") or imgs.get("large") or []
        photo = img_list[0] if img_list and isinstance(img_list[0], dict) else {}

        # Convert clickhandler trail (newest-first) to oldest-first [[lat, lon, alt_m, age_s], ...]
        raw_trail = [p for p in (d.get("trail") or []) if isinstance(p, dict) and p.get("lat") is not None and p.get("lng") is not None]
        raw_trail.reverse()
        step = max(1, len(raw_trail) // 320)
        sampled = raw_trail[::step]
        if raw_trail and (not sampled or sampled[-1] is not raw_trail[-1]):
            sampled.append(raw_trail[-1])

        trail = [
            [
                round(float(p["lat"]), 5),
                round(float(p["lng"]), 5),
                round(float(p.get("alt") or 0) * FT),
                round(max(0.0, now - float(p.get("ts") or now)), 1),
            ]
            for p in sampled
        ]

        icao = str(ac.get("hex") or "").strip().upper()
        result = {
            "fr24_id": fid,
            "icao": icao,
            "callsign": str((d.get("identification") or {}).get("callsign") or "").strip().upper(),
            "flight_number": str(((d.get("identification") or {}).get("number") or {}).get("default") or "").strip().upper(),
            "type": str(model.get("code") or "").strip().upper(),
            "model": str(model.get("text") or "").strip(),
            "reg": str(ac.get("registration") or "").strip().upper(),
            "airline": str(airline.get("name") or airline.get("short") or "").strip(),
            "airline_iata": str(al_code.get("iata") or "").strip().upper(),
            "airline_icao": str(al_code.get("icao") or "").strip().upper(),
            "route": f"{orig_code}-{dest_code}" if (orig_code and dest_code) else (orig_code or dest_code or ""),
            "origin": {"code": orig_code, "name": orig.get("name") or "", "city": orig_city},
            "destination": {"code": dest_code, "name": dest.get("name") or "", "city": dest_city},
            "photo_url": photo.get("src") or "",
            "photo_link": photo.get("link") or "",
            "photo_credit": photo.get("copyright") or "",
            "trail": trail,
        }
        with self._lock:
            self._detail_cache[fid] = (now, result)
            if icao and trail:
                self._full_trails[icao] = (now, trail)
        return result
