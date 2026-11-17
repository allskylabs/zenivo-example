"""
Comprehensive Unit Tests for IMX335 AllSky Edge REST API & Streaming Engine.
Covers:
- F15: REST endpoints (/api/status, /api/telemetry, /api/camera/exposure, /api/camera/gain, /api/camera/mode)
- F16: Live streaming (MJPEG multipart, WebSockets /ws/live, <10ms histogram calculation)
- F17: Calibration manager (/api/calibration/master-dark) and gallery/events viewer
"""

import time
import pytest
from fastapi.testclient import TestClient

from src.hardware.simulator import SimulatedIMX335Controller
from src.api.service import AllSkyService
from src.api.main import create_app


@pytest.fixture(scope="module")
def sim_camera():
    """Provides a fresh simulated camera controller."""
    cam = SimulatedIMX335Controller(width=640, height=480, seed=42)
    cam.open()
    yield cam
    cam.close()


@pytest.fixture(scope="module")
def api_client(sim_camera):
    """Provides a FastAPI TestClient configured with sim_camera."""
    AllSkyService.reset_instance()
    service = AllSkyService.get_instance(camera=sim_camera)
    app = create_app(camera=sim_camera)
    with TestClient(app) as client:
        yield client
    service.close()
    AllSkyService.reset_instance()


class TestStatusAndTelemetryAPI:
    """Verifies camera status, telemetry schemas, and hardware metadata."""

    def test_status_endpoint_returns_online(self, api_client):
        res = api_client.get("/api/status")
        assert res.status_code == 200
        data = res.json()

        assert data["status"] == "online"
        assert data["camera_connected"] is True
        assert "uptime_s" in data
        assert "simulator_mode" in data
        assert "mode" in data

        hw = data["hardware"]
        assert hw["vid_pid"] == "0bda:5869"
        assert "registers" in hw
        for reg in ["VMAX", "HMAX", "SHR0", "PGC"]:
            assert reg in hw["registers"]
            assert isinstance(hw["registers"][reg], int)

        timing = hw["timing"]
        assert "exposure_s" in timing
        assert "gain_db" in timing
        assert "hw_frame_delta_ms" in timing

    def test_telemetry_endpoint_structure(self, api_client):
        res = api_client.get("/api/telemetry")
        assert res.status_code == 200
        data = res.json()

        assert "exposure_s" in data
        assert "gain_db" in data
        assert "star_count" in data
        assert "cloud_cover_pct" in data
        assert "fps" in data
        assert "sensor_temp_c" in data
        assert "histogram" in data

        hist = data["histogram"]
        for ch in ["r", "g", "b", "y"]:
            assert len(hist[ch]) == 256


class TestHardwareControlAPI:
    """Verifies camera parameter controls, bounds checking, and validation."""

    def test_set_valid_exposure(self, api_client):
        for exp in [0.005, 1.0, 15.0, 90.0]:
            res = api_client.post("/api/camera/exposure", json={"exposure_s": exp, "gain_db": 30.0})
            assert res.status_code == 200
            data = res.json()
            assert data["success"] is True
            assert data["settings"]["requested_exposure_s"] == exp

    @pytest.mark.parametrize("invalid_exp", [0.00001, -1.0, 90.01, 200.0, "not_a_number"])
    def test_set_invalid_exposure_rejected(self, api_client, invalid_exp):
        res = api_client.post("/api/camera/exposure", json={"exposure_s": invalid_exp})
        assert res.status_code == 422

    def test_set_valid_gain(self, api_client):
        for gain in [0.0, 18.0, 36.0, 72.0]:
            res = api_client.post("/api/camera/gain", json={"gain_db": gain})
            assert res.status_code == 200
            data = res.json()
            assert data["success"] is True

    @pytest.mark.parametrize("invalid_gain", [-0.5, -10.0, 72.05, 100.0, "high"])
    def test_set_invalid_gain_rejected(self, api_client, invalid_gain):
        res = api_client.post("/api/camera/gain", json={"gain_db": invalid_gain})
        assert res.status_code == 422

    def test_set_mode_toggle(self, api_client):
        res_auto = api_client.post("/api/camera/mode", json={"mode": "auto"})
        assert res_auto.status_code == 200
        assert res_auto.json()["mode"] == "auto"

        res_manual = api_client.post("/api/camera/mode", json={"mode": "manual"})
        assert res_manual.status_code == 200
        assert res_manual.json()["mode"] == "manual"

        # Test toggle alias
        res_toggle = api_client.post("/api/camera/auto")
        assert res_toggle.status_code == 200
        assert res_toggle.json()["mode"] == "auto"


