"""Bright-star pointing solver: recovers a known pointing, rejects random point fields."""

import math
from datetime import datetime, timezone

import cv2
import pytest
import numpy as np

from src.calibration import bright_star_solver as bss

WHEN = datetime(2026, 10, 16, 16, 57, 45, tzinfo=timezone.utc)
LAT, LON = 21.0285, 105.8542


def _rotation(az, alt, roll):
    """Camera->ENU rotation for a camera looking at (az, alt) with the given roll."""
    a, e = math.radians(az), math.radians(alt)
    fwd = np.array([math.cos(e) * math.sin(a), math.cos(e) * math.cos(a), math.sin(e)])
    right = np.array([math.cos(a), -math.sin(a), 0.0])
    down = np.cross(fwd, right)
    R = np.column_stack([right, down, fwd])
    r = math.radians(roll)
    rz = np.array([[math.cos(r), -math.sin(r), 0], [math.sin(r), math.cos(r), 0], [0, 0, 1]])
    return R @ rz


def _render(az, alt, roll, hfov, w=2304, h=1296):
    stars = [s for s in bss.load_bright_star_catalog() if s["mag"] <= 3.0]
    altaz = bss.stars_alt_az(stars, LAT, LON, WHEN)
    enu = bss._enu(altaz[:, 0], altaz[:, 1])
    f = (w / 2) / math.tan(math.radians(hfov / 2))
    xy, ok = bss._project(_rotation(az, alt, roll), enu, f, w / 2, h / 2)
    img = np.full((h, w, 3), 30, np.uint8)
    for (x, y), s, good, (al, _) in zip(xy, stars, ok, altaz):
        if good and al > 3 and 5 < x < w - 5 and 5 < y < h - 5:
            cv2.circle(img, (int(round(x)), int(round(y))), 3 if s["mag"] < 1.5 else 2, (255, 255, 255), -1)
    return img


def test_recovers_known_pointing():
    img = _render(az=142.0, alt=35.0, roll=2.0, hfov=68.0)
    res = bss.solve_bright_stars(img, LAT, LON, when=WHEN)
    assert res["success"], res
    assert abs(res["pointing_az_deg"] - 142.0) < 1.0
    assert abs(res["pointing_alt_deg"] - 35.0) < 1.0
    assert abs(res["fov_horizontal_deg"] - 68.0) < 1.5
    assert res["rms_px"] < 2.5
    assert len(res["matched_stars"]) >= 5


def test_random_points_are_rejected():
    rng = np.random.default_rng(7)
    img = np.full((1296, 2304, 3), 30, np.uint8)
    for _ in range(60):
        cv2.circle(img, (int(rng.integers(10, 2294)), int(rng.integers(10, 1286))), 2, (255, 255, 255), -1)
    assert bss.solve_bright_stars(img, LAT, LON, when=WHEN)["success"] is False


def test_service_converts_downscaled_solution_to_sensor_units(monkeypatch):
    from src.api.service import AllSkyService

    AllSkyService.reset_instance()
    svc = AllSkyService.get_instance()
    monkeypatch.setattr(svc, "_save_station_config", lambda: None)
    svc.station_config["camera_sensor"] = {"width_px": 2304, "height_px": 1296, "pixel_pitch_um": 2.8}
    svc.station_config.pop("sky_mask", None)  # the live station's polygon does not fit this synthetic frame
    frame = cv2.resize(_render(az=142.0, alt=35.0, roll=0.0, hfov=68.0), (1152, 648), interpolation=cv2.INTER_AREA)
    monkeypatch.setattr(bss, "datetime", type("D", (), {"now": staticmethod(lambda tz=None: WHEN)}))
    res = svc._solve_bright_star_pattern(frame, LAT, LON)
    assert res is not None and res["success"]
    optics = svc.station_config["optics"]
    assert abs(optics["focal_length_mm"] - 4.75) < 0.15  # 1695 sensor px x 2.8 µm
    assert optics["optical_center_x_px"] == 1152.0
    assert svc.station_config["solve_source"].startswith("live_frame")
    AllSkyService.reset_instance()


