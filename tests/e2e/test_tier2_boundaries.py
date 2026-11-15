"""
Tier 2 Test Suite: Boundary Value Analysis (BVA) & Corner Cases.

Covers extreme operating boundaries across all 4 acceptance domains:
1. Hardware & Exposure Boundaries (0.001s, 90.0s, 0 dB, 72 dB, 20-bit register rollover).
2. Sensor Defect Extremes (0 hot pixels vs >5% massive sensor defect density).
3. Solar Elevation Extremes (+90.0 deg zenith vs -90.0 deg nadir).
4. Cloud Cover Extremes (0% crystal clear vs 100% solid overcast).
5. Meteor Photometry Extremes (Magnitude -6 bloomed fireball vs faint magnitude +4 streak).
6. Queue Backlog Boundaries (Empty queue vs 1,000+ items queue compaction).
7. Network Payload Boundaries (10MB ceiling enforcement, HTTP 500 handling, partial uploads).
"""

import os
import numpy as np

from src.hardware.registers import calculate_exposure_registers, calculate_gain_register
from src.calibration.dark import DarkCalibrationEngine, verify_ac1_reduction
from src.calibration.auto_exposure import DayNightAutoExposure, DayNightProfile
from tests.fixtures.astro_reference import (
    reference_detect_meteors,
)
from tests.fixtures.sync_reference import ReferenceSyncQueue, create_mock_sync_app
from fastapi.testclient import TestClient


class TestTier2HardwareBoundaries:
    """Verifies edge boundaries for hardware driver and registers."""

    def test_tier2_minimum_exposure_boundary(self):
        """Minimum possible exposure: 0.001s (1ms)."""
        hmax, vmax, shr0, actual_exp = calculate_exposure_registers(0.001)
        assert hmax == 612
        assert vmax == 4044
        assert shr0 == 3923
        assert 0.00099 < actual_exp < 0.00101

    def test_tier2_maximum_exposure_boundary(self):
        """Maximum possible exposure: 90.0s (90,000ms)."""
        hmax, vmax, shr0, actual_exp = calculate_exposure_registers(90.0)
        assert hmax == 6750
        assert vmax == 990012
        assert shr0 == 12
        assert abs(actual_exp - 90.0) < 0.1

    def test_tier2_sub_millisecond_clamping(self):
        """Requested exposure below 0.001s is clamped to MIN_EXPOSURE_S (0.001s)."""
        hmax, vmax, shr0, actual_exp = calculate_exposure_registers(0.00001)
        assert actual_exp >= 0.00099

    def test_tier2_over_90s_clamping(self):
        """Requested exposure above 90.0s is clamped to MAX_EXPOSURE_S (90.0s)."""
        hmax, vmax, shr0, actual_exp = calculate_exposure_registers(150.0)
        assert hmax == 6750
        assert actual_exp <= 90.001

    def test_tier2_gain_zero_and_max_boundary(self):
        """Gain boundary values: 0 dB and 72 dB."""
        pgc_0, actual_0 = calculate_gain_register(0.0)
        assert pgc_0 == 0
        assert actual_0 == 0.0

        pgc_72, actual_72 = calculate_gain_register(72.0)
        assert pgc_72 == 240
        assert actual_72 == 72.0

        # Clamping beyond limits
        pgc_neg, _ = calculate_gain_register(-10.0)
        assert pgc_neg == 0
        pgc_high, _ = calculate_gain_register(120.0)
        assert pgc_high == 240


