"""Automatic sky mask proposal: polygons from a frame, and the API that serves them."""

import cv2
import numpy as np
import pytest

from src.calibration.sky_mask_auto import MAX_POINTS, MAX_POLYS, propose_sky_mask
from tests.unit.test_api import api_client, sim_camera  # noqa: F401  (fixtures)

H, W = 720, 960


def _night_disc(tree=False):
    """A dim smooth sky disc with stars on a black surround, optionally a textured 'tree' inside it."""
    f = np.zeros((H, W, 3), np.uint8)
    cv2.circle(f, (W // 2, H // 2), 340, (22, 20, 18), -1)
    rng = np.random.default_rng(3)
    for _ in range(60):
        cv2.circle(f, (int(rng.integers(300, 660)), int(rng.integers(150, 570))), 1, (255, 255, 255), -1)
    if tree:
        f[300:420, 420:540] = (rng.random((120, 120, 3)) * 255).astype(np.uint8)
    return f


def _blocky_noise(seed):
    """Strong texture at the scale of the detector (random 6 px blocks): nothing smooth, so no sky."""
    rng = np.random.default_rng(seed)
    small = (rng.random((H // 6, W // 6, 3)) * 255).astype(np.uint8)
    return cv2.resize(small, (W, H), interpolation=cv2.INTER_NEAREST)


def test_fisheye_disc_becomes_one_sky_polygon_without_the_black_corners():
    r = propose_sky_mask(_night_disc())
    assert len(r["sky"]) == 1 and r["obstruction"] == []
    assert 3 <= len(r["sky"][0]) <= MAX_POINTS
    # disc area / frame area is 0.525; the detector keeps a margin off the bright horizon, so a bit less
    assert 0.38 < r["sky_fraction"] < 0.53
    assert all(0.0 <= v <= 1.0 for pt in r["sky"][0] for v in pt)


def test_textured_object_inside_the_sky_is_an_obstruction():
    r = propose_sky_mask(_night_disc(tree=True))
    assert len(r["sky"]) == 1 and len(r["obstruction"]) == 1
    cx = np.mean([p[0] for p in r["obstruction"][0]])
    cy = np.mean([p[1] for p in r["obstruction"][0]])
    assert cx == pytest.approx(480 / W, abs=0.04) and cy == pytest.approx(360 / H, abs=0.05)


def test_no_clear_sky_gives_empty_proposal():
    r = propose_sky_mask(_blocky_noise(5))
    assert r["sky"] == [] and r["obstruction"] == []


def test_polygon_limits_respected():
    r = propose_sky_mask(_night_disc(tree=True))
    assert len(r["sky"]) <= MAX_POLYS and all(len(p) <= MAX_POINTS for p in r["sky"] + r["obstruction"])


class TestSkyMaskAutoApi:
    def test_endpoint_returns_proposal_and_saves_nothing(self, api_client, monkeypatch):  # noqa: F811
        from src.api.service import AllSkyService

        svc = AllSkyService.get_instance()
        monkeypatch.setattr(svc, "last_frame", _night_disc(tree=True))
        monkeypatch.setitem(svc.station_config, "image_transform", {})
        monkeypatch.setitem(svc.station_config, "sky_mask", {"sky": [[[0, 0], [1, 0], [1, 1]]], "obstruction": []})
        r = api_client.post("/api/station/sky-mask/auto")
        assert r.status_code == 200
        body = r.json()
        assert body["sky"] and "sky_fraction" in body
        assert svc.station_config["sky_mask"]["sky"] == [[[0, 0], [1, 0], [1, 1]]]  # unchanged: proposal only

    def test_endpoint_400_when_no_sky_found(self, api_client, monkeypatch):  # noqa: F811
        from src.api.service import AllSkyService

        svc = AllSkyService.get_instance()
        monkeypatch.setattr(svc, "last_frame", _blocky_noise(1))
        r = api_client.post("/api/station/sky-mask/auto")
        assert r.status_code == 400 and "draw the mask by hand" in r.json()["detail"]
