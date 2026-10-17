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


def test_sky_analysis_skipped_in_daylight_and_throttled_at_night(monkeypatch):
    import src.api.service as svc_mod
    import src.calibration.auto_exposure as ae_mod

    AllSkyService.reset_instance()
    svc = AllSkyService.get_instance()
    calls = []
    monkeypatch.setattr(svc_mod, "estimate_stars_and_clouds", lambda f: calls.append(1) or {"star_count": 7, "cloud_cover_pct": 12.0, "status": "clear"})
    frame = np.zeros((100, 100, 3), np.uint8)

    monkeypatch.setattr(ae_mod, "calculate_solar_elevation", lambda lat, lon: 20.0)
    svc._sun_check_ts = 0.0
    assert svc._sky_stats(frame, is_pytest=False)["status"] == "daylight" and not calls

    monkeypatch.setattr(ae_mod, "calculate_solar_elevation", lambda lat, lon: -30.0)
    svc._sun_check_ts = 0.0
    assert svc._sky_stats(frame, is_pytest=False)["star_count"] == 7
    svc._sky_stats(frame, is_pytest=False)  # within analysis_interval_s: cached
    assert len(calls) == 1
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
    monkeypatch.setattr(svc_mod, "estimate_stars_and_clouds", lambda f: calls.append(1) or {})
    assert svc._sky_stats(np.zeros((10, 10, 3), np.uint8), is_pytest=True)["status"] == "disabled" and not calls
    AllSkyService.reset_instance()
