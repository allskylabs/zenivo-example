"""
Unit tests for Calibration Layer:
- F5: Master Dark Generation via median stacking
- F6: Hot Pixel Map Extraction & 2D Local Median Interpolation
- AC1: Verification of >= 90% reduction of hot pixels on long exposures
- F7: Live Stacking Engine with Sigma-Clipping (kappa = 2.2)
- F8: Day/Night Auto-Exposure with Ephemeris & Luminance Feedback
"""

from datetime import datetime, timezone
import pytest
import numpy as np

from src.calibration.dark import (
    DarkCalibrationEngine,
    verify_ac1_reduction,
)
from src.calibration.stacking import (
    LiveStackingEngine,
)
from src.calibration.auto_exposure import (
    DayNightProfile,
    DayNightAutoExposure,
    calculate_solar_elevation,
)
from src.hardware.simulator import SimulatedIMX335Controller


class TestMasterDarkGeneration:
    """Verify F5: Master Dark median stacking."""

    def test_median_stacking_rejects_cosmic_rays(self):
        """Median stacking across frames eliminates transient cosmic ray hits."""
        engine = DarkCalibrationEngine()
        h, w = 100, 100
        # 9 dark frames with Gaussian background noise ~15 DN
        rng = np.random.RandomState(42)
        frames = [
            np.clip(rng.normal(15, 2, (h, w, 3)), 0, 255).astype(np.uint8)
            for _ in range(9)
        ]

        # Add single cosmic ray streak on frame 4
        frames[4][50:55, 50, :] = 250

        master_dark = engine.create_master_dark(frames)
        assert master_dark.shape == (h, w, 3)
        assert master_dark.dtype == np.uint8

        # Cosmic ray must be completely rejected by median
        assert np.all(master_dark[50:55, 50, :] < 30)
        # Background mean should be close to 15
        assert 12 <= np.mean(master_dark) <= 18

    def test_empty_list_and_shape_mismatch_raise_error(self):
        engine = DarkCalibrationEngine()
        with pytest.raises(ValueError):
            engine.create_master_dark([])

        f1 = np.zeros((10, 10, 3), dtype=np.uint8)
        f2 = np.zeros((20, 20, 3), dtype=np.uint8)
        with pytest.raises(ValueError):
            engine.create_master_dark([f1, f2])


class TestHotPixelMappingAndInterpolation:
    """Verify F6: Hot pixel mask extraction and 2D local median repair."""

    def test_hot_pixel_mask_extraction(self):
        engine = DarkCalibrationEngine()
        dark = np.ones((50, 50, 3), dtype=np.uint8) * 15
        # Set 3 hot pixels
        dark[10, 10, :] = 120
        dark[25, 25, 0] = 150  # Hot in Blue channel
        dark[40, 40, :] = 95   # Just below threshold 100

        mask = engine.build_hot_pixel_map(dark, threshold=100)
        assert mask.shape == (50, 50)
        assert mask.dtype == bool
        assert mask[10, 10] is True or mask[10, 10] == 1
        assert mask[25, 25] is True or mask[25, 25] == 1
        assert mask[40, 40] is False or mask[40, 40] == 0

    def test_local_median_interpolation_replaces_defects(self):
        engine = DarkCalibrationEngine()
        h, w = 60, 60
        # Background clean image with gradient
        raw = np.ones((h, w, 3), dtype=np.uint8) * 20
        master_dark = np.ones((h, w, 3), dtype=np.uint8) * 5

        # Create defect pixel in raw and dark
        raw[30, 30, :] = 210
        master_dark[30, 30, :] = 180

        mask = engine.build_hot_pixel_map(master_dark, threshold=100)
        calibrated = engine.calibrate_frame(raw, master_dark, mask, pedestal=0)

        # Defect at (30, 30) should be replaced by neighbor median (~ 20 - 5 = 15)
        assert calibrated[30, 30, 0] < 30
        assert calibrated[30, 30, 1] < 30
        assert calibrated[30, 30, 2] < 30

    def test_dense_cluster_fallback(self):
        """When entire 3x3 neighborhood is defective, engine expands to 5x5 window."""
        engine = DarkCalibrationEngine()
        h, w = 40, 40
        raw = np.ones((h, w), dtype=np.uint8) * 25
        master_dark = np.ones((h, w), dtype=np.uint8) * 5

        # 3x3 block of defects centered at (20, 20)
        raw[19:22, 19:22] = 220
        master_dark[19:22, 19:22] = 200

        mask = engine.build_hot_pixel_map(master_dark, threshold=100)
        calibrated = engine.calibrate_frame(raw, master_dark, mask)

        # Center defect must be successfully interpolated from the 5x5 perimeter
        assert calibrated[20, 20] < 35


