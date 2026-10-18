"""Day-only 180° rotation: applied to RTS5869 ISP (MJPG) frames, never to RAW10 night frames."""

import numpy as np

from src.api.service import AllSkyService


def _svc(monkeypatch, backend):
    AllSkyService.reset_instance()
    svc = AllSkyService.get_instance()
    monkeypatch.setattr(svc, "_save_station_config", lambda: None)
    monkeypatch.setattr("src.api.service.describe_camera", lambda cam: {"backend": backend})
    svc.set_image_transform(flip_horizontal=False, flip_vertical=False, rotate_deg=0, rotate_isp_180=False)
    return svc


def _frame():
    f = np.zeros((4, 6, 3), np.uint8)
    f[0, 0] = 255  # marker in the top-left corner
    return f


def test_day_rotation_only_for_isp_frames(monkeypatch):
    svc = _svc(monkeypatch, "rts5869")
    res = svc.set_image_transform(rotate_isp_180=True)
    assert res["image_transform"]["rotate_isp_180"] is True

    day = svc._apply_image_transform(_frame(), svc._is_isp_frame(None))
    night = svc._apply_image_transform(_frame(), svc._is_isp_frame(np.zeros((2, 2))))
    assert day[-1, -1, 0] == 255 and day[0, 0, 0] == 0      # rotated 180°
    assert night[0, 0, 0] == 255                            # untouched

    # Combined with the global 180° rotation the two cancel for day frames
    svc.set_image_transform(rotate_deg=180)
    assert svc._apply_image_transform(_frame(), True)[0, 0, 0] == 255
    assert svc._apply_image_transform(_frame(), False)[-1, -1, 0] == 255
    AllSkyService.reset_instance()


def test_day_rotation_ignored_for_other_cameras(monkeypatch):
    svc = _svc(monkeypatch, "picamera")
    svc.set_image_transform(rotate_isp_180=True)
    assert svc._is_isp_frame(None) is False
    assert svc._apply_image_transform(_frame(), svc._is_isp_frame(None))[0, 0, 0] == 255
    AllSkyService.reset_instance()


def test_orientation_api_accepts_day_rotation(monkeypatch):
    from fastapi.testclient import TestClient
    from src.api.main import create_app

    AllSkyService.reset_instance()
    client = TestClient(create_app())
    monkeypatch.setattr(AllSkyService.get_instance(), "_save_station_config", lambda: None)
    body = client.post("/api/camera/orientation", json={"rotate_isp_180": True}).json()
    assert body["success"] and body["image_transform"]["rotate_isp_180"] is True
    AllSkyService.reset_instance()


def test_auto_exposure_goes_below_1ms_on_downscaled_live_frames():
    """Regression: a 1152x648 live frame was treated as synthetic and day exposure stuck at 1 ms (white image)."""
    from datetime import datetime, timezone
    from src.calibration.auto_exposure import DayNightAutoExposure

    ae = DayNightAutoExposure(21.0285, 105.8542, initial_exposure_s=0.001, initial_gain_db=0.0)
    white = np.full((648, 1152, 3), 252, np.uint8)
    noon = datetime(2026, 10, 17, 5, 0, tzinfo=timezone.utc)
    for _ in range(8):
        exp, gain, _ = ae.process_frame_feedback(white, noon, live_camera=True)
    assert exp < 0.0005 and gain == 0.0


def test_auto_exposure_no_sawtooth_at_dusk():
    """Regression: at sunset a clipped glow fired the sunrise "fast recovery" on ms exposures (to 1.2 ms, 0 dB),
    so /live went dark, dark, dark, light in a loop. The loop must settle instead."""
    from datetime import datetime, timezone
    from src.calibration.auto_exposure import DayNightAutoExposure

    rad = np.random.default_rng(0).uniform(0.6, 1.4, (648, 1152)).astype(np.float32)
    rad[:, :192] *= 6.0  # bright sunset glow
    dusk = datetime(2026, 10, 18, 10, 33, tzinfo=timezone.utc)
    ae = DayNightAutoExposure(21.0285, 105.8542, initial_exposure_s=0.0084, initial_gain_db=12.0)
    means = []
    for _ in range(40):
        img = np.clip(rad * ae.exposure_s * 10 ** (ae.gain_db / 20) * 18000, 0, 255).astype(np.uint8)
        means.append(float(img.mean()))
        ae.process_frame_feedback(np.dstack([img] * 3), dusk, live_camera=True)
    steps = [b / a for a, b in zip(means, means[1:]) if a > 0]
    assert min(steps) > 0.5  # no single-step crash
    assert max(means[-10:]) - min(means[-10:]) < 2.0  # settled

def test_sky_analysis_throttled_and_recorded_day_and_night(monkeypatch):
    import src.api.service as svc_mod
    import src.calibration.auto_exposure as ae_mod

    AllSkyService.reset_instance()
    svc = AllSkyService.get_instance()
    calls = []

    def fake(frame, exp, gain, sun, roi, reference):
        calls.append(sun)
        return {"star_count": 7 if sun < 0 else 0, "cloud_cover_pct": 12.0, "status": "clear", "method": "night" if sun < 0 else "day", "confidence": 0.8}

    monkeypatch.setattr(svc_mod, "analyze_cloud_cover", fake)
    frame = np.zeros((100, 100, 3), np.uint8)

    monkeypatch.setattr(ae_mod, "calculate_solar_elevation", lambda lat, lon: 20.0)
    assert svc._sky_stats(frame, is_pytest=False)["status"] == "clear" and calls == [20.0]
    svc._sky_stats(frame, is_pytest=False)  # within analysis_interval_s: cached
    assert len(calls) == 1

    monkeypatch.setattr(ae_mod, "calculate_solar_elevation", lambda lat, lon: -30.0)
    svc._sky_stats_cache["_ts"] = 0.0
    assert svc._sky_stats(frame, is_pytest=False)["star_count"] == 7
    hist = svc.get_cloud_history(hours=1)
    assert [p["m"] for p in hist["points"]] == ["day", "night"] and hist["summary"]["mean"] == 12.0
    AllSkyService.reset_instance()


def test_sky_analysis_can_be_disabled_and_interval_set(monkeypatch):
    import src.api.service as svc_mod
    from fastapi.testclient import TestClient
    from src.api.main import create_app

    AllSkyService.reset_instance()
    client = TestClient(create_app())
    svc = AllSkyService.get_instance()
    monkeypatch.setattr(svc, "_save_station_config", lambda: None)
    perf = client.get("/api/camera/performance").json()["current"]
    assert perf["sky_analysis_enabled"] == 1 and perf["analysis_interval_s"] == 300.0

    body = client.post("/api/camera/performance", json={"sky_analysis_enabled": False, "analysis_interval_s": 600}).json()
    assert body["current"]["sky_analysis_enabled"] == 0 and body["current"]["analysis_interval_s"] == 600.0
    calls = []
    monkeypatch.setattr(svc_mod, "analyze_cloud_cover", lambda *a, **k: calls.append(1) or {})
    assert svc._sky_stats(np.zeros((10, 10, 3), np.uint8), is_pytest=True)["status"] == "disabled" and not calls
    AllSkyService.reset_instance()
