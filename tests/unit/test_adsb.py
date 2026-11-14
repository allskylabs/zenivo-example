"""ADS-B flight matching: SBS-1 parsing, station geometry and projection onto plane events."""

import math

import numpy as np

from src.analytics.adsb import AdsbFeed, enu_from_station, match_flight, pointing_rotation

STATION = {"latitude_deg": 21.0285, "longitude_deg": 105.8542, "elevation_m": 20.0}
SOLUTION = {"success": True, "pointing_az_deg": 147.77, "pointing_alt_deg": 25.0, "roll_deg": 1.91,
            "focal_px": 843.3, "projection": "rectilinear", "optical_center_px": [576.0, 324.0]}


def _offset(lat, lon, east_m, north_m):
    return lat + north_m / 111320.0, lon + east_m / (111320.0 * math.cos(math.radians(lat)))


def _to_px(lat, lon, alt_m):
    R = pointing_rotation(SOLUTION["pointing_az_deg"], SOLUTION["pointing_alt_deg"], SOLUTION["roll_deg"])
    enu = enu_from_station(STATION["latitude_deg"], STATION["longitude_deg"], STATION["elevation_m"], lat, lon, alt_m)
    cam = R.T @ (enu / np.linalg.norm(enu))
    f, (cx, cy) = SOLUTION["focal_px"], SOLUTION["optical_center_px"]
    return f * cam[0] / cam[2] + cx, f * cam[1] / cam[2] + cy


def _flight(t0=1000.0, n=40):
    """Aircraft at 3000 m flying east 15 km SSE of the station, one position per second."""
    pos = []
    for k in range(n):
        lat, lon = _offset(STATION["latitude_deg"], STATION["longitude_deg"], 6000 + 200 * k, -13000)
        pos.append((t0 + k, lat, lon, 3000.0))
    return pos


def test_pointing_axis_projects_to_image_centre():
    alt, az = math.radians(25.0), math.radians(147.77)
    axis = np.array([math.cos(alt) * math.sin(az), math.cos(alt) * math.cos(az), math.sin(alt)])
    R = pointing_rotation(147.77, 25.0, 1.91)
    assert np.allclose(R @ np.array([0, 0, 1.0]), axis)
    assert np.allclose(R.T @ R, np.eye(3), atol=1e-9)
    # Image "up" points to higher altitude
    assert (R @ np.array([0, -1.0, 0]))[2] > 0


def test_enu_of_overhead_point_is_up():
    e, n, u = enu_from_station(21.0, 105.0, 0.0, 21.0, 105.0, 10000.0)
    assert abs(e) < 1 and abs(n) < 1 and abs(u - 10000) < 1


def test_moving_dot_event_matches_flight():
    pos = _flight()
    t_start, t_end = 1010.0, 1020.0
    p0 = _to_px(*pos[10][1:])
    p1 = _to_px(*pos[20][1:])
    assert all(0 < v < 1152 for v in (p0[0], p1[0])) and all(0 < v < 648 for v in (p0[1], p1[1]))
    ev = {"type": "plane", "start_ts": t_start, "end_ts": t_end, "frames": 5, "moving_dot": True,
          "line": [p0[0] / 1152, p0[1] / 648, p1[0] / 1152, p1[1] / 648]}
    other = [(t, la + 0.3, lo, a) for t, la, lo, a in pos]  # another aircraft far to the north
    tracks = [{"icao": "888123", "callsign": "", "pos": other},
              {"icao": "8880AB", "callsign": "HVN123", "pos": pos}]
    m = match_flight(ev, tracks, STATION, SOLUTION)
    assert m is not None and m["callsign"] == "HVN123" and m["match_px"] < 5
    assert m["altitude_m"] == 3000 and 10 < m["distance_km"] < 20

    # Reversed direction (the dots ran the other way) is not this flight
    ev_rev = dict(ev, line=ev["line"][2:] + ev["line"][:2])
    assert match_flight(ev_rev, tracks, STATION, SOLUTION) is None


def test_single_frame_streak_matches_on_the_segment_and_misses_elsewhere():
    pos = _flight()
    p = _to_px(*pos[15][1:])
    q = _to_px(*pos[25][1:])
    ev = {"type": "meteor", "start_ts": 1015.0, "end_ts": 1015.0, "frames": 1,
          "line": [q[0] / 1152, q[1] / 648, (2 * p[0] - q[0]) / 1152, (2 * p[1] - q[1]) / 648]}
    tracks = [{"icao": "8880AB", "callsign": "HVN123", "pos": pos}]
    assert match_flight(ev, tracks, STATION, SOLUTION)["icao"] == "8880AB"
    far = dict(ev, line=[0.05, 0.05, 0.1, 0.06])
    assert match_flight(far, tracks, STATION, SOLUTION) is None
    # No positions near the event time: no match
    assert match_flight(dict(ev, start_ts=5000.0, end_ts=5000.0), tracks, STATION, SOLUTION) is None