class TestAC1AcceptanceCriteriaVerification:
    """
    Mandatory Acceptance Criteria 1 (AC1):
    The Master Dark + Hot Pixel Map pipeline removes at least 90% of hot pixels
    (pixel >= 100) on long exposures (10s – 30s) compared with the uncorrected raw frame.
    """

    @pytest.mark.parametrize("exposure_s,gain_db", [(10.0, 36.0), (30.0, 36.0)])
    def test_ac1_reduction_on_long_exposures(self, exposure_s, gain_db):
        sim = SimulatedIMX335Controller(width=640, height=480, seed=101)
        sim.set_hardware_exposure(exposure_s, gain_db)

        # Acquire 9 dark frames for master dark library
        dark_stack = [sim.capture_frame()[0] for _ in range(9)]
        # Acquire raw test frame under identical conditions
        raw_test, meta = sim.capture_frame()

        engine = DarkCalibrationEngine()
        master_dark = engine.create_master_dark(dark_stack)
        hot_mask = engine.build_hot_pixel_map(master_dark, threshold=100)
        calibrated = engine.calibrate_frame(raw_test, master_dark, hot_mask)

        # Run automated AC1 verification
        report = verify_ac1_reduction(raw_test, calibrated, threshold=100)

        assert report["ac1_passed"] is True, f"AC1 failed: {report}"
        assert report["reduction_percentage"] >= 90.0, (
            f"Reduction {report['reduction_percentage']}% is below mandatory 90% target! "
            f"Initial hot: {report['n_initial_hot_pixels']}, Remaining: {report['n_final_hot_pixels']}"
        )


class TestLiveStackingEngine:
    """Verify F7: Live stacking with sigma-clipping (kappa = 2.2)."""

    def test_live_stacking_rejects_satellite_streak(self):
        engine = LiveStackingEngine(max_frames=5, kappa=2.2)
        h, w = 50, 50

        # Feed 5 frames with background value 40
        for i in range(5):
            frame = np.ones((h, w, 3), dtype=np.uint8) * 40
            if i == 2:
                # Frame 2 has bright satellite streak across row 25
                frame[25, :, :] = 240
            engine.add_frame(frame)

        stacked = engine.compute_stack()
        assert stacked.shape == (h, w, 3)

        # Pixel on streak must be clipped and remain at clean background ~40
        assert np.all(stacked[25, :, :] == 40)
        assert np.all(stacked[10, :, :] == 40)

    def test_additive_stacking_mode(self):
        engine = LiveStackingEngine(max_frames=4)
        for _ in range(4):
            engine.add_frame(np.ones((20, 20, 3), dtype=np.uint8) * 30)
        accum = engine.compute_additive_stack()
        # 30 * 4 = 120
        assert np.all(accum == 120)

    def test_single_frame_and_empty_buffer(self):
        engine = LiveStackingEngine(max_frames=5)
        with pytest.raises(ValueError):
            engine.compute_stack()

        f = np.ones((10, 10, 3), dtype=np.uint8) * 75
        res = engine.add_frame(f)
        assert np.all(res == 75)


class TestDayNightAutoExposureEngine:
    """Verify F8: Astronomical Ephemeris solar elevation, hysteresis, and luminance feedback."""

    def test_solar_elevation_calculation(self):
        # Hanoi coordinates: 21.0285 N, 105.8542 E
        # Solar Noon in Hanoi (~11:50 AM UTC+7 => ~04:50 UTC)
        noon_dt = datetime(2026, 6, 21, 4, 50, tzinfo=timezone.utc)
        elev_noon = calculate_solar_elevation(21.0285, 105.8542, noon_dt)
        # On summer solstice at solar noon in Hanoi, Sun is near zenith (> 80 deg)
        assert elev_noon > 80.0

        # Midnight in Hanoi (~17:00 UTC)
        night_dt = datetime(2026, 6, 21, 17, 0, tzinfo=timezone.utc)
        elev_night = calculate_solar_elevation(21.0285, 105.8542, night_dt)
        assert elev_night < -40.0

    def test_hysteresis_state_transitions(self):
        ae = DayNightAutoExposure(latitude_deg=21.0285, longitude_deg=105.8542)

        # Start in day
        ae.current_profile = DayNightProfile.DAY
        # At -0.5 deg, should remain DAY due to 1.0 deg hysteresis
        profile = ae.update_profile(-0.5)
        assert profile == DayNightProfile.DAY

        # Drops below -1.0 deg -> transitions to CIVIL_TWILIGHT
        profile = ae.update_profile(-1.5)
        assert profile == DayNightProfile.CIVIL_TWILIGHT

        # Drops below -7.0 deg -> transitions to NAUTICAL_TWILIGHT
        profile = ae.update_profile(-7.5)
        assert profile == DayNightProfile.NAUTICAL_TWILIGHT

        # Drops below -13.0 deg -> transitions to NIGHT
        profile = ae.update_profile(-14.0)
        assert profile == DayNightProfile.NIGHT

    def test_closed_loop_luminance_feedback(self):
        ae = DayNightAutoExposure(latitude_deg=21.0285, longitude_deg=105.8542)
        ae.current_profile = DayNightProfile.NIGHT
        ae.exposure_s = 5.0
        ae.gain_db = 30.0

        # Midnight timestamp
        night_dt = datetime(2026, 6, 21, 17, 0, tzinfo=timezone.utc)

        # Over-exposed night frame (median luma 80 vs target 25)
        bright_frame = np.ones((200, 200, 3), dtype=np.uint8) * 80
        new_exp, new_gain, telem = ae.process_frame_feedback(bright_frame, night_dt)

        # Exposure should decrease
        assert new_exp < 5.0
        assert telem["profile"] == DayNightProfile.NIGHT

        # Saturated highlight protection: test frame with P98 > 240
        ae.exposure_s = 6.0
        glare_frame = np.ones((200, 200, 3), dtype=np.uint8) * 30
        glare_frame[0:40, 0:40, :] = 255  # bright highlight spot (4% of frame, triggers P98)
        exp_before = ae.exposure_s
        new_exp, new_gain, telem = ae.process_frame_feedback(glare_frame, night_dt)
        assert new_exp <= exp_before * 0.75  # immediate 30% reduction triggered
