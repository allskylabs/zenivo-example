"""Rise/set maths (src/analytics/riseset.py) against well-known almanac values, plus the /proc parsing for the Admin tiles."""
from datetime import datetime, timezone

import pytest

from src.analytics import riseset
from src.api import sysinfo


def ts(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp()


def utc_minutes(epoch, day_start):
    return (epoch - day_start) / 60.0


# (place, lat, lon, midnight UTC the search starts at, expected rise UTC minutes, expected set UTC minutes), +-5 min
SUN_CASES = [
    ("London solstice", 51.5074, -0.1278, ts(2024, 6, 21), 3 * 60 + 43, 20 * 60 + 21),
    ("Greenwich equator, equinox", 0.0, 0.0, ts(2024, 3, 20), 6 * 60 + 5, 18 * 60 + 10),
]


@pytest.mark.parametrize("name,lat,lon,start,rise_min,set_min", SUN_CASES, ids=[c[0] for c in SUN_CASES])
def test_sun_rise_set_match_almanac(name, lat, lon, start, rise_min, set_min):
    r = riseset.body_info("sun", lat, lon, start)
    assert r["state"] == "down"
    assert utc_minutes(r["rise"], start) == pytest.approx(rise_min, abs=5)
    assert utc_minutes(r["set"], start) == pytest.approx(set_min, abs=5)


@pytest.mark.parametrize("lat,month,state", [(69.65, 6, "always_up"), (69.65, 12, "never_rises")], ids=["midnight sun", "polar night"])
def test_sun_polar_states(lat, month, state):
    r = riseset.body_info("sun", lat, 18.96, ts(2024, month, 21, 12))
    assert r["state"] == state and r["rise"] is None and r["set"] is None


@pytest.mark.parametrize("ra,dec,lat,state", [
    (101.29, -16.72, 0.0, "up_or_down"),       # Sirius rises and sets on the equator
    (37.95, 89.26, 21.0, "always_up"),         # Polaris is circumpolar in the northern hemisphere
    (37.95, 89.26, -30.0, "never_rises"),      # and never seen from the southern
])
def test_fixed_object_visibility(ra, dec, lat, state):
    r = riseset.fixed_info(ra, dec, lat, 105.0, ts(2026, 11, 17, 12))
    if state == "up_or_down":
        assert r["state"] in ("up", "down") and r["rise"] and r["set"]
        assert abs((r["rise"] - r["set"]) % 86164 - 43082) < 3600 * 2   # about 12 sidereal hours apart
    else:
        assert r["state"] == state and r["rise"] is None


def test_sun_and_moon_positions_are_sane():
    # solar noon on the equinox at the equator, Greenwich: Sun nearly overhead
    assert riseset.body_info("sun", 0.0, 0.0, ts(2024, 3, 20, 12, 7))["alt"] > 85
    assert riseset.body_info("pluto", 0.0, 0.0, ts(2024, 3, 20)) is None


@pytest.mark.parametrize("when,lo,hi", [(ts(2024, 4, 8, 18, 21), 0.0, 0.02), (ts(2024, 4, 24, 23, 49), 0.97, 1.0)], ids=["new moon", "full moon"])
def test_moon_phase(when, lo, hi):
    assert lo <= riseset.moon_phase(when)["illum"] <= hi


def test_moon_gets_a_rise_and_a_set_within_a_day_and_planets_have_magnitudes():
    now = ts(2026, 11, 17, 0)
    m = riseset.body_info("moon", 21.0, 105.8, now)
    assert m["rise"] and m["set"] and m["phase"] in ("New Moon", "Waxing Crescent", "First Quarter", "Waxing Gibbous", "Full Moon", "Waning Gibbous", "Last Quarter", "Waning Crescent")
    assert [b["name"] for b in riseset.all_bodies(21.0, 105.8, now)] == ["Sun", "Moon", "Venus", "Mars", "Jupiter", "Saturn"]


def test_cpu_percent_from_two_samples_and_meminfo():
    stat = iter(["cpu 100 0 100 800 0 0 0 0\n", "cpu 150 0 150 900 0 0 0 0\n"])
    sysinfo._last = None
    assert sysinfo.cpu_percent(read=lambda: next(stat), sleep=lambda s: None) == 50.0
    mi = sysinfo.parse_meminfo("MemTotal:  1000 kB\nMemAvailable:  250 kB\n")
    assert mi == {"MemTotal": 1000, "MemAvailable": 250}