def test_sbs_parsing_keeps_positions_and_callsign():
    feed = AdsbFeed("127.0.0.1", 30003)
    feed._parse(b"MSG,1,1,1,8880AB,1,2026/10/18,12:00:00.000,2026/10/18,12:00:00.000,HVN123  ,,,,,,,,,,,", 100.0)
    feed._parse(b"MSG,3,333,0,8880AB,100,2026/10/18,12:34:58.117,2026/10/18,12:34:58.117,,20325,,,20.13222,105.79437,,,0,0,0,0", 101.0)
    feed._parse(b"MSG,3,333,0,8880AB,100,2026/10/18,12:34:58.117,2026/10/18,12:34:58.117,,,,,,,,,0,0,0,0", 102.0)  # no position
    feed._parse(b"MSG,8,333,0,8880AB,100", 103.0)  # too short
    tr = feed.tracks(100.0, 102.0)
    assert len(tr) == 1 and tr[0]["callsign"] == "HVN123"
    t, lat, lon, alt = tr[0]["pos"][0]
    assert t == 101.0 and abs(lat - 20.13222) < 1e-6 and abs(alt - 20325 * 0.3048) < 1e-3
    feed._prune(101.0 + AdsbFeed.KEEP_S + 5)
    assert feed.tracks(0, 1e9) == []



def test_current_trail_is_thinned_and_ends_at_the_latest_fix():
    import time
    feed = AdsbFeed("127.0.0.1", 30003)
    now = time.time()
    for i in range(40):  # one fix per second for the last 40 s, climbing
        feed._parse(f"MSG,3,333,0,8880AB,100,,,,,,{10000 + i * 10},,,{21.0 + i * 0.001:.5f},105.9,,,0,0,0,0".encode(), now - 39 + i)
    (a,) = feed.current(trail_s=30.0)
    trail = a["trail"]
    assert [round(p[3]) for p in trail] == [27, 23, 19, 15, 11, 7, 3, 0]  # stored every TRAIL_STEP_S, then the latest fix
    assert trail[-1][:3] == [round(a["lat"], 5), 105.9, round(a["altitude_m"])]
    assert "trail" not in feed.current()[0]


# A real pass recorded 2026-10-18 20:43 (+07): moving-dot track of 34 frames and GTI8523's SBS positions
# (seconds from the first dot, lat, lon, altitude ft). The station coordinates had been left at the Hanoi-centre
# default, ~6.6 km from the camera; for an aircraft ~20-50 km away that put the projection 5-17 deg off.
GTI8523_EVENT = {"type": "plane", "start_ts": 0.0, "end_ts": 93.6, "frames": 34, "moving_dot": True,
                 "line": [0.1756, 0.7578, 0.5312, 0.8958]}
GTI8523_POS = [(-9.6, 21.00037, 106.01802, 10000), (-0.6, 20.98801, 106.01973, 10275),
               (7.9, 20.97621, 106.02146, 10450), (17.1, 20.96298, 106.02351, 10625),
               (26.0, 20.94997, 106.02552, 10775), (35.0, 20.93614, 106.02768, 10925),
               (44.0, 20.92204, 106.02989, 11025), (53.4, 20.90698, 106.03225, 11200),
               (62.7, 20.89167, 106.03465, 11400), (72.0, 20.87603, 106.03704, 11575),
               (80.5, 20.86121, 106.03925, 11700), (89.5, 20.84528, 106.04161, 11825),
               (98.7, 20.82873, 106.04408, 12075), (103.5, 20.81996, 106.04539, 12275)]


def test_recorded_pass_matches_only_from_the_true_station_location():
    tracks = [{"icao": "A4CB6F", "callsign": "GTI8523",
               "pos": [(t, lat, lon, ft * 0.3048) for t, lat, lon, ft in GTI8523_POS]}]
    # Location fitted from five recorded tracks of four aircraft (residual < 1 deg)
    fitted = {"latitude_deg": 21.0538, "longitude_deg": 105.9095, "elevation_m": 20.0}
    m = match_flight(GTI8523_EVENT, tracks, fitted, SOLUTION, lag_s=0.5)
    assert m is not None and m["callsign"] == "GTI8523" and m["match_px"] < 25 and m["match_angle_deg"] < 5
    assert match_flight(GTI8523_EVENT, tracks, STATION, SOLUTION, lag_s=0.5) is None