def test_recovers_fisheye_pointing_centre_and_distortion():
    w, h = 1296, 972
    f, k, cx, cy = 442.0, -0.02, 664.0, 490.0
    stars = [s for s in bss.load_bright_star_catalog() if s["mag"] <= 2.2]
    altaz = bss.stars_alt_az(stars, LAT, LON, WHEN)
    enu = bss._enu(altaz[:, 0], altaz[:, 1])
    xy, ok = bss._project(_rotation(156.0, 15.0, 1.0), enu, f, cx, cy, "equidistant", k)
    img = np.full((h, w, 3), 40, np.uint8)
    for (x, y), good, (al, _) in zip(xy, ok, altaz):
        if good and al > 3 and 5 < x < w - 5 and 5 < y < h - 5:
            cv2.circle(img, (int(round(x)), int(round(y))), 2, (255, 255, 255), -1)
    res = bss.solve_bright_stars(img, LAT, LON, when=WHEN, projection="equidistant",
                                 hfov_candidates_deg=range(120, 244, 6), use_sky_mask=False)
    assert res["success"], res
    assert abs(res["pointing_az_deg"] - 156.0) < 1.5
    assert abs(res["pointing_alt_deg"] - 15.0) < 1.5
    assert abs(res["optical_center_px"][0] - cx) < 6 and abs(res["optical_center_px"][1] - cy) < 6
    assert res["rms_px"] < 1.5


# ----------------------------------------------------------------------
# City-sky pipeline: planets, user mask, sidereal tracking, fixed-lens solve
# ----------------------------------------------------------------------

WHEN_HZ = datetime(2026, 10, 17, 12, 31, 31, tzinfo=timezone.utc)  # 19:31 Hanoi, camera facing the south horizon
FISH = dict(f=442.0, cx=669.0, cy=490.0, k=-0.02, w=1296, h=972)
POSE_HZ = (151.0, -3.0, 1.0)


def test_planet_positions_match_reference_ephemeris():
    # Reference: PyEphem astrometric (J2000) positions
    for when, name, ra, dec in [
        (WHEN_HZ, "Saturn", 10.173, 1.434),
        (datetime(2020, 1, 1, tzinfo=timezone.utc), "Jupiter", 276.967, -23.193),
        (datetime(2030, 6, 1, tzinfo=timezone.utc), "Venus", 32.532, 11.017),
    ]:
        p = next(p for p in bss.planet_positions(when) if p["name"] == name)
        cos_d = math.cos(math.radians(dec))
        assert math.hypot((p["ra"] - ra) * cos_d, p["dec"] - dec) < 0.15, (name, p)
    sat = next(p for p in bss.planet_positions(WHEN_HZ) if p["name"] == "Saturn")
    assert abs(sat["mag"] - 0.4) < 0.3
    assert any(o["name"] == "Saturn" for o in bss.bright_objects(WHEN_HZ, 3.0))


def test_polygon_mask_sky_minus_obstruction():
    m = bss.polygon_mask((100, 200), sky=[[[0, 0], [1, 0], [1, 0.5], [0, 0.5]]],
                         obstruction=[[[0, 0], [0.25, 0], [0.25, 0.5], [0, 0.5]]])
    assert m[10, 100] == 1 and m[80, 100] == 0 and m[10, 20] == 0
    assert bss.polygon_mask((100, 200)) is None


def _sky_series(pose=POSE_HZ, n=6, span_s=100.0, max_mag=2.1, mirror=False, seed=3):
    """Fisheye frames of the few bright stars/planets a city sky shows, plus static hot pixels and noise."""
    w, h, f, cx, cy, k = FISH["w"], FISH["h"], FISH["f"], FISH["cx"], FISH["cy"], FISH["k"]
    rng = np.random.default_rng(seed)
    hot = np.column_stack([rng.integers(10, w - 10, 40), rng.integers(10, h - 10, 40)])
    objs = bss.bright_objects(WHEN_HZ, max_mag)
    R = _rotation(*pose)
    frames, times = [], []
    for i in range(n):
        t = WHEN_HZ.timestamp() + (i - (n - 1) / 2) * span_s / (n - 1)
        when = datetime.fromtimestamp(t, timezone.utc)
        altaz = bss.stars_alt_az(objs, LAT, LON, when)
        xy, _ = bss._project(R, bss._enu(altaz[:, 0], altaz[:, 1]), f, cx, cy, "equidistant", k)
        img = np.clip(rng.normal(60, 2, (h, w)), 0, 255).astype(np.float32)
        for (x, y), o, (alt, _) in zip(xy, objs, altaz):
            if np.isfinite(x) and alt > 2 and 5 < x < w - 5 and 5 < y < h - 5:
                amp = 160.0 * 10 ** (-0.4 * (o["mag"] - 0.4))
                yy, xx = np.mgrid[-4:5, -4:5]
                xi, yi = int(x), int(y)
                img[yi - 4:yi + 5, xi - 4:xi + 5] += amp * np.exp(-((xx + xi - x) ** 2 + (yy + yi - y) ** 2) / 2.0)
        for x, y in hot:
            cv2.circle(img, (int(x), int(y)), 1, 200, -1)
        img = np.clip(img, 0, 255).astype(np.uint8)
        frames.append(cv2.flip(img, 1) if mirror else img)
        times.append(t)
    _sky_series.hot_pixels = hot
    return frames, times


