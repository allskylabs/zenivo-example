"""Cloud cover analysis (structure / stars / learned reference), history storage and API."""

import numpy as np
import cv2

from src.analytics.cloud_cover import CloudReference, analyze_cloud_cover, analyze_structure, MIN_NIGHT_SAMPLES
from src.analytics.cloud_history import CloudHistory


def _sky(cloud_frac: float, seed: int = 1, base: int = 70, gradient: bool = True) -> np.ndarray:
    """Dark sky with a smooth gradient and bright soft-edged blobs covering ~cloud_frac of it."""
    rng = np.random.default_rng(seed)
    h, w = 270, 480
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    sky = np.full((h, w), float(base), np.float32) + (25.0 * xx / w if gradient else 0.0)
    noise = cv2.GaussianBlur(rng.random((h, w)).astype(np.float32), (0, 0), 25)
    if cloud_frac > 0:
        thr = np.quantile(noise, 1.0 - cloud_frac)
        sky += 110.0 * cv2.GaussianBlur((noise > thr).astype(np.float32), (5, 5), 0)
    img = np.clip(sky + rng.normal(0, 2, (h, w)), 0, 255).astype(np.uint8)
    return cv2.merge([img, img, img])


def test_structure_measures_broken_cloud():
    for frac in (0.3, 0.6):
        st = analyze_structure(_sky(frac))
        assert st["bimodal"]
        assert abs(st["cloud_frac"] - frac) < 0.12


def test_uniform_sky_is_not_bimodal_and_day_needs_reference():
    st = analyze_structure(_sky(0.0))
    assert not st["bimodal"]
    res = analyze_cloud_cover(_sky(0.0), 0.001, 0.0, 40.0)
    assert res["cloud_cover_pct"] is None and res["status"] == "unknown"


def test_overexposed_frame_is_skipped():
    white = np.full((200, 300, 3), 255, np.uint8)
    res = analyze_cloud_cover(white, 0.001, 0.0, 40.0)
    assert res["cloud_cover_pct"] is None and res["status"] == "overexposed"


def test_day_reference_learned_from_mixed_sky_flags_uniform_overcast():
    ref = CloudReference(None)
    for seed in range(4):
        r = analyze_cloud_cover(_sky(0.4, seed=seed), 0.001, 0.0, 45.0, reference=ref)
        assert r["method"] == "day" and 20 < r["cloud_cover_pct"] < 60
    assert ref.day_samples >= 3
    # Same exposure, uniform and much brighter sky => overcast; uniform at the clear level => clear
    assert analyze_cloud_cover(_sky(0.0, base=190, gradient=False), 0.001, 0.0, 45.0, reference=ref)["cloud_cover_pct"] > 80
    assert analyze_cloud_cover(_sky(0.0, base=70), 0.001, 0.0, 45.0, reference=ref)["cloud_cover_pct"] < 20


def _stars(n: int, seed: int = 3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    img = np.full((540, 960), 20, np.uint8)
    for x, y in zip(rng.integers(10, 950, n), rng.integers(10, 530, n)):
        cv2.circle(img, (int(x), int(y)), 2, 200, -1)
    return cv2.merge([img, img, img])


def test_night_star_reference_adapts_to_camera():
    ref = CloudReference(None)
    for _ in range(MIN_NIGHT_SAMPLES):
        analyze_cloud_cover(_stars(60), 10.0, 30.0, -40.0, reference=ref)
    clear = analyze_cloud_cover(_stars(60), 10.0, 30.0, -40.0, reference=ref)
    half_frame = _stars(60)
    half_frame[:, 480:] = 20  # clouds hide the right half
    half = analyze_cloud_cover(half_frame, 10.0, 30.0, -40.0, reference=ref)
    assert clear["cloud_cover_pct"] < 15 and clear["confidence"] >= 0.5
    assert 30 < half["cloud_cover_pct"] < 75


def test_roi_excludes_ground():
    img = _sky(0.0)
    img[150:] = np.random.default_rng(0).integers(0, 255, img[150:].shape, dtype=np.uint8)  # textured ground
    st_full = analyze_structure(img)
    st_roi = analyze_structure(img[:140])
    assert not st_roi["bimodal"] and st_full["separation"] > st_roi["separation"]


def test_reference_persists(tmp_path):
    p = tmp_path / "ref.json"
    ref = CloudReference(str(p))
    analyze_cloud_cover(_stars(40), 10.0, 30.0, -40.0, reference=ref)
    again = CloudReference(str(p))
    assert again.night_samples == 1 and max(again.night_cells) > 0
    again.reset()
    assert CloudReference(str(p)).night_samples == 0


def test_history_query_and_bucketing(tmp_path):
    h = CloudHistory(str(tmp_path / "h.jsonl"))
    for i in range(100):
        h.append({"t": 1000 + i * 60, "cc": float(i), "m": "night", "conf": 0.8, "stars": i})
    h.append({"t": 1000 + 100 * 60, "cc": None, "m": "day", "conf": 0.0, "stars": 0})
    assert len(h.query(1000, 1000 + 99 * 60)) == 100
    b = h.query(1000, 1000 + 100 * 60, max_points=10)
    assert len(b) <= 11 and sum(p["n"] for p in b) == 101
    assert b[0]["cc"] < b[-2]["cc"]
    with open(h.path, "a") as fp:
        fp.write('{"t": 99')  # torn line
    assert len(h.query(0, 1e9)) == 101


def test_cloud_api_roundtrip(monkeypatch):
    from fastapi.testclient import TestClient
    from src.api.main import create_app
    from src.api.service import AllSkyService

    AllSkyService.reset_instance()
    client = TestClient(create_app())
    svc = AllSkyService.get_instance()
    monkeypatch.setattr(svc, "_save_station_config", lambda: None)
    svc.cloud_history.append({"t": int(__import__("time").time()) - 60, "cc": 42.0, "m": "day", "conf": 0.7, "stars": 0})
    data = client.get("/api/sky/clouds/history?hours=1").json()
    assert data["points"][-1]["cc"] == 42.0 and data["summary"]["samples"] == 1
    cfg = client.post("/api/sky/clouds/config", json={"sky_roi": [0, 0, 1, 0.6]}).json()
    assert cfg["sky_roi"] == [0.0, 0.0, 1.0, 0.6]
    assert client.post("/api/sky/clouds/config", json={"sky_roi": [0.5, 0, 0.52, 1]}).status_code == 400
    assert client.post("/api/sky/clouds/config", json={"reset_reference": True}).json()["reference"]["night_samples"] == 0
    AllSkyService.reset_instance()
