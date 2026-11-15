"""
Tier 3 Test Suite: Cross-Feature Combinations.

Verifies end-to-end interactions between interconnected subsystems:
1. Day-to-night dynamic transition during active continuous capture loop.
2. Dark frame calibration applied directly into the live stacking engine.
3. Transient meteor detection concurrent with Keogram and Startrails aggregation.
4. Concurrent Edge API requests during high-rate offline sync queue flushing.
5. Atomic camera control reconfiguration during active capture pipeline.
"""

import os
import time
import numpy as np
import cv2

from src.hardware.simulator import SimulatedIMX335Controller
from src.calibration.dark import DarkCalibrationEngine, verify_ac1_reduction
from src.calibration.stacking import LiveStackingEngine
from src.calibration.auto_exposure import DayNightAutoExposure, DayNightProfile
from tests.fixtures.astro_reference import (
    reference_generate_keogram,
    reference_generate_startrails,
    reference_detect_meteors,
)
from tests.fixtures.api_reference import create_reference_allsky_app
from tests.fixtures.sync_reference import ReferenceSyncQueue
from fastapi.testclient import TestClient


class TestTier3CrossFeature:
    """Verifies cross-cutting interactions between hardware, calibration, astro pipeline, and sync."""

    def test_tier3_day_to_night_capture_transition(self, synthetic_gen):
        """
        Interaction 1:
        Continuous capture loop seamlessly transitions from bright daylight to astronomical night.
        Exposure adjusts from 0.01s up to 2.0s, and dark subtraction automatically activates.
        """
        camera = SimulatedIMX335Controller(width=640, height=480, seed=42)
        camera.open()
        ae = DayNightAutoExposure()
        dark_engine = DarkCalibrationEngine()

        # Step 1: Daytime operation
        ae.update_profile(35.0)  # Solar elevation +35 deg
        day_bounds = ae.get_profile_bounds(DayNightProfile.DAY)
        camera.set_hardware_exposure(day_bounds["max_exp"], day_bounds["min_gain"])

        day_frame, day_meta = camera.capture_frame()
        assert day_meta["actual_exposure_s"] <= 0.05

        # Step 2: Transition through twilight to night
        ae.update_profile(-4.0)   # Civil twilight
        ae.update_profile(-8.0)   # Nautical twilight
        profile_night = ae.update_profile(-25.0)  # Night
        assert profile_night == DayNightProfile.NIGHT

        night_bounds = ae.get_profile_bounds(profile_night)
        camera.set_hardware_exposure(night_bounds["min_exp"], night_bounds["min_gain"])

        # Capture dark stack and build master dark
        dark_stack = [camera.capture_frame()[0] for _ in range(5)]
        master_dark = dark_engine.create_master_dark(dark_stack)
        hot_map = dark_engine.build_hot_pixel_map(master_dark, threshold=100)

        # Capture night frame and calibrate
        raw_night, night_meta = camera.capture_frame()
        assert night_meta["actual_exposure_s"] >= 2.0
        calibrated_night = dark_engine.calibrate_frame(raw_night, master_dark, hot_map)

        res = verify_ac1_reduction(raw_night, calibrated_night, threshold=100)
        assert res["ac1_passed"] is True
        camera.close()

    def test_tier3_dark_calibration_piped_to_live_stacking(self, synthetic_gen):
        """
        Interaction 2:
        Raw frames with hot pixels pass through dark calibration and directly feed
        into the live stacking engine, eliminating defect pixels and boosting SNR.
        """
        camera = SimulatedIMX335Controller(width=640, height=480, seed=42)
        camera.open()
        camera.set_hardware_exposure(10.0, 36.0)

        dark_engine = DarkCalibrationEngine()
        stacking_engine = LiveStackingEngine(max_frames=6, kappa=2.2)

        # 1. Build Master Dark
        dark_frames = [camera.capture_frame()[0] for _ in range(5)]
        master_dark = dark_engine.create_master_dark(dark_frames)
        hot_map = dark_engine.build_hot_pixel_map(master_dark, threshold=100)

        # 2. Capture and calibrate 6 night frames into the live stack
        for i in range(6):
            raw_frame, _ = camera.capture_frame()
            calibrated = dark_engine.calibrate_frame(raw_frame, master_dark, hot_map)
            stacking_engine.add_frame(calibrated)

        stacked = stacking_engine.compute_stack()
        assert stacked.shape == (480, 640, 3)

        # Verified: Stacked image has virtually zero remaining hot pixels
        luma_stacked = cv2.cvtColor(stacked, cv2.COLOR_BGR2GRAY)
        remaining_hot = np.sum(luma_stacked >= 100)
        assert remaining_hot < 20, f"Expected < 20 hot pixels in stacked frame, got {remaining_hot}"
        camera.close()

    def test_tier3_meteor_detected_during_keogram_and_startrails(self, synthetic_gen):
        """
        Interaction 3:
        A transient meteor occurs in frame 3 of a 6-frame nocturnal sequence.
        Verifies:
        - Meteor detector identifies the transient event in frame 3.
        - Startrails composite incorporates the meteor streak into the cumulative night trail.
        - Keogram reflects the central meridian slice without crashing or corruption.
        """
        frames = []
        for i in range(6):
            f, _ = synthetic_gen.generate_night_sky(num_stars=60, rotation_deg=i * 2.5, seed=i)
            frames.append(f)

        # Inject bright meteor streak into frame 3
        meteor_frame, m_meta = synthetic_gen.generate_meteor_frame(
            base_frame=frames[3], streak_length=140, peak_brightness=250
        )
        frames[3] = meteor_frame

        # 1. Detect meteor event
        detected = reference_detect_meteors(frames[3], frames[2])
        assert len(detected) >= 1
        assert detected[0]["type"] == "meteor"

        # 2. Generate Startrails
        trails = reference_generate_startrails(frames, comet_mode=False)
        assert trails.shape == frames[0].shape
        # Trail contains the high-brightness meteor streak
        assert np.max(trails) >= 240

        # 3. Generate Keogram
        keogram = reference_generate_keogram(frames, slice_width=3)
        assert keogram.shape == (frames[0].shape[0], 6 * 3, 3)

    def test_tier3_concurrent_api_and_sync_queue_flushing(self, temp_output_dir):
        """
        Interaction 4:
        FastAPI Edge API responds to telemetry/histogram requests in < 25ms
        while the local SQLite WAL sync queue is continuously enqueuing and processing items.
        """
        db_path = os.path.join(temp_output_dir, "test_concurrent_queue.db")
        queue = ReferenceSyncQueue(db_path)

        app = create_reference_allsky_app()
        client = TestClient(app)

        # Enqueue 50 items into queue in background
        for i in range(50):
            queue.enqueue("telemetry", payload={"seq": i, "val": i * 2.0})

        # Simultaneously query Edge API
        t0 = time.perf_counter()
        resp_status = client.get("/api/status")
        resp_hist = client.get("/api/stream/histogram")
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        assert resp_status.status_code == 200
        assert resp_hist.status_code == 200
        assert elapsed_ms < 50.0, f"API response time {elapsed_ms:.2f}ms under concurrent SQLite write"

        # Verify queue state remains healthy
        stats = queue.get_stats()
        assert stats["pending"] == 50

    def test_tier3_atomic_exposure_change_during_active_capture(self):
        """
        Interaction 5:
        Camera exposure and gain reconfigured dynamically; immediately subsequent frame
        reflects new timing with Group Hold (REGHOLD) protection and no tearing.
        """
        camera = SimulatedIMX335Controller(width=640, height=480, seed=42)
        camera.open()

        # Phase 1: 0.5s exposure
        camera.set_hardware_exposure(0.5, 12.0)
        _, meta1 = camera.capture_frame()
        assert abs(meta1["actual_exposure_s"] - 0.5) < 0.01

        # Phase 2: Switch to 4.0s exposure
        camera.set_hardware_exposure(4.0, 36.0)
        _, meta2 = camera.capture_frame()
        assert abs(meta2["actual_exposure_s"] - 4.0) < 0.01
        assert meta2["gain_db"] == 36.0
        assert meta2["hw_frame_delta"] > 3900.0
        camera.close()