def test_tracking_rejects_hot_pixels_and_keeps_drifting_stars():
    frames, times = _sky_series()
    tr = bss.track_sidereal_points(frames, times, FISH["f"])
    assert tr["static"] >= 30  # the hot pixels were found but did not move
    assert len(tr["points"]) >= 3
    hot = _sky_series.hot_pixels
    assert all(np.hypot(*(hot - p[:2]).T).min() > 2.0 for p in tr["points"])
    speed = np.hypot(tr["velocity"][:, 0], tr["velocity"][:, 1])
    assert np.all(speed < FISH["f"] * bss.SIDEREAL_RATE_RAD_S * 1.6)  # fisheye stretches up to pi/2 at 90°
    assert abs(tr["time"] - WHEN_HZ.timestamp()) < 1.0


def _fixed_solve(frames, times, when=None, cx=None):
    tr = bss.track_sidereal_points(frames, times, FISH["f"])
    when = when or datetime.fromtimestamp(tr["time"], timezone.utc)
    return bss.solve_fixed_lens(tr["points"], (FISH["w"], FISH["h"]), LAT, LON, when, FISH["f"] * 1.01,
                                (cx or FISH["cx"], FISH["cy"]), FISH["k"], "equidistant", velocity=tr["velocity"])


def test_fixed_lens_recovers_horizon_pointing_from_few_stars():
    res = _fixed_solve(*_sky_series())
    assert res["success"], res
    assert abs(res["pointing_az_deg"] - POSE_HZ[0]) < 0.5
    assert abs(res["pointing_alt_deg"] - POSE_HZ[1]) < 0.5
    assert abs(res["roll_deg"] - POSE_HZ[2]) < 0.5
    assert abs(res["focal_scale"] - 1 / 1.01) < 0.01  # the stored focal length was 1% off
    assert "Saturn" in [m["name"] for m in res["matched_stars"]]


def test_fixed_lens_rejects_wrong_sky():
    frames, times = _sky_series()
    # Wrong date: Saturn sits elsewhere among the stars
    assert not _fixed_solve(frames, times, when=WHEN_HZ.replace(month=12))["success"]
    # Mirror image: triangle handedness is wrong
    m_frames, _ = _sky_series(mirror=True)
    assert not _fixed_solve(m_frames, times, cx=FISH["w"] - 1 - FISH["cx"])["success"]
    # Only hot pixels and noise
    blank, _ = _sky_series(max_mag=-10)
    assert not _fixed_solve(blank, times)["success"]


def test_fixed_lens_rejects_random_drifting_points():
    rng = np.random.default_rng(11)
    for _ in range(40):
        n = int(rng.integers(3, 8))
        pts = np.column_stack([rng.uniform(20, 1270, n), rng.uniform(20, 500, n), rng.uniform(20, 400, n)])
        vel = np.tile([0.02, -0.025], (n, 1)) * rng.uniform(0.6, 1.2, (n, 1))
        res = bss.solve_fixed_lens(pts, (FISH["w"], FISH["h"]), LAT, LON, WHEN_HZ, FISH["f"], (FISH["cx"], FISH["cy"]),
                                   FISH["k"], "equidistant", velocity=vel)
        assert not res["success"], res


def test_service_sky_mask_roundtrip_and_validation(monkeypatch):
    from src.api.service import AllSkyService

    AllSkyService.reset_instance()
    svc = AllSkyService.get_instance()
    monkeypatch.setattr(svc, "_save_station_config", lambda: None)
    out = svc.set_sky_mask(sky=[[[0, 0], [1.2, 0], [1, 0.5]]], obstruction=[])
    assert out["sky"] == [[[0.0, 0.0], [1.0, 0.0], [1.0, 0.5]]]
    assert svc.station_config["sky_mask"]["sky"] == out["sky"]
    with pytest.raises(ValueError):
        svc.set_sky_mask(sky=[[[0, 0], [1, 1]]])
    svc.set_sky_mask(sky=[], obstruction=[])
    assert "sky_mask" not in svc.station_config
    AllSkyService.reset_instance()