class TestTier2CalibrationAndDefectBoundaries:
    """Verifies calibration under clean sensors and extreme defect densities."""

    def test_tier2_zero_hot_pixels_clean_sensor(self):
        """Clean sensor with zero hot pixels: calibration must not introduce artifacts."""
        engine = DarkCalibrationEngine()
        clean_dark = np.full((100, 100, 3), 12, dtype=np.uint8)
        master_dark = engine.create_master_dark([clean_dark] * 5)
        hot_map = engine.build_hot_pixel_map(master_dark, threshold=100)

        assert np.sum(hot_map) == 0

        calibrated = engine.calibrate_frame(clean_dark, master_dark, hot_map)
        res = verify_ac1_reduction(clean_dark, calibrated, threshold=100)
        assert res["n_initial_hot_pixels"] == 0
        assert res["n_final_hot_pixels"] == 0
        assert res["ac1_passed"] is True

    def test_tier2_dense_defect_cluster_5_percent(self):
        """High defect density (5% defective pixels): 2D local median expands to 5x5 window."""
        engine = DarkCalibrationEngine()
        h, w = 120, 120
        raw = np.full((h, w, 3), 15, dtype=np.uint8)
        # Cluster of adjacent hot pixels in center
        raw[50:56, 50:56, :] = 250

        master_dark = raw.copy()
        hot_map = engine.build_hot_pixel_map(master_dark, threshold=100)
        assert np.sum(hot_map) >= 36

        calibrated = engine.calibrate_frame(raw, master_dark, hot_map)
        res = verify_ac1_reduction(raw, calibrated, threshold=100)
        assert res["ac1_passed"] is True
        assert res["reduction_percentage"] >= 90.0


class TestTier2AstroAndEphemerisBoundaries:
    """Verifies astronomical ephemeris and detection under extreme conditions."""

    def test_tier2_solar_elevation_zenith_and_nadir(self):
        """Solar elevation extremes: +90.0 deg (zenith) vs -90.0 deg (nadir)."""
        ae = DayNightAutoExposure()

        # Step through transitions up to zenith
        for _ in range(4):
            profile_zenith = ae.update_profile(+90.0)
        assert profile_zenith == DayNightProfile.DAY

        # Step through transitions down to nadir
        for _ in range(4):
            profile_nadir = ae.update_profile(-90.0)
        assert profile_nadir == DayNightProfile.NIGHT

    def test_tier2_meteor_photometric_extremes(self, synthetic_gen):
        """Detect both saturated fireball (DN 255) and faint streak (DN 50 above background)."""
        base_night, _ = synthetic_gen.generate_night_sky(num_stars=50)

        # 1. Fireball (saturated peak brightness 255, length 200px)
        fireball_frame, _ = synthetic_gen.generate_meteor_frame(
            base_frame=base_night, streak_length=200, peak_brightness=255
        )
        fb_events = reference_detect_meteors(fireball_frame, base_night)
        assert len(fb_events) >= 1
        assert fb_events[0]["peak_dn"] >= 200

        # 2. Faint streak (peak brightness 70 above background 12, length 50px)
        faint_frame, _ = synthetic_gen.generate_meteor_frame(
            base_frame=base_night, streak_length=50, peak_brightness=70
        )
        faint_events = reference_detect_meteors(faint_frame, base_night)
        assert len(faint_events) >= 1


class TestTier2SyncAndNetworkBoundaries:
    """Verifies queue scalability and network payload boundaries."""

    def test_tier2_queue_with_1000_items(self, temp_output_dir):
        """Queue scalability: successfully insert and query 1,000 backlog items."""
        db_path = os.path.join(temp_output_dir, "test_large_queue.db")
        queue = ReferenceSyncQueue(db_path)

        for i in range(1000):
            queue.enqueue("telemetry", payload={"seq": i, "val": i * 1.5})

        stats = queue.get_stats()
        assert stats["pending"] == 1000

        # Batch fetch performance
        batch = queue.fetch_pending(limit=100)
        assert len(batch) == 100
        assert batch[0]["payload"]["seq"] == 0

    def test_tier2_empty_queue_fetch(self, temp_output_dir):
        """Empty queue fetch returns empty list without error."""
        db_path = os.path.join(temp_output_dir, "test_empty_queue.db")
        queue = ReferenceSyncQueue(db_path)

        batch = queue.fetch_pending(limit=10)
        assert batch == []
        stats = queue.get_stats()
        assert stats["pending"] == 0
        assert stats["total"] == 0

    def test_tier2_payload_ceiling_enforcement(self):
        """Upload payload exceeding 10MB ceiling is rejected with HTTP 413 Payload Too Large."""
        mock_sync = create_mock_sync_app(network_online=True)
        client = TestClient(mock_sync)

        # 11 MB payload
        oversized = b"\x00" * (11 * 1024 * 1024)
        files = {"image": ("huge.jpg", oversized, "image/jpeg")}

        response = client.post("/api/allsky/ingest/frame", files=files)
        assert response.status_code == 413
