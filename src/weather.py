"""Weather around the station: current conditions and a 48 h forecast from Open-Meteo (free, no account or key).

Off by default. When on, one request per refresh goes to api.open-meteo.com carrying only the station's rounded
latitude/longitude (0.1 degree, about 11 km), never a name, address or picture. The answer is cached in
data/cache/weather.json so a restart does not refetch.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from src import net

HOST = "api.open-meteo.com"
URL = (f"https://{HOST}/v1/forecast?latitude={{lat}}&longitude={{lon}}&timezone=UTC&forecast_days=3&wind_speed_unit=ms"
       "&current=temperature_2m,relative_humidity_2m,dew_point_2m,cloud_cover,precipitation,wind_speed_10m,wind_gusts_10m,"
       "wind_direction_10m,surface_pressure,visibility,weather_code,is_day"
       "&hourly=temperature_2m,cloud_cover,precipitation_probability,precipitation,wind_speed_10m,visibility,weather_code")
FALLBACK_HOST = "api.met.no"
FALLBACK_URL = f"https://{FALLBACK_HOST}/weatherapi/locationforecast/2.0/compact?lat={{lat}}&lon={{lon}}"
UA = "Zenivo/1.3 (+https://github.com/hoanb1/zenivo)"   # MET Norway asks every client to identify itself
REFRESH_CHOICES_MIN = (15, 30, 60, 180)
DEFAULTS = {"enabled": 0, "refresh_min": 30}
HOURS = 48
# WMO weather interpretation codes, grouped as Open-Meteo documents them
_WMO = ((0, "Clear"), (1, "Mostly clear"), (2, "Partly cloudy"), (3, "Overcast"), (45, "Fog"), (48, "Rime fog"),
        (51, "Light drizzle"), (53, "Drizzle"), (55, "Heavy drizzle"), (56, "Freezing drizzle"), (57, "Freezing drizzle"),
        (61, "Light rain"), (63, "Rain"), (65, "Heavy rain"), (66, "Freezing rain"), (67, "Freezing rain"),
        (71, "Light snow"), (73, "Snow"), (75, "Heavy snow"), (77, "Snow grains"), (80, "Rain showers"),
        (81, "Rain showers"), (82, "Violent showers"), (85, "Snow showers"), (86, "Snow showers"),
        (95, "Thunderstorm"), (96, "Thunderstorm, hail"), (99, "Thunderstorm, hail"))
_WMO_MAP = dict(_WMO)


def describe(code: Any) -> str:
    try:
        return _WMO_MAP.get(int(code), "")
    except (TypeError, ValueError):
        return ""


def _num(v: Any, nd: int = 1) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f, nd) if f == f else None


def parse(payload: Dict[str, Any], now: Optional[float] = None) -> Dict[str, Any]:
    """Open-Meteo answer -> {current, hourly (from now, up to 48 h), outlook}. Raises ValueError on a bad answer."""
    cur = payload.get("current")
    hr = payload.get("hourly")
    if not isinstance(cur, dict) or not isinstance(hr, dict) or not isinstance(hr.get("time"), list):
        raise ValueError("unexpected answer from the weather service")
    now = time.time() if now is None else now
    from datetime import datetime, timezone

    def ts(s: str) -> float:
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp()

    current = {
        "temp_c": _num(cur.get("temperature_2m")), "humidity_pct": _num(cur.get("relative_humidity_2m"), 0),
        "dew_point_c": _num(cur.get("dew_point_2m")), "cloud_pct": _num(cur.get("cloud_cover"), 0),
        "precip_mm": _num(cur.get("precipitation")), "wind_ms": _num(cur.get("wind_speed_10m")),
        "gust_ms": _num(cur.get("wind_gusts_10m")), "wind_dir_deg": _num(cur.get("wind_direction_10m"), 0),
        "pressure_hpa": _num(cur.get("surface_pressure"), 0), "visibility_m": _num(cur.get("visibility"), 0),
        "code": cur.get("weather_code"), "summary": describe(cur.get("weather_code")), "is_day": bool(cur.get("is_day")),
    }
    keys = ("temperature_2m", "cloud_cover", "precipitation_probability", "precipitation", "wind_speed_10m", "visibility", "weather_code")
    hourly: List[Dict[str, Any]] = []
    for i, t in enumerate(hr["time"]):
        try:
            t_s = ts(t)
        except (TypeError, ValueError):
            continue
        if t_s < now - 3600:
            continue
        row = {"t": t_s}
        for k in keys:
            arr = hr.get(k)
            row[k] = arr[i] if isinstance(arr, list) and i < len(arr) else None
        hourly.append({"t": t_s, "temp_c": _num(row["temperature_2m"]), "cloud_pct": _num(row["cloud_cover"], 0),
                       "precip_prob_pct": _num(row["precipitation_probability"], 0), "precip_mm": _num(row["precipitation"]),
                       "wind_ms": _num(row["wind_speed_10m"]), "visibility_m": _num(row["visibility"], 0),
                       "summary": describe(row["weather_code"])})
        if len(hourly) >= HOURS:
            break
    nxt = [h for h in hourly[:12] if h["cloud_pct"] is not None]
    outlook = {"clear_hours_next_12": sum(1 for h in nxt if h["cloud_pct"] <= 30 and (h["precip_prob_pct"] or 0) < 30),
               "mean_cloud_next_12_pct": round(sum(h["cloud_pct"] for h in nxt) / len(nxt)) if nxt else None,
               "rain_expected_next_12": any((h["precip_prob_pct"] or 0) >= 50 or (h["precip_mm"] or 0) >= 0.3 for h in hourly[:12])}
    return {"current": current, "hourly": hourly, "outlook": outlook}


def parse_metno(payload: Dict[str, Any], now: Optional[float] = None) -> Dict[str, Any]:
    """MET Norway locationforecast (compact) -> the same shape as parse(). It has no visibility or rain probability."""
    from datetime import datetime, timezone
    series = ((payload.get("properties") or {}).get("timeseries")) if isinstance(payload, dict) else None
    if not isinstance(series, list) or not series:
        raise ValueError("unexpected answer from the weather service")
    now = time.time() if now is None else now

    def ts(s: str) -> float:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()

    def summary(entry: Dict[str, Any]) -> str:
        sym = ((entry.get("data") or {}).get("next_1_hours") or (entry.get("data") or {}).get("next_6_hours") or {}).get("summary", {}).get("symbol_code", "")
        return sym.split("_")[0].replace("lightrain", "light rain").replace("heavyrain", "heavy rain").replace("partlycloudy", "partly cloudy").capitalize() if sym else ""

    hourly: List[Dict[str, Any]] = []
    first = None
    for e in series:
        try:
            t = ts(e["time"])
        except (KeyError, TypeError, ValueError):
            continue
        if t < now - 3600:
            continue
        d = (e.get("data") or {}).get("instant", {}).get("details", {})
        nxt = (e.get("data") or {}).get("next_1_hours") or {}
        first = first or (e, d)
        hourly.append({"t": t, "temp_c": _num(d.get("air_temperature")), "cloud_pct": _num(d.get("cloud_area_fraction"), 0),
                       "precip_prob_pct": None, "precip_mm": _num((nxt.get("details") or {}).get("precipitation_amount")),
                       "wind_ms": _num(d.get("wind_speed")), "visibility_m": None, "summary": summary(e)})
        if len(hourly) >= HOURS:
            break
    if not first:
        raise ValueError("no forecast hours in the answer")
    e0, d0 = first
    current = {"temp_c": _num(d0.get("air_temperature")), "humidity_pct": _num(d0.get("relative_humidity"), 0),
               "dew_point_c": _num(d0.get("dew_point_temperature")), "cloud_pct": _num(d0.get("cloud_area_fraction"), 0),
               "precip_mm": hourly[0]["precip_mm"], "wind_ms": _num(d0.get("wind_speed")), "gust_ms": None,
               "wind_dir_deg": _num(d0.get("wind_from_direction"), 0), "pressure_hpa": _num(d0.get("air_pressure_at_sea_level"), 0),
               "visibility_m": None, "code": None, "summary": summary(e0), "is_day": None}
    nxt12 = [h for h in hourly[:12] if h["cloud_pct"] is not None]
    outlook = {"clear_hours_next_12": sum(1 for h in nxt12 if h["cloud_pct"] <= 30 and (h["precip_mm"] or 0) < 0.3),
               "mean_cloud_next_12_pct": round(sum(h["cloud_pct"] for h in nxt12) / len(nxt12)) if nxt12 else None,
               "rain_expected_next_12": any((h["precip_mm"] or 0) >= 0.3 for h in hourly[:12])}
    return {"current": current, "hourly": hourly, "outlook": outlook}


class WeatherManager:
    def __init__(self, data_dir: str, get_settings: Callable[[], Dict[str, Any]], save_settings: Callable[[Dict[str, Any]], None],
                 get_position: Callable[[], Optional[tuple]]):
        self.path = os.path.join(data_dir, "cache", "weather.json")
        self._get, self._save, self._pos = get_settings, save_settings, get_position
        self.lock = threading.Lock()
        self.data: Optional[Dict[str, Any]] = None
        self.fetched_at = 0.0
        self.error = ""
        self.fetching = False
        self.provider = HOST
        try:
            with open(self.path, encoding="utf-8") as f:
                saved = json.load(f)
            self.data, self.fetched_at = saved.get("data"), float(saved.get("fetched_at") or 0)
            self.provider = saved.get("provider") or HOST
        except (OSError, ValueError):
            pass

    def settings(self) -> Dict[str, Any]:
        cfg = dict(DEFAULTS)
        cfg.update(self._get() or {})
        return cfg

    def set_settings(self, enabled: Optional[bool] = None, refresh_min: Optional[int] = None) -> None:
        cfg = self.settings()
        if enabled is not None:
            cfg["enabled"] = int(bool(enabled))
        if refresh_min is not None:
            if int(refresh_min) not in REFRESH_CHOICES_MIN:
                raise ValueError(f"refresh_min must be one of {list(REFRESH_CHOICES_MIN)}")
            cfg["refresh_min"] = int(refresh_min)
        self._save({"enabled": cfg["enabled"], "refresh_min": cfg["refresh_min"]})
        if cfg["enabled"]:
            self.refresh_async()

    def status(self) -> Dict[str, Any]:
        cfg = self.settings()
        out: Dict[str, Any] = {"enabled": bool(cfg["enabled"]), "refresh_min": cfg["refresh_min"], "provider": "MET Norway" if self.provider == FALLBACK_HOST else "Open-Meteo",
                               "host": self.provider, "refresh_choices_min": list(REFRESH_CHOICES_MIN), "fetching": self.fetching,
                               "fetched_at": self.fetched_at or None, "error": self.error or None, "available": False}
        if cfg["enabled"] and self.data:
            out.update(self.data)
            out["available"] = True
            out["age_s"] = round(time.time() - self.fetched_at)
        return out

    def refresh(self) -> bool:
        pos = self._pos()
        if not pos:
            self.error = "Set the station latitude and longitude first"
            return False
        lat, lon = round(float(pos[0]), 1), round(float(pos[1]), 1)   # about 11 km: the station is not located exactly
        data, used, err = None, HOST, ""
        # Open-Meteo first; its free tier is per IP address, so a shared address can hit the daily limit (HTTP 429):
        # then MET Norway answers instead. On any failure the last good answer is kept and the reason shown.
        for host, url, fn in ((HOST, URL, parse), (FALLBACK_HOST, FALLBACK_URL, parse_metno)):
            try:
                _, body, _ = net.pooled_get(url.format(lat=lat, lon=lon), {"User-Agent": UA, "Accept": "application/json"}, 15.0)
                data, used = fn(json.loads(body)), host
                break
            except Exception as exc:
                err = (err + "; " if err else "") + f"{host}: {str(exc)[:80]}"
        if data is None:
            self.error = err[:240]
            return False
        with self.lock:
            self.data, self.fetched_at, self.error, self.provider = data, time.time(), "", used
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                with open(self.path + ".tmp", "w", encoding="utf-8") as f:
                    json.dump({"data": data, "fetched_at": self.fetched_at, "provider": self.provider}, f)
                os.replace(self.path + ".tmp", self.path)
            except OSError:
                pass
        return True

    def refresh_async(self) -> bool:
        if self.fetching:
            return False
        self.fetching = True

        def run() -> None:
            try:
                self.refresh()
            finally:
                self.fetching = False
        threading.Thread(target=run, name="weather", daemon=True).start()
        return True

    def start_background(self) -> None:
        def loop() -> None:
            time.sleep(60)
            while True:
                cfg = self.settings()
                if cfg["enabled"] and time.time() - self.fetched_at >= cfg["refresh_min"] * 60 - 5:
                    self.refresh()
                time.sleep(60)
        threading.Thread(target=loop, name="weather-loop", daemon=True).start()
