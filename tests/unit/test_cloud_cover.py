"""Cloud cover analysis (colour / brightness structure, learned template), history storage and API."""

import numpy as np
import cv2

from src.analytics.cloud_cover import MIN_TEMPLATE_SAMPLES, CloudReference, analyze_cloud_cover
from src.analytics.cloud_history import CloudHistory

H, W = 270, 480


def _sky(cloud_frac: float, seed: int = 1, night: bool = True, glow: float = 40.0, bump: float = 0.0) -> np.ndarray:
    """Teal sky with lens colour shading (bluer centre) and a light-pollution glow at the bottom, plus
    grey clouds (B = R) covering ~cloud_frac of it; at night the clouds are also brighter."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    r2 = ((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2
    base = (60.0 if night else 150.0) + glow * (yy / H) ** 2
    if bump:
        # A small static glow (street lamp behind a roof): not a quadratic, the template must learn it
        base = base + bump * np.exp(-(((xx - 0.75 * W) / 18) ** 2 + ((yy - 0.3 * H) / 18) ** 2))
    blue = 1.25 - 0.15 * r2   # B/R of clear sky, falling towards the corners (lens shading)
    b, g, r = base * blue, base * 1.05, base.copy()
    if cloud_frac > 0:
        noise = cv2.GaussianBlur(rng.random((H, W)).astype(np.float32), (0, 0), 20)
        k = cv2.GaussianBlur((noise > np.quantile(noise, 1.0 - cloud_frac)).astype(np.float32), (5, 5), 0)
        grey = base * (1.6 if night else 1.1)
        b, g, r = b + k * (grey - b), g + k * (grey - g), r + k * (grey - r)
    img = np.stack([b, g, r], axis=-1) + rng.normal(0, 1.5, (H, W, 3))
    return np.clip(img, 0, 255).astype(np.uint8)


def test_broken_cloud_is_measured_day_and_night():
    for night, sun in ((True, -40.0), (False, 40.0)):
        for frac in (0.3, 0.6):
            r = analyze_cloud_cover(_sky(frac, night=night), 1.0, 10.0, sun)
            assert r["method"] == ("night" if night else "day")
            assert abs(r["cloud_cover_pct"] - 100 * frac) < 12, (night, frac, r)


def test_clear_sky_with_glow_and_shading_is_clear():
    for night, sun in ((True, -40.0), (False, 40.0)):
        r = analyze_cloud_cover(_sky(0.0, night=night, glow=60.0), 1.0, 10.0, sun)
        assert r["cloud_cover_pct"] < 5 and r["status"] == "clear"


def test_sky_mask_excludes_buildings():
    img = _sky(0.0)
    img[:, 400:] = (90, 140, 200)  # a lit wall on the right: bright and orange
    walls = analyze_cloud_cover(img, 1.0, 10.0, -40.0)
    mask = {"sky": [[[0, 0], [0.8, 0], [0.8, 1], [0, 1]]], "obstruction": []}
    masked = analyze_cloud_cover(img, 1.0, 10.0, -40.0, sky_polygons=mask)
    assert walls["cloud_cover_pct"] > 10 and masked["cloud_cover_pct"] < 5


def test_overexposed_and_black_frames_are_skipped():
    white = np.full((200, 300, 3), 255, np.uint8)
    res = analyze_cloud_cover(white, 0.001, 0.0, 40.0)
    assert res["cloud_cover_pct"] is None and res["status"] == "overexposed"
    black = np.full((200, 300, 3), 8, np.uint8)
    res = analyze_cloud_cover(black, 10.0, 30.0, -40.0)
    assert res["cloud_cover_pct"] is None and res["status"] == "unknown"


def test_template_learns_static_glow(monkeypatch):
    from src.analytics import cloud_cover

    monkeypatch.setattr(cloud_cover, "TEMPLATE_RATE", 0.02)  # learn in 80 samples instead of a few nights
    before = analyze_cloud_cover(_sky(0.0, bump=60.0), 1.0, 10.0, -40.0)["cloud_cover_pct"]
    assert before > 0.5  # the lamp glow looks like a small cloud at first
    ref = CloudReference(None)
    for seed in range(80):
        analyze_cloud_cover(_sky(0.15, seed=seed, bump=60.0), 1.0, 10.0, -40.0, reference=ref, count_stars=False)
    assert ref.night_samples >= MIN_TEMPLATE_SAMPLES
    after = analyze_cloud_cover(_sky(0.0, bump=60.0), 1.0, 10.0, -40.0, reference=ref)
    assert after["cloud_cover_pct"] < before / 4
    # Twilight does not teach the template
    n = ref.night_samples
    analyze_cloud_cover(_sky(0.0), 1.0, 10.0, -8.0, reference=ref)
    assert ref.night_samples == n


def test_reference_persists(tmp_path):
    p = tmp_path / "ref.json"
    ref = CloudReference(str(p))
    for seed in range(CloudReference.SAVE_EVERY):
        analyze_cloud_cover(_sky(0.0, seed=seed), 1.0, 10.0, -40.0, reference=ref, count_stars=False)
    again = CloudReference(str(p))
    assert again.night_samples == CloudReference.SAVE_EVERY and again.template is not None
    assert np.allclose(again.template, ref.template, atol=1e-3)
    again.reset()
    assert CloudReference(str(p)).night_samples == 0 and CloudReference(str(p)).template is None


def test_old_star_reference_is_ignored(tmp_path):
    p = tmp_path / "ref.json"
    p.write_text('{"night_cells": [1.0], "night_samples": 300, "day_clear": 8.8, "day_samples": 88}')
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
    assert h.remove_range(1000, 1000 + 50 * 60) == 50
    assert len(h.query(0, 1e9)) == 51 and h.query(0, 1e9)[0]["t"] == 1000 + 50 * 60


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


def test_lens_circle_keeps_the_dark_rim_out_of_the_judged_sky():
    from src.analytics.cloud_cover import sky_work_mask

    full = sky_work_mask((60, 80), None, (0, 0, 1, 1))
    disc = sky_work_mask((60, 80), None, (0, 0, 1, 1), circle=(0.5, 0.5, 0.35))
    assert disc.sum() < full.sum() * 0.7 and disc[30, 40] and not disc[2, 2] and not disc[30, 2]