class TestCalibrationAPI:
    """Verifies Master Dark calibration trigger and parameter bounds."""

    def test_trigger_master_dark_success(self, api_client):
        payload = {"num_frames": 4, "exposure_s": 1.0, "gain_db": 30.0}
        res = api_client.post("/api/calibration/master-dark", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert "master_dark" in data["filename"]
        assert data["frames_stacked"] == 4
        assert "hot_pixels_detected" in data

    @pytest.mark.parametrize("invalid_frames", [1, 2, 51, 100])
    def test_trigger_master_dark_validation(self, api_client, invalid_frames):
        payload = {"num_frames": invalid_frames, "exposure_s": 2.0, "gain_db": 36.0}
        res = api_client.post("/api/calibration/master-dark", json=payload)
        assert res.status_code == 422


class TestSnapshotAndHistogramAPI:
    """Verifies live snapshot binary payload and histogram calculation engine."""

    def test_live_snapshot_jpg(self, api_client):
        res = api_client.get("/api/live/snapshot.jpg")
        assert res.status_code == 200
        assert res.headers["content-type"] == "image/jpeg"
        content = res.content
        assert len(content) > 1000
        assert content.startswith(b"\xff\xd8")
        assert content.endswith(b"\xff\xd9")

    def test_histogram_performance_and_statistics(self, api_client):
        t0 = time.perf_counter()
        res = api_client.get("/api/stream/histogram")
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        assert res.status_code == 200
        data = res.json()

        for ch in ["r", "g", "b", "y"]:
            assert len(data[ch]) == 256
            assert all(isinstance(v, int) for v in data[ch])

        assert data["calculation_time_ms"] < 15.0
        assert elapsed_ms < 100.0

        stats = data["stats"]
        assert 0.0 <= stats["clipping_low_pct"] <= 100.0
        assert 0.0 <= stats["clipping_high_pct"] <= 100.0
        assert stats["mean"] >= 0.0
        assert stats["stddev"] >= 0.0


class TestGalleryAndEventsAPI:
    """Verifies gallery listing and meteor events endpoint."""

    def test_gallery_listing(self, api_client):
        res = api_client.get("/api/gallery")
        assert res.status_code == 200
        data = res.json()
        assert "date" in data
        assert "items" in data
        assert len(data["items"]) >= 1

    def test_meteor_events_endpoint(self, api_client):
        res = api_client.get("/api/events/meteors?limit=10")
        assert res.status_code == 200
        data = res.json()
        assert "events" in data
        assert "total_count" in data
        assert isinstance(data["events"], list)

    def test_sky_event_recording_config_and_images(self, api_client, monkeypatch):
        import numpy as np

        service = AllSkyService.get_instance()
        monkeypatch.setattr(service, "_save_station_config", lambda: None)
        monkeypatch.setitem(service.station_config, "event_detection", {})
        cfg = api_client.get("/api/events/config").json()
        assert cfg["enabled"] == {"meteor": 1, "plane": 1, "satellite": 1, "clouds": 0, "flash": 1}
        res = api_client.post("/api/events/config", json={"satellite": True, "plane": False})
        assert res.status_code == 200 and res.json()["enabled"]["satellite"] == 1 and res.json()["enabled"]["plane"] == 0

        rec = service._record_sky_event({
            "type": "meteor", "ts": 1792245758.6, "start_ts": 1792245758.6, "end_ts": 1792245758.6, "frames": 1,
            "length_px": 120.0, "peak_dn": 90, "mean_dn": 40.0, "dashes": 1, "line": [0.1, 0.2, 0.3, 0.4],
            "bbox": [10, 20, 100, 60], "_crop": np.zeros((60, 100, 3), np.uint8), "_overview": np.zeros((360, 640, 3), np.uint8),
        })
        data = api_client.get("/api/events?limit=5&type=meteor").json()
        assert data["events"][0]["event_id"] == rec["event_id"]
        assert data["counts"]["meteor"] >= 1
        img = api_client.get(rec["thumbnail_url"])
        assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
        assert api_client.get(rec["sky_url"]).status_code == 200
        assert api_client.get("/api/events/img/2026-10-17/..%2Fstation_config.json").status_code == 404
        assert api_client.get("/api/events?type=bogus").status_code == 422

    def test_detect_sky_events_at_night_records_meteor(self, api_client, monkeypatch):
        from tests.unit.test_sky_events import _sky
        import cv2

        service = AllSkyService.get_instance()
        monkeypatch.setitem(service.station_config, "sky_mask", None)
        monkeypatch.setitem(service.station_config, "event_detection", {"meteor": 1})
        service.sky_events.reset()
        service._events_night, service._events_sun_ts = True, 2e9  # pretend night, skip the sun check
        meta = {"actual_exposure_s": 10.0, "gain_db": 30.0}
        frames = [_sky(i, i * 0.5) for i in range(4)]
        cv2.line(frames[1], (300, 200), (520, 330), (230, 230, 230), 2)
        before = api_client.get("/api/events?type=meteor").json()["total_count"]
        for i, f in enumerate(frames):
            service._detect_sky_events(f, meta, 1_800_000_000.0 + 15 * i)
        assert api_client.get("/api/events?type=meteor").json()["total_count"] == before + 1


class TestStreamingAndWebSockets:
    """Verifies MJPEG video stream and WebSocket broadcast."""

    def test_mjpeg_stream_headers(self, api_client):
        # Read initial chunk of MJPEG stream with limit=1 so it terminates cleanly
        with api_client.stream("GET", "/api/stream/mjpeg?limit=1") as stream:
            assert stream.status_code == 200
            assert "multipart/x-mixed-replace" in stream.headers["content-type"]
            chunk = next(stream.iter_bytes())
            assert b"--frame" in chunk
            assert b"Content-Type: image/jpeg" in chunk

    def test_websocket_pushes_subscribed_overlay_layers(self, api_client):
        with api_client.websocket_connect("/ws/live") as websocket:
            assert websocket.receive_json()["type"] == "welcome"
            websocket.send_text('{"action": "subscribe", "layers": ["aircraft", "bogus"]}')
            seen = {}
            for _ in range(40):
                msg = websocket.receive_json()
                seen[msg["type"]] = msg
                if "overlay" in seen and "subscribed" in seen:
                    break
            assert seen["subscribed"]["layers"] == ["aircraft"]
            assert seen["overlay"]["layer"] == "aircraft" and "aircraft" in seen["overlay"]["data"]

    def test_websocket_live_connection(self, api_client):
        with api_client.websocket_connect("/ws/live") as websocket:
            # First message should be welcome payload
            initial = websocket.receive_json()
            assert initial["type"] == "welcome"
            assert "status" in initial
            assert "telemetry" in initial

            # Test ping/pong
            websocket.send_text('{"action": "ping"}')
            reply = websocket.receive_json()
            assert reply["type"] == "pong"