def test_service_fixed_lens_solve_updates_pointing_only(monkeypatch):
    from src.api.service import AllSkyService

    AllSkyService.reset_instance()
    svc = AllSkyService.get_instance()
    monkeypatch.setattr(svc, "_save_station_config", lambda: None)
    svc.station_config["camera_sensor"] = {"width_px": FISH["w"] * 2, "height_px": FISH["h"] * 2, "pixel_pitch_um": 2.0}
    svc.station_config["optics"] = {
        "projection_type": "Fisheye Equidistant", "lens_projection": "equidistant", "focal_length_px": FISH["f"] * 2,
        "optical_center_x_px": FISH["cx"] * 2, "optical_center_y_px": FISH["cy"] * 2, "distortion_k": FISH["k"],
        "fov_horizontal_deg": 170.0,
    }
    svc.station_config.pop("sky_mask", None)
    svc.station_config["image_transform"] = {}  # the synthetic frames are already in sensor orientation
    frames, times = _sky_series()
    res = svc._solve_bright_star_pattern(frames[-1], LAT, LON, frames, times)
    assert res is not None and res["success"], res
    ori = svc.station_config["orientation"]
    assert abs(ori["azimuth_deg"] - POSE_HZ[0]) < 0.5 and abs(ori["altitude_deg"] - POSE_HZ[1]) < 0.5
    assert svc.station_config["optics"]["focal_length_px"] == FISH["f"] * 2  # lens left as solved
    assert "fixed lens" in svc.station_config["solve_source"]
    AllSkyService.reset_instance()


def test_fixed_lens_real_hanoi_city_sky():
    """Points that survived tracking in 6 x 10 s frames of the IMX335 fisheye, 2026-10-17 19:31 Hanoi
    (only Saturn, Fomalhaut and Diphda show through the light pollution). Manual solve: Az 151, Alt -3, roll 1."""
    pts = np.array([[723.9, 334.5, 362.6], [1371.1, 410.8, 145.4], [944.0, 524.0, 31.5]])
    vel = np.array([[0.049, -0.0598], [0.0535, -0.0343], [0.0428, -0.0511]])
    when = datetime(2026, 10, 17, 12, 31, 31, 771000, tzinfo=timezone.utc)
    sky = [[[x / 2591, y / 1943] for x, y in [(64, 880), (205, 930), (722, 880), (898, 840), (1186, 850), (1720, 850),
                                              (1856, 760), (1786, 462), (1835, 203), (2080, 100), (2080, 10), (460, 10),
                                              (259, 301), (110, 599)]]]  # open sky above the buildings
    args = dict(image_size=(2592, 1944), latitude_deg=LAT, longitude_deg=LON, focal_px=883.2,
                center_px=(1338.2, 979.6), distortion_k=-0.0193, projection="equidistant",
                sky_mask=bss.polygon_mask((1944, 2592), sky=sky))
    res = bss.solve_fixed_lens(pts, when=when, velocity=vel, **args)
    assert res["success"], res
    assert abs(res["pointing_az_deg"] - 151.0) < 0.8
    assert abs(res["pointing_alt_deg"] + 3.0) < 0.8
    assert abs(res["roll_deg"] - 1.0) < 0.8
    assert sorted(m["name"] for m in res["matched_stars"]) == ["Diphda", "Fomalhaut", "Saturn"]
    # Same points a month later, or mirrored, must not solve
    assert not bss.solve_fixed_lens(pts, when=when.replace(month=11), velocity=vel, **args)["success"]
    mirrored = pts.copy()
    mirrored[:, 0] = 2591 - mirrored[:, 0]
    args["center_px"] = (2591 - 1338.2, 979.6)
    args["sky_mask"] = args["sky_mask"][:, ::-1].copy()
    assert not bss.solve_fixed_lens(mirrored, when=when, velocity=vel * [-1, 1], **args)["success"]


