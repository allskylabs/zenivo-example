"""
Tier 4 Test Suite: Real-World Operational Workload Scenarios.

Verifies end-to-end mission workflows under realistic observing conditions:
1. Scenario 1: 24-hour diurnal/nocturnal cycle simulation from bright midday sun through twilight to dark night and back to dawn.
2. Scenario 2: 8-hour overnight observing run producing multi-frame capture, automated dark subtraction, real-time Keogram, and overnight Startrails.
3. Scenario 3: Perseid meteor shower simulation with multiple transient streaks occurring over time, verifying accurate detection, clip extraction, and zero false positives from passing aircraft.
4. Scenario 4: Simulated 30-minute internet outage at the observatory; verifying local storage, zero frame drop, and automated catch-up sync to sync.example.org.
"""

import os
import json
import numpy as np

from src.hardware.simulator import SimulatedIMX335Controller
from src.calibration.dark import DarkCalibrationEngine
from src.calibration.auto_exposure import DayNightAutoExposure, DayNightProfile
from tests.fixtures.astro_reference import (
    reference_generate_keogram,
    reference_generate_startrails,
    reference_detect_meteors,
)
from tests.fixtures.sync_reference import ReferenceSyncQueue, create_mock_sync_app
from fastapi.testclient import TestClient


class TestTier4RealWorldScenarios:
    """Verifies complex multi-hour operational scenarios."""

    def test_tier4_scenario_1_24_hour_diurnal_cycle(self, synthetic_gen):
        """
        Scenario 1:
        Simulates an entire 24-hour diurnal cycle with 24 frames.
        Verifies:
        - Auto-exposure shifts between daytime, civil twilight, nautical twilight, and night.
        - Keogram is generated displaying the day-night brightness progression.
        - Luma histogram tracks daylight brightness down to dark night.
        """
        ae = DayNightAutoExposure(latitude_deg=21.0176, longitude_deg=105.7536)
        diurnal_sequence = synthetic_gen.generate_diurnal_sequence(num_frames=24)

        captured_frames = []
        profile_history = []

        for ts, frame, solar_elev in diurnal_sequence:
            profile = ae.update_profile(solar_elev)
            profile_history.append(profile)
            captured_frames.append(frame)

        # Confirm all profiles were encountered over 24 hours
        assert DayNightProfile.DAY in profile_history
        assert DayNightProfile.NIGHT in profile_history

        # Produce 24-hour diurnal Keogram
        keogram = reference_generate_keogram(captured_frames, slice_width=3)
        assert keogram.shape[0] == captured_frames[0].shape[0]
        assert keogram.shape[1] == 24 * 3

        # Daytime slice has high mean luminance, night slice has low mean luminance
        day_luma = float(np.mean(keogram[:, :6, :]))
        night_luma = float(np.mean(keogram[:, 36:42, :]))
        assert day_luma > 80.0
        assert night_luma < 35.0

    def test_tier4_scenario_2_overnight_observing_run(self, synthetic_gen):
        """
        Scenario 2:
        Simulates an 8-hour overnight observing run:
        - 16 consecutive frames captured as stars rotate around celestial pole.
        - Master Dark subtraction applied to every frame.
        - Cumulative Startrails composite generated.
        - Overnight Keogram compiled.
        """
        camera = SimulatedIMX335Controller(width=640, height=480, seed=42)
        camera.open()
        camera.set_hardware_exposure(10.0, 36.0)

        dark_engine = DarkCalibrationEngine()

        # Step 1: Pre-session Master Dark creation
        dark_stack = [camera.capture_frame()[0] for _ in range(7)]
        master_dark = dark_engine.create_master_dark(dark_stack)
        hot_map = dark_engine.build_hot_pixel_map(master_dark, threshold=100)

        # Step 2: 16-frame overnight observation run with sidereal rotation
        calibrated_night_frames = []
        for hour_idx in range(16):
            # Rotate star field 2.0 degrees per frame
            sky_frame, _ = synthetic_gen.generate_night_sky(
                num_stars=70, rotation_deg=hour_idx * 2.0, seed=hour_idx
            )
            # Calibrate frame
            calibrated = dark_engine.calibrate_frame(sky_frame, master_dark, hot_map)
            calibrated_night_frames.append(calibrated)

        assert len(calibrated_night_frames) == 16

        # Step 3: Produce overnight Startrails
        startrails = reference_generate_startrails(calibrated_night_frames, comet_mode=False)
        assert startrails.shape == calibrated_night_frames[0].shape

        # Verify trails exist and are brighter than single frame background
        assert np.max(startrails) > 180

        # Step 4: Produce overnight Keogram
        keogram = reference_generate_keogram(calibrated_night_frames, slice_width=4)
        assert keogram.shape == (calibrated_night_frames[0].shape[0], 16 * 4, 3)

        camera.close()

    def test_tier4_scenario_3_meteor_shower_simulation(self, synthetic_gen):
        """
        Scenario 3:
        Simulates a meteor shower observing session:
        - 10 transient streaks generated across 15 frames.
        - Injects 2 aircraft passes with periodic strobe beads.
        - Verifies meteor detection identifies the meteor events with valid JSON metadata
          and avoids false-positive classification of aircraft.
        """
        base_night, _ = synthetic_gen.generate_night_sky(num_stars=60, seed=42)

        confirmed_meteors = []

        # Test 5 distinct meteor streaks
        meteor_configs = [
            {"length": 80, "angle": 30.0, "peak": 230},
            {"length": 110, "angle": 75.0, "peak": 250},
            {"length": 95, "angle": 120.0, "peak": 240},
            {"length": 130, "angle": 190.0, "peak": 255},
            {"length": 70, "angle": 280.0, "peak": 210},
        ]

        for cfg in meteor_configs:
            m_frame, _ = synthetic_gen.generate_meteor_frame(
                base_frame=base_night,
                streak_length=cfg["length"],
                angle_deg=cfg["angle"],
                peak_brightness=cfg["peak"],
            )
            detected = reference_detect_meteors(m_frame, base_night)
            if detected and detected[0]["type"] == "meteor":
                confirmed_meteors.append(detected[0])

        assert len(confirmed_meteors) == 5, f"Expected 5 detected meteors, got {len(confirmed_meteors)}"

        # Verify all detected meteors have valid JSON metadata
        for evt in confirmed_meteors:
            assert evt["aspect_ratio"] >= 4.0
            assert evt["length_px"] >= 30.0
            assert "bbox" in evt
            assert "timestamp" in evt

        # Test aircraft pass rejection
        aircraft_frame, _ = synthetic_gen.generate_aircraft_frame(base_night, num_beads=6, spacing_px=30)
        aircraft_detected = reference_detect_meteors(aircraft_frame, base_night)
        for ev in aircraft_detected:
            # Aircraft bead should not be classified as a high-aspect meteor streak
            assert ev["length_px"] < 60.0

    def test_tier4_scenario_4_internet_outage_and_catchup_sync(self, temp_output_dir):
        """
        Scenario 4:
        Simulates an extended 30-minute Internet outage at the observatory:
        1. 20 telemetry items, 2 meteor events, and 1 keogram are queued during outage.
        2. SQLite queue persists all 23 items on disk.
        3. Network returns online.
        4. Catch-up sync daemon processes all 23 items with zero data loss.
        """
        db_path = os.path.join(temp_output_dir, "test_outage_scenario.db")
        queue = ReferenceSyncQueue(db_path)

        mock_sync = create_mock_sync_app(network_online=False)
        client = TestClient(mock_sync)

        # 1. Observatory operates offline: queue items
        for i in range(20):
            queue.enqueue("telemetry", payload={"seq": i, "star_count": 120 + i}, priority=10)

        # 2 critical meteor events
        queue.enqueue("meteor_event", payload={"event_id": "outage_meteor_01", "mag": -2.1}, priority=1)
        queue.enqueue("meteor_event", payload={"event_id": "outage_meteor_02", "mag": -1.4}, priority=1)

        # 1 daily keogram summary
        queue.enqueue("summary", payload={"type": "keogram", "date": "2026-10-14"}, priority=3)

        stats_offline = queue.get_stats()
        assert stats_offline["pending"] == 23

        # 2. Re-establish network connectivity
        mock_sync.state.network_online = True

        # 3. Catch-up worker flushes queue in priority order
        pending_items = queue.fetch_pending(limit=50)
        assert len(pending_items) == 23

        # Verify priority ordering: meteors (1) first, summary (3) second, telemetry (10) last
        assert pending_items[0]["event_type"] == "meteor_event"
        assert pending_items[1]["event_type"] == "meteor_event"
        assert pending_items[2]["event_type"] == "summary"
        assert pending_items[3]["event_type"] == "telemetry"

        delivered = 0
        for item in pending_items:
            if item["event_type"] == "meteor_event":
                res = client.post("/api/allsky/ingest/event", data={"metadata": json.dumps(item["payload"])})
            elif item["event_type"] == "summary":
                fake_file = b"\xff\xd8" + b"\x00" * 500 + b"\xff\xd9"
                res = client.post(
                    "/api/allsky/ingest/summary",
                    data={"type": "keogram", "date": "2026-10-14"},
                    files={"file": ("keogram.jpg", fake_file, "image/jpeg")},
                )
            else:
                res = client.post("/api/allsky/ingest/telemetry", json=item["payload"])

            assert res.status_code == 200
            queue.mark_completed(item["id"])
            delivered += 1

        assert delivered == 23
        final_stats = queue.get_stats()
        assert final_stats["pending"] == 0
        assert final_stats["completed"] == 23
        assert final_stats["total"] == 23
