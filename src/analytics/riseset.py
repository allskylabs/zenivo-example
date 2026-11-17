"""Rise and set times, altitude and Moon phase of the Sun, Moon, planets and fixed sky objects for the station.

Low-precision series (Astronomical Almanac): Sun about 1 arcmin, Moon about 0.3 deg (rise/set within ~2 min),
planets from the Keplerian elements already used by the live overlay. That is plenty for a "rises 17:08" label and
needs no ephemeris package (the Pi has little RAM and none of astropy/ephem/skyfield is a dependency).

Times are epoch seconds (UTC); the page shows them in the station's time zone.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from src.pipeline.overlay import calculate_local_sidereal_time, equatorial_to_horizontal

H0_STAR = -0.567        # standard refraction at the horizon
H0_SUN = -0.833         # refraction + semi-diameter
WINDOW_S = 30 * 3600.0  # a Moon rise can be 24 h 50 min after the last one
STEP_S = 600.0
PLANETS = ("Venus", "Mars", "Jupiter", "Saturn")
_d2r = math.pi / 180.0


def _jd(t: float) -> float:
    return t / 86400.0 + 2440587.5


def _eq_from_ecliptic(lon_deg: float, lat_deg: float, n: float) -> Tuple[float, float]:
    eps = (23.439 - 0.0000004 * n) * _d2r
    l, b = lon_deg * _d2r, lat_deg * _d2r
    ra = math.atan2(math.sin(l) * math.cos(eps) - math.tan(b) * math.sin(eps), math.cos(l))
    dec = math.asin(math.sin(b) * math.cos(eps) + math.cos(b) * math.sin(eps) * math.sin(l))
    return (ra / _d2r) % 360.0, dec / _d2r


def sun_radec(t: float) -> Tuple[float, float]:
    """Sun RA/Dec (deg, of date) at epoch seconds t."""
    n = _jd(t) - 2451545.0
    g = (357.528 + 0.9856003 * n) * _d2r
    lon = 280.46 + 0.9856474 * n + 1.915 * math.sin(g) + 0.02 * math.sin(2 * g)
    return _eq_from_ecliptic(lon, 0.0, n)


def moon_radec(t: float) -> Tuple[float, float, float]:
    """Geocentric Moon RA/Dec (deg, of date) and horizontal parallax (deg), Almanac low-precision series."""
    T = (_jd(t) - 2451545.0) / 36525.0
    s = lambda a, b: math.sin((a + b * T) * _d2r)
    lon = (218.32 + 481267.881 * T + 6.29 * s(135.0, 477198.87) - 1.27 * s(259.3, -413335.36)
           + 0.66 * s(235.7, 890534.22) + 0.21 * s(269.9, 954397.74) - 0.19 * s(357.5, 35999.05) - 0.11 * s(186.5, 966404.03))
    lat = 5.13 * s(93.3, 483202.02) + 0.28 * s(228.2, 960400.89) - 0.28 * s(318.3, 6003.15) - 0.17 * s(217.6, -407332.21)
    c = lambda a, b: math.cos((a + b * T) * _d2r)
    par = (0.9508 + 0.0518 * c(135.0, 477198.87) + 0.0095 * c(259.3, -413335.36)
           + 0.0078 * c(235.7, 890534.22) + 0.0028 * c(269.9, 954397.74))
    ra, dec = _eq_from_ecliptic(lon, lat, T * 36525.0)
    return ra, dec, par


def moon_phase(t: float) -> Dict[str, Any]:
    """Illuminated fraction, age angle (0 new, 180 full) and a name for the phase."""
    n = _jd(t) - 2451545.0
    g = (357.528 + 0.9856003 * n) * _d2r
    sun_lon = 280.46 + 0.9856474 * n + 1.915 * math.sin(g) + 0.02 * math.sin(2 * g)
    T = n / 36525.0
    moon_lon = (218.32 + 481267.881 * T + 6.29 * math.sin((135.0 + 477198.87 * T) * _d2r) - 1.27 * math.sin((259.3 - 413335.36 * T) * _d2r)
                + 0.66 * math.sin((235.7 + 890534.22 * T) * _d2r))
    angle = (moon_lon - sun_lon) % 360.0
    illum = (1 - math.cos(angle * _d2r)) / 2
    names = ["New Moon", "Waxing Crescent", "First Quarter", "Waxing Gibbous", "Full Moon", "Waning Gibbous", "Last Quarter", "Waning Crescent"]
    return {"illum": round(illum, 3), "angle_deg": round(angle, 1), "phase": names[int(((angle + 22.5) % 360.0) // 45.0)]}


def _precess(ra: float, dec: float, t: float) -> Tuple[float, float]:
    """J2000 RA/Dec to the equinox of date (first-order, good to ~0.01 deg for a few decades)."""
    yrs = (_jd(t) - 2451545.0) / 365.25
    ra_r, dec_r = ra * _d2r, dec * _d2r
    dra = (0.012854 + 0.0055675 * math.sin(ra_r) * math.tan(dec_r)) * yrs
    ddec = 0.0055675 * math.cos(ra_r) * yrs
    return (ra + dra) % 360.0, max(-90.0, min(90.0, dec + ddec))


def _altaz(ra: float, dec: float, t: float, lat: float, lon: float) -> Tuple[float, float]:
    lst = calculate_local_sidereal_time(datetime.fromtimestamp(t, timezone.utc), lon)
    return equatorial_to_horizontal(ra, dec, lst, lat)


def _planet_track(name: str, t0: float):
    """RA/Dec (of date) of a planet as a function of t, linear between t0 and t0 + WINDOW_S (they move <1 deg a day)."""
    from src.calibration.bright_star_solver import planet_positions
    pts = {}
    for t in (t0, t0 + WINDOW_S):
        row = next(p for p in planet_positions(datetime.fromtimestamp(t, timezone.utc)) if p["name"] == name)
        pts[t] = _precess(row["ra"], row["dec"], t) + (row["mag"],)
    (ra0, dec0, mag), (ra1, dec1, _) = pts[t0], pts[t0 + WINDOW_S]
    dra = ((ra1 - ra0 + 180.0) % 360.0) - 180.0
    return (lambda t: ((ra0 + dra * (t - t0) / WINDOW_S) % 360.0, dec0 + (dec1 - dec0) * (t - t0) / WINDOW_S)), mag


def _events(f, h0: float, t0: float) -> Dict[str, Any]:
    """f(t) -> altitude (deg). Next rise and next set within WINDOW_S of t0 (bisected to ~seconds)."""
    def g(t):
        return f(t) - h0

    def root(a, b):
        ga = g(a)
        for _ in range(14):
            m = (a + b) / 2
            gm = g(m)
            if (gm > 0) == (ga > 0):
                a, ga = m, gm
            else:
                b = m
        return (a + b) / 2

    rise = set_ = None
    prev_t, prev_g = t0, g(t0)
    t = t0
    while t < t0 + WINDOW_S:
        t += STEP_S
        cur = g(t)
        if (cur > 0) != (prev_g > 0):
            if cur > 0 and rise is None:
                rise = root(prev_t, t)
            elif cur <= 0 and set_ is None:
                set_ = root(prev_t, t)
            if rise is not None and set_ is not None:
                break
        prev_t, prev_g = t, cur
    up_now = g(t0) > 0
    state = "up" if up_now else "down"
    if rise is None and set_ is None:
        state = "always_up" if up_now else "never_rises"
    return {"rise": round(rise) if rise else None, "set": round(set_) if set_ else None, "state": state}


def body_info(body: str, lat: float, lon: float, now: float) -> Optional[Dict[str, Any]]:
    """Altitude/azimuth now plus next rise/set for 'sun', 'moon' or a planet name (case-insensitive); None if unknown."""
    key = body.strip().lower()
    mag = None
    if key == "sun":
        def f(t):
            return _altaz(*sun_radec(t), t, lat, lon)[0]
        h0, pos = H0_SUN, lambda t: sun_radec(t)
        name = "Sun"
    elif key == "moon":
        def f(t):
            return _altaz(*moon_radec(t)[:2], t, lat, lon)[0]
        h0, pos = 0.7275 * moon_radec(now)[2] - 0.5667, lambda t: moon_radec(t)[:2]
        name = "Moon"
    else:
        match = next((p for p in PLANETS if p.lower() == key), None)
        if match is None:
            return None
        track, mag = _planet_track(match, now)

        def f(t):
            return _altaz(*track(t), t, lat, lon)[0]
        h0, pos, name = H0_STAR, track, match
    ra, dec = pos(now)
    alt, az = _altaz(ra, dec, now, lat, lon)
    out = {"name": name, "alt": round(alt, 1), "az": round(az, 1), **_events(f, h0, now)}
    if mag is not None:
        out["mag"] = mag
    if key == "moon":
        out.update(moon_phase(now))
    return out


def fixed_info(ra_j2000: float, dec_j2000: float, lat: float, lon: float, now: float) -> Dict[str, Any]:
    """Same for a star / deep-sky object at fixed J2000 RA/Dec."""
    ra, dec = _precess(ra_j2000, dec_j2000, now)
    alt, az = _altaz(ra, dec, now, lat, lon)
    return {"alt": round(alt, 1), "az": round(az, 1), **_events(lambda t: _altaz(ra, dec, t, lat, lon)[0], H0_STAR, now)}


def all_bodies(lat: float, lon: float, now: float) -> List[Dict[str, Any]]:
    return [body_info(b, lat, lon, now) for b in ("sun", "moon", *PLANETS)]
