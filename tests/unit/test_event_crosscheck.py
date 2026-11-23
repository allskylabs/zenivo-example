from datetime import datetime, timezone

import pytest

sgp4 = pytest.importorskip("sgp4.api")

from src.analytics.satellites import SatelliteTracker
from src.api.events_mixin import EventsMixin

L1 = "1 25544U 98067A   24001.50000000  .00016717  00000-0  10270-3 0  9005"
L2 = "2 25544  51.6400 247.4627 0006703 130.5360 325.0288 15.50000000 10000"
STATION = {"latitude_deg": 21.0, "longitude_deg": 105.8, "elevation_m": 10.0}


def _tracker(tmp_path):
    t = SatelliteTracker(str(tmp_path / "tle.json"))
    t._sats = [("ISS", "stations", sgp4.Satrec.twoline2rv(L1, L2))]
    return t


def _find_sunlit_moment(t):
    """Scan the day after the TLE epoch for a moment the ISS is above 10 deg, sunlit, under a dark sky."""
    base = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc).timestamp()
    sat = t._sats[0][2]
    for k in range(0, 3 * 86400, 30):
        c = t._context(STATION, datetime.fromtimestamp(base + k, timezone.utc))
        st = t._state(c, sat)
        if st and st["alt"] > 10 and st["sunlit"] and c["sun_alt"] < -6:
            return base + k, st
    return None, None


def test_match_pass_names_the_satellite_and_rejects_far_track(tmp_path):
    t = _tracker(tmp_path)
    ts, st = _find_sunlit_moment(t)
    if ts is None:
        pytest.skip("no visible pass in the window for this synthetic TLE")
    p0 = (st["az"] - 0.3, st["alt"]); p1 = (st["az"] + 0.3, st["alt"])
    hit = t.match_pass(STATION, p0, p1, ts, ts)
    assert hit and hit["norad"] == 25544
    far = t.match_pass(STATION, (st["az"] + 20, st["alt"]), (st["az"] + 21, st["alt"]), ts, ts)
    assert far is None


class _Svc(EventsMixin):
    def __init__(self, gates=None):
        self.station_config = {"event_gates": gates or {}}


def test_plane_fragments_of_one_flight_share_a_group():
    s = _Svc()
    evs = [{"type": "plane", "start_ts": 1000, "end_ts": 1010, "flight": {"icao": "abc"}},
           {"type": "plane", "start_ts": 1100, "end_ts": 1105, "flight": {"icao": "abc"}},
           {"type": "plane", "start_ts": 2000, "end_ts": 2005, "flight": {"icao": "abc"}},
           {"type": "plane", "start_ts": 1100, "end_ts": 1105}]
    for e in evs:
        s._group_plane_fragments(e)
    assert evs[1]["group_id"] == evs[0]["group_id"] and evs[1]["fragment"]
    assert evs[2]["group_id"] != evs[0]["group_id"] and "fragment" not in evs[2]
    assert "group_id" not in evs[3]
    off = _Svc({"crosscheck": False}); e = {"type": "plane", "start_ts": 1, "flight": {"icao": "x"}}
    off._group_plane_fragments(e); assert "group_id" not in e
