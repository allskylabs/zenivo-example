"""Focus assistant: HFR / contrast measures, session best tracking and the /api/focus endpoints."""

import json

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from src.analytics.focus import FocusSession, find_best_star, measure_focus
from src.api.main import create_app
from src.api.service import AllSkyService
from src.hardware.simulator import SimulatedIMX335Controller


def _stars(sigma: float, seed: int = 1, size: int = 256, peak: float = 150.0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    img = np.full((size, size), 20, np.float32) + rng.normal(0, 2, (size, size))
    yy, xx = np.mgrid[0:size, 0:size]
    for x, y, a in [(50, 60, 1.0), (120, 80, 0.7), (200, 200, 1.2), (70, 190, 0.6), (180, 40, 0.4)]:
        img += peak * a * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sigma ** 2))
    img[100, 100] = 255  # hot pixel
    return np.clip(img, 0, 255).astype(np.uint8)


def test_star_hfr_tracks_defocus():
    scores = [measure_focus(_stars(s))["score"] for s in (0.7, 1.2, 2.0, 3.0, 4.0)]
    assert all(a < b for a, b in zip(scores, scores[1:])), scores
    res = measure_focus(_stars(1.5))
    assert res["method"] == "stars" and res["lower_is_better"]
    assert abs(res["hfr"] - 1.1774 * 1.5) < 0.35  # Gaussian HFR = 1.1774 sigma
    assert res["n_sources"] == 5  # the hot pixel is rejected


def test_saturated_lights_use_lights_method():
    scores = []
    for sigma in (1.5, 3.0, 6.0):
        res = measure_focus(_stars(sigma, peak=900.0, size=384), method="lights")
        assert res["method"] == "lights" and res["score"] is not None
        scores.append(res["score"])
    assert scores[0] < scores[1] < scores[2]


def test_daytime_contrast_drops_with_blur():
    rng = np.random.default_rng(3)
    scene = cv2.resize(rng.integers(40, 220, (32, 32), dtype=np.uint8), (256, 256), interpolation=cv2.INTER_NEAREST)
    sharp = measure_focus(scene)
    assert sharp["method"] == "contrast" and not sharp["lower_is_better"]
    soft = measure_focus(cv2.GaussianBlur(scene, (9, 9), 0))
    assert soft["score"] < sharp["score"] * 0.7


def test_session_best_uses_smoothed_score_and_resets_on_new_roi():
    fs = FocusSession()
    for sigma in (3.0, 2.0, 1.0, 1.0, 1.0, 2.5):
        out = fs.record(fs.measure(_stars(sigma)), 0.0)
    assert out["quality_pct"] < 90 and fs.best < 1.6
    fs.configure(cx=0.2)
    assert fs.best is None and not fs.history


def test_find_best_star_prefers_unsaturated():
    img = _stars(1.5, size=384)
    img[300:310, 300:310] = 255  # saturated blob
    star = find_best_star(img)
    assert star is not None and not star["saturated"]


@pytest.fixture
def client():
    AllSkyService.reset_instance()
    cam = SimulatedIMX335Controller(width=640, height=480, seed=42)
    cam.open()
    app = create_app(camera=cam)
    with TestClient(app) as c:
        yield c
    AllSkyService.reset_instance()


def test_focus_api_roundtrip(client):
    assert client.get("/api/focus/frame").status_code == 409
    state = client.post("/api/focus/start", json={"roi_size": 128, "method": "stars"}).json()
    assert state["active"] and state["roi_size"] == 128 and state["method"] == "stars"
    r = client.get("/api/focus/frame", params={"since": 0, "stretch": 1})
    assert r.status_code == 200 and r.content[:2] == b"\xff\xd8"
    res = json.loads(r.headers["X-Focus"])
    assert res["roi"][2] == 128 and res["method"] == "stars" and len(r.headers["X-Focus"]) < 4096
    assert client.post("/api/focus/config", json={"cx": 0.25, "cy": 0.75, "method": "contrast"}).json()["cx"] == 0.25
    assert client.get("/api/focus/overview.jpg").content[:2] == b"\xff\xd8"
    assert "found" in client.post("/api/focus/auto-roi").json()
    assert client.get("/api/status").json()["focus"]["active"]
    assert not client.post("/api/focus/stop").json()["active"]
    assert client.get("/focus").status_code == 200
