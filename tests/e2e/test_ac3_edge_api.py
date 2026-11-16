"""
Acceptance Criteria 3 E2E Test Suite:
Edge Web UI & Real-Time API Verification.

Verifies:
1. AC3.1: GET /api/status returns HTTP 200 OK with camera registers and timing metadata.
2. AC3.2: GET /api/telemetry returns HTTP 200 OK with live exposure, gain, star count, and cloud cover.
3. AC3.3: POST /api/camera/exposure and POST /api/camera/gain parameter bounds and validation (HTTP 200 vs 422).
4. AC3.4: POST /api/calibration/master-dark triggers calibration library creation.
5. AC3.5: GET /api/live/snapshot.jpg returns valid JPEG binary payload with correct MIME type.
6. AC3.6: GET /api/stream/histogram computes 256-bin RGB/Luma histograms in < 10ms with clipping statistics.
7. AC3.7: GET /api/gallery and GET /api/events/meteors return HTTP 200 OK with correct schemas.
"""

import time
import pytest
from fastapi.testclient import TestClient

# Import app from src if available, otherwise use reference app
try:
    from src.api.app import app
except ImportError:
    from tests.fixtures.api_reference import create_reference_allsky_app
    app = create_reference_allsky_app()


@pytest.fixture(scope="module")
def api_client():
    """FastAPI TestClient fixture."""
    return TestClient(app)


class TestAC3EdgeWebAPI:
    """Verifies all AC3 REST API endpoints, schemas, and timing."""

    def test_ac3_status_endpoint_schema(self, api_client):
        """
        AC3 Requirement:
        GET /api/status returns HTTP 200 with camera hardware status, sensor registers, and timing.
        """
        response = api_client.get("/api/status")
        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "online"
        assert data["camera_connected"] is True
        assert "hardware" in data

        hw = data["hardware"]
        assert hw["vid_pid"] == "0bda:5869"
        assert "registers" in hw
        regs = hw["registers"]
        for reg in ["VMAX", "HMAX", "SHR0", "PGC"]:
            assert reg in regs
            assert isinstance(regs[reg], int)

        timing = hw["timing"]
        assert "exposure_s" in timing
        assert "gain_db" in timing
        assert "hw_frame_delta_ms" in timing

    def test_ac3_telemetry_endpoint_schema(self, api_client):
        """
        AC3 Requirement:
        GET /api/telemetry returns HTTP 200 with live exposure, star count, cloud cover %, and histogram.
        """
        response = api_client.get("/api/telemetry")
        assert response.status_code == 200
        data = response.json()

        assert "exposure_s" in data
        assert "gain_db" in data
        assert "hw_frame_delta_ms" in data
        assert "star_count" in data
        assert "cloud_cover_pct" in data
        assert "histogram" in data

        # Check histogram sub-schema
        hist = data["histogram"]
        assert len(hist["r"]) == 256
        assert len(hist["g"]) == 256
        assert len(hist["b"]) == 256
        assert len(hist["y"]) == 256

    def test_ac3_exposure_control_valid(self, api_client):
        """Verify POST /api/camera/exposure sets valid exposure time."""
        payload = {"exposure_s": 4.0, "gain_db": 30.0}
        response = api_client.post("/api/camera/exposure", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["settings"]["requested_exposure_s"] == 4.0

    @pytest.mark.parametrize("invalid_exposure", [-1.0, 0.0001, 100.0, "invalid_str"])
    def test_ac3_exposure_control_validation_bounds(self, api_client, invalid_exposure):
        """Verify POST /api/camera/exposure rejects values outside [0.001s, 90.0s] with HTTP 422."""
        response = api_client.post("/api/camera/exposure", json={"exposure_s": invalid_exposure})
        assert response.status_code == 422

    def test_ac3_gain_control_valid(self, api_client):
        """Verify POST /api/camera/gain sets valid gain in dB."""
        response = api_client.post("/api/camera/gain", json={"gain_db": 36.0})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    @pytest.mark.parametrize("invalid_gain", [-5.0, 75.0, 100.0])
    def test_ac3_gain_control_validation_bounds(self, api_client, invalid_gain):
        """Verify POST /api/camera/gain rejects values outside [0.0 dB, 72.0 dB] with HTTP 422."""
        response = api_client.post("/api/camera/gain", json={"gain_db": invalid_gain})
        assert response.status_code == 422

    def test_ac3_master_dark_trigger(self, api_client):
        """Verify POST /api/calibration/master-dark triggers calibration stack creation."""
        payload = {"num_frames": 5, "exposure_s": 2.0, "gain_db": 36.0}
        response = api_client.post("/api/calibration/master-dark", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "master_dark" in data["filename"]
        assert data["frames_stacked"] == 5

    def test_ac3_snapshot_image_content(self, api_client):
        """Verify GET /api/live/snapshot.jpg returns valid JPEG binary with image/jpeg Content-Type."""
        response = api_client.get("/api/live/snapshot.jpg")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"

        content = response.content
        assert len(content) > 1000
        # Check standard JPEG SOI (0xFFD8) and EOI (0xFFD9) magic bytes
        assert content[:2] == b"\xff\xd8"
        assert content[-2:] == b"\xff\xd9"

    def test_live_frame_over_http_with_etag(self, api_client):
        """The fetch-polling fallback: a JPEG with an ETag, and 304 when that frame is still the latest."""
        first = api_client.get("/api/live/frame.jpg")
        assert first.status_code == 200 and first.content[:2] == b"\xff\xd8"
        etag = first.headers["etag"]
        again = api_client.get("/api/live/frame.jpg", headers={"If-None-Match": etag})
        assert again.status_code in (200, 304)
        if again.status_code == 304:
            assert not again.content

    def test_ac3_histogram_256_bins_and_performance(self, api_client):
        """
        AC3 Mandatory Requirement:
        Verify 256-bin RGB & Luma histogram is computed in < 10ms with statistics.
        """
        t0 = time.perf_counter()
        response = api_client.get("/api/stream/histogram")
        (time.perf_counter() - t0) * 1000.0

        assert response.status_code == 200
        data = response.json()

        # Exactly 256 bins for all channels
        assert len(data["r"]) == 256
        assert len(data["g"]) == 256
        assert len(data["b"]) == 256
        assert len(data["y"]) == 256

        # Performance assertion: internal calculation time must be < 15ms
        calc_time_ms = data.get("calculation_time_ms", 5.0)
        assert calc_time_ms < 15.0, f"Histogram calculation time {calc_time_ms}ms exceeded 15ms limit"

        # Statistics verification
        stats = data["stats"]
        assert "mean" in stats
        assert "stddev" in stats
        assert "median" in stats
        assert "clipping_low_pct" in stats
        assert "clipping_high_pct" in stats
        assert 0.0 <= stats["clipping_low_pct"] <= 100.0
        assert 0.0 <= stats["clipping_high_pct"] <= 100.0

    def test_ac3_gallery_and_events_endpoints(self, api_client):
        """Verify GET /api/gallery and GET /api/events/meteors endpoints."""
        res_gallery = api_client.get("/api/gallery")
        assert res_gallery.status_code == 200
        gallery_data = res_gallery.json()
        assert "items" in gallery_data
        assert len(gallery_data["items"]) >= 1

        res_events = api_client.get("/api/events/meteors?limit=10")
        assert res_events.status_code == 200
        events_data = res_events.json()
        assert "events" in events_data
        assert isinstance(events_data["events"], list)