def test_trails_survive_a_restart(tmp_path):
    import time
    feed = AdsbFeed("127.0.0.1", 30003)
    now = time.time()
    for i in range(10):
        feed._parse(f"MSG,3,333,0,8880AB,100,,,,,,{10000 + i * 10},,,{21.0 + i * 0.01:.5f},105.9,,,0,0,0,0".encode(), now - 90 + i * 10)
    path = str(tmp_path / "trails.json")
    feed.save_trails(path)

    fresh = AdsbFeed("127.0.0.1", 30003)
    assert fresh.load_trails(path) == 1
    feed_trail = [p[1:] for p in feed._ac["8880AB"]["trail"]]
    assert [tuple(p[1:]) for p in fresh._ac["8880AB"]["trail"]] == [(lat, lon, round(alt)) for lat, lon, alt in feed_trail]
    # the next fix extends the restored path
    fresh._parse(b"MSG,3,333,0,8880AB,100,,,,,,10100,,,21.10000,105.9,,,0,0,0,0", now)
    (a,) = fresh.current(trail_s=900)
    assert len(a["trail"]) == len(feed_trail) + 1


def test_flights3d_globe_and_terrain_structure():
    from pathlib import Path
    html = (Path(__file__).resolve().parents[2] / "src" / "web" / "flights3d.html").read_text(encoding="utf-8")
    assert 'projection: { type: "globe" }' in html
    assert 'terrain: { source: "dem", exaggeration: 1 }' in html
    assert "new maplibregl.GlobeControl()" in html
    assert 'id="btn-globe"' in html
    assert 'id="btn-fr24"' in html
    assert 'id="fr24-modal"' in html
    assert 'id="lay-fr24"' in html
    assert "function llToUnitSphere(" in html
    assert "function globePos(" in html
    assert "function enuBasis(" in html
    assert "function boundsParam(" in html
    assert "function fastLiveryMaterials(" in html
    assert "projectionTransition" in html


def test_fr24_row_parsing_and_status():
    import time
    from src.analytics.fr24 import Fr24Feed
    feed = Fr24Feed(enabled=True, email="ops@example.org", token="test_enterprise_token")
    st = feed.status()
    assert st["enabled"] is True
    assert st["authenticated"] is True
    assert st["account_tier"] == "enterprise"

    now = time.time()
    row = [
        "8881B3", 21.2144, 105.8022, 281, 35000, 460, "4521",
        "F-VVNB1", "A359", "VN-A899", int(now) - 2, "HAN", "FRA",
        "VN37", 0, 640, "HVN37", 0, "HVN"
    ]
    ac = feed._parse_row("3f9a12b4", row, now, 600.0)
    assert ac is not None
    assert ac["icao"] == "8881B3"
    assert ac["fr24_id"] == "3f9a12b4"
    assert ac["callsign"] == "HVN37"
    assert ac["type"] == "A359"
    assert ac["reg"] == "VN-A899"
    assert ac["route"] == "HAN-FRA"
    assert ac["airline_iata"] == "VN"
    assert ac["airline_icao"] == "HVN"
    assert round(ac["altitude_m"]) == round(35000 * 0.3048)
    assert round(ac["speed_kmh"]) == round(460 * 1.852)
    assert round(ac["climb_m_min"]) == round(640 * 0.3048)
    assert len(ac["trail"]) == 1



def test_radiosonde_position_is_snapped_and_switch_stops_fetching():
    from types import SimpleNamespace
    from src.api.traffic_mixin import TrafficMixin

    calls = []

    class FakeFeed:
        def current(self, lat, lon, at=None):
            calls.append((lat, lon))
            return {"sondes": [], "error": ""}

    svc = SimpleNamespace(
        station_config={}, _sonde_feed=FakeFeed(),
        _overlay_station=lambda: {"latitude_deg": 21.0285, "longitude_deg": 105.8542, "elevation_m": 20.0},
        _overlay_solution=lambda: None)
    res = TrafficMixin.get_radiosondes_now(svc)
    assert res["enabled"] is True and calls and set(calls) == {(21.0, 106.0)}
    calls.clear()
    svc.station_config["radiosondes_enabled"] = 0
    res = TrafficMixin.get_radiosondes_now(svc)
    assert res["enabled"] is False and res["sondes"] == [] and not calls
