"""Plate-solve must never report success (or overwrite station optics) without a real solution."""

import numpy as np

from src.calibration import station_solver as ss


def _frame():
    return np.zeros((120, 160, 3), dtype=np.uint8)


def test_solver_failure_is_reported_not_faked(monkeypatch):
    calls = []

    def fake_post(ep, image_bytes, fields, timeout_s):
        calls.append((ep, image_bytes is not None, dict(fields)))
        return {"success": False, "error": "no stars"}

    monkeypatch.setattr(ss, "_multipart_post_solve", fake_post)
    res = ss.solve_station_parameters(_frame(), 21.0, 105.8, endpoints=["http://a/x", "http://b/x"])
    assert res["success"] is False
    assert "no stars" in res["error"]
    assert "optics" not in res
    # only the live frame is sent (no sample/reference images), and a definite "no solution" stops retries
    assert calls == [("http://a/x", True, calls[0][2])]
    assert "sampleId" not in calls[0][2]


def test_unreachable_endpoint_falls_through_to_next(monkeypatch):
    def fake_post(ep, image_bytes, fields, timeout_s):
        if "a" in ep:
            raise OSError("connection refused")
        return {"success": True, "wcs": {"center_ra": 10.0, "center_dec": 20.0, "pixel_scale": 120.0,
                                          "fov_x_deg": 66.0, "fov_y_deg": 41.0}, "telemetry": {"matched_stars_count": 31}}

    monkeypatch.setattr(ss, "_multipart_post_solve", fake_post)
    spec = {"width_px": 160, "height_px": 120, "pixel_pitch_um": 2.8}
    res = ss.solve_station_parameters(_frame(), 21.0, 105.8, endpoints=["http://a/x", "http://b/x"], sensor_spec=spec)
    assert res["success"] is True
    assert res["solve_source"].startswith("live_frame (http://b/x")
    assert res["camera_sensor"] == spec
    assert res["matched_stars_count"] == 31


def test_service_keeps_config_on_failed_solve(monkeypatch):
    from src.api.service import AllSkyService

    AllSkyService.reset_instance()
    svc = AllSkyService.get_instance()
    monkeypatch.setattr(svc, "_save_station_config", lambda: None)
    monkeypatch.setattr(ss, "_multipart_post_solve", lambda *a, **k: {"success": False, "error": "no stars"})
    before = dict(svc.station_config["optics"])
    res = svc.run_station_plate_solve()
    assert res["success"] is False and "NOT changed" in res["message"]
    assert svc.station_config["optics"] == before
    AllSkyService.reset_instance()


def test_background_plate_solve_job_reports_failure(monkeypatch):
    import time as _t
    from fastapi.testclient import TestClient
    from src.api.main import create_app
    from src.api.service import AllSkyService

    AllSkyService.reset_instance()
    client = TestClient(create_app())
    svc = AllSkyService.get_instance()
    monkeypatch.setattr(svc, "_save_station_config", lambda: None)
    monkeypatch.setattr(ss, "_multipart_post_solve", lambda *a, **k: {"success": False, "error": "no stars"})
    svc.station_config["remote_solver_url"] = "https://solver.example"  # the remote solver is opt-in

    start = client.post("/api/station/plate-solve/start").json()
    assert start["state"] in ("running", "failed")
    for _ in range(100):
        st = client.get("/api/station/plate-solve/status").json()
        if st["state"] != "running":
            break
        _t.sleep(0.05)
    assert st["state"] == "failed" and "no stars" in st["message"]
    assert "station_config" in st
    AllSkyService.reset_instance()