def test_service_deep_solve_remeasures_wrong_lens(monkeypatch):
    from src.api.service import AllSkyService

    AllSkyService.reset_instance()
    svc = AllSkyService.get_instance()
    monkeypatch.setattr(svc, "_save_station_config", lambda: None)
    svc.station_config["camera_sensor"] = {"width_px": FISH["w"] * 2, "height_px": FISH["h"] * 2, "pixel_pitch_um": 2.0}
    wrong_f = FISH["f"] * 2 * 1.4
    svc.station_config["optics"] = {
        "projection_type": "Fisheye Equidistant", "lens_projection": "equidistant", "focal_length_px": wrong_f,
        "optical_center_x_px": FISH["cx"] * 2, "optical_center_y_px": FISH["cy"] * 2, "distortion_k": 0.0,
        "fov_horizontal_deg": 170.0,
    }
    svc.station_config.pop("sky_mask", None)
    frames, times = _sky_series()
    res = svc._solve_bright_star_pattern(frames[-1], LAT, LON, frames, times, refit_lens=True)
    assert res is not None and res["success"], res
    assert "fixed lens" not in svc.station_config["solve_source"]
    assert abs(svc.station_config["optics"]["focal_length_px"] - FISH["f"] * 2) / (FISH["f"] * 2) < 0.05
    AllSkyService.reset_instance()


def test_service_reset_calibration_keeps_location_and_mask(monkeypatch):
    from src.api.service import AllSkyService

    AllSkyService.reset_instance()
    svc = AllSkyService.get_instance()
    monkeypatch.setattr(svc, "_save_station_config", lambda: None)
    svc.station_config.update({"latitude_deg": 21.0538, "sky_mask": {"sky": [[[0, 0], [1, 0], [1, 1]]], "obstruction": []},
                               "solve_source": "live_frame (bright-star pattern)", "bright_star_solution": {"x": 1}})
    svc.station_config["optics"] = {"focal_length_px": 1.0, "lens_projection": "equidistant"}
    res = svc.reset_station_calibration()
    assert res["success"]
    cfg = svc.station_config
    assert cfg["solve_source"] == "not_solved_yet" and "bright_star_solution" not in cfg
    assert "focal_length_px" not in cfg["optics"] and svc._lens_intrinsics(2592) is None
    assert cfg["latitude_deg"] == 21.0538 and cfg["sky_mask"]["sky"]
    AllSkyService.reset_instance()


def test_manual_star_align_identifies_clicked_stars(monkeypatch):
    from src.api.service import AllSkyService

    AllSkyService.reset_instance()
    svc = AllSkyService.get_instance()
    monkeypatch.setattr(svc, "_save_station_config", lambda: None)
    svc.station_config.pop("sky_mask", None)
    svc.station_config["image_transform"] = {}
    svc.station_config["latitude_deg"], svc.station_config["longitude_deg"] = LAT, LON  # not the host's own saved location
    svc.station_config["camera_sensor"] = {"width_px": 1296, "height_px": 972, "pixel_pitch_um": 2.8}
    w, h, f, cx, cy, k = 1296, 972, 442.0, 664.0, 490.0, -0.02
    stars = [s for s in bss.load_bright_star_catalog() if s["mag"] <= 2.6]
    altaz = bss.stars_alt_az(stars, LAT, LON, WHEN)
    enu = bss._enu(altaz[:, 0], altaz[:, 1])
    xy, ok = bss._project(_rotation(156.0, 15.0, 1.0), enu, f, cx, cy, "equidistant", k)
    img = np.full((h, w, 3), 40, np.uint8)
    clicks = []
    for (x, y), good, (al, _) in zip(xy, ok, altaz):
        if good and al > 3 and 20 < x < w - 20 and 20 < y < h - 20:
            cv2.circle(img, (int(round(x)), int(round(y))), 3, (255, 255, 255), -1)
            clicks.append([(x + 4) / w, (y - 3) / h])  # a few px off: the click must snap to the star
    svc.last_frame = img
    res = svc.manual_star_solve(clicks[:6], apply=False, when=WHEN)
    assert res["success"], res
    assert len([n for n in res["names"] if n]) >= 5
    assert abs(res["pointing"]["pointing_az_deg"] - 156.0) < 3.0
    assert svc.station_config.get("solve_source") != "manual star alignment"  # preview stores nothing
    with pytest.raises(ValueError):
        svc.manual_star_solve(clicks[:3])
    AllSkyService.reset_instance()
