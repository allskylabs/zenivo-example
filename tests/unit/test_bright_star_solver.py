"""Bright-star pointing solver: recovers a known pointing, rejects random point fields."""

import math
from datetime import datetime, timezone

import cv2
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
