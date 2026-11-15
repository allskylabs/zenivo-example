"""
Acceptance Criteria 1 E2E Test Suite:
Hardware Camera Control & Dark Subtraction Verification.

Verifies:
1. AC1.1: Frame timing interval (hw_frame_delta) from V4L2 timestamps within 5% error
   at 0.5s (500ms), 2.0s (2000ms), and 10.0s (10000ms).
2. AC1.2: Register mapping and dynamic HMAX scaling (0.001s to 90.0s) and PGC gain (0..72 dB).
3. AC1.3: UVC Extension Unit XDATA unlock (0xC78A=1) and 90.0s exposure ceiling patch (0xC96F).
4. AC1.4: Group Hold atomic register latching (REGHOLD).
5. AC1.5: Master Dark median stacking rejecting transient cosmic rays.
6. AC1.6: Hot pixel map generation at threshold DN >= 100.
7. AC1.7: Dark calibration pipeline achieves >= 90% hot pixel reduction on long exposures (10s - 30s).
8. AC1.8: Real-time Live Stacking SNR boost and sigma-clipping (kappa=2.2).
9. AC1.9: Day/Night Auto-Exposure Ephemeris elevation calculation with hysteresis.
"""

import pytest
import numpy as np

from src.hardware.constants import (
    XDATA_UNLOCK_ADDR,
    XDATA_EXPOSURE_MAX_ADDR,
    REG_REGHOLD,
    REG_VMAX_L,
)
from src.hardware.registers import (
    calculate_exposure_registers,
    calculate_gain_register,
)
from src.hardware.simulator import SimulatedIMX335Controller
from src.hardware.driver import get_camera_driver
from src.calibration.dark import DarkCalibrationEngine, verify_ac1_reduction
from src.calibration.stacking import LiveStackingEngine
from src.calibration.auto_exposure import DayNightAutoExposure, DayNightProfile


class TestAC1HardwareTimingAndExposure:
    """Verifies AC1 hardware control and frame delta accuracy (<5% error)."""

    @pytest.mark.parametrize(
        "target_exposure_s,expected_delta_ms,max_error_pct",
        [
            (0.5, 500.0, 5.0),
            (2.0, 2000.0, 5.0),
            (10.0, 10000.0, 5.0),
        ],
    )
    def test_ac1_hw_frame_delta_timing_accuracy(
        self, target_exposure_s: float, expected_delta_ms: float, max_error_pct: float
    ):
        """
        AC1 Core Requirement:
        hw_frame_delta measured from V4L2 timestamps must match requested exposure
        with error < 5% at 0.5s, 2.0s, and 10.0s.
        """
        controller = SimulatedIMX335Controller(width=640, height=480, seed=42)
        controller.open()
        try:
            reg_info = controller.set_hardware_exposure(target_exposure_s, gain_db=18.0)
            assert reg_info["requested_exposure_s"] == target_exposure_s

            frame, metadata = controller.capture_frame()
            assert isinstance(frame, np.ndarray)
            assert "hw_frame_delta" in metadata

            measured_delta_ms = metadata["hw_frame_delta"]
            error_ms = abs(measured_delta_ms - expected_delta_ms)
            error_pct = (error_ms / expected_delta_ms) * 100.0

            assert (
                error_pct < max_error_pct
            ), f"Timing error {error_pct:.3f}% exceeds 5% limit at {target_exposure_s}s (measured: {measured_delta_ms}ms)"
        finally:
            controller.close()

    def test_ac1_camera_factory_creates_controller(self):
        """Verify get_camera_driver produces a valid CameraDriverInterface with auto-fallback."""
        camera = get_camera_driver(prefer_simulator=True)
        assert hasattr(camera, "open") and hasattr(camera, "capture_frame") and hasattr(camera, "set_hardware_exposure")
        assert camera.open() is True
        info = camera.get_firmware_info()
        assert info["vid_pid"] == "0bda:5869"
        assert "hw_version" in info
        camera.close()

    @pytest.mark.parametrize(
        "exposure_s,expected_hmax,expected_vmax,expected_shr0",
        [
            (0.001, 612, 4044, 3923),    # Sub-frame (1 ms): default VMAX=4044, SHR0=3923
            (0.010, 612, 4044, 2831),    # Sub-frame (10 ms)
            (0.100, 612, 12144, 12),     # Short exposure: VMAX expands, SHR0=12
            (0.500, 612, 60674, 12),     # Mid exposure (0.5s)
            (2.000, 612, 242659, 12),    # Optimal night exposure (2.0s)
            (10.000, 750, 990012, 12),   # Long exposure (10.0s): HMAX scales to 750
            (30.000, 2250, 990012, 12),  # Long exposure (30.0s): HMAX scales to 2250
            (90.000, 6750, 990012, 12),  # Max exposure (90.0s): HMAX scales to 6750
        ],
    )
    def test_ac1_register_mathematics(
        self, exposure_s: float, expected_hmax: int, expected_vmax: int, expected_shr0: int
    ):
        """Verify dynamic HMAX and 20-bit VMAX calculations across the entire 0.001s to 90.0s range."""
        hmax, vmax, shr0, actual_exp = calculate_exposure_registers(exposure_s)
        assert hmax == expected_hmax
        assert vmax == expected_vmax
        assert shr0 == expected_shr0

        # Timing error between requested and actual synthesized exposure must be < 0.5%
        timing_error = abs(actual_exp - exposure_s) / exposure_s
        assert timing_error < 0.005

    @pytest.mark.parametrize(
        "gain_db,expected_pgc",
        [
            (0.0, 0),
            (12.0, 40),
            (36.0, 120),
            (54.0, 180),
            (72.0, 240),
        ],
    )
    def test_ac1_gain_register_calculation(self, gain_db: float, expected_pgc: int):
        """Verify 11-bit PGC Gain mapping (0.3 dB per step, 0..240)."""
        pgc, actual_gain = calculate_gain_register(gain_db)
        assert pgc == expected_pgc
        assert abs(actual_gain - gain_db) < 0.01

    def test_ac1_uvc_xu_unlock_and_ceiling_patch(self):
        """Verify UVC Extension Unit XDATA[0xC78A]=1 unlock and 90.0s RAM ceiling expansion."""
        sim = SimulatedIMX335Controller()
        # Initially locked
        assert sim.is_locked() is True
        with pytest.raises(PermissionError):
            sim.read_xdata(0x0000, 1)

        # Unlock
        sim.unlock_extension_unit()
        assert sim.is_locked() is False
        assert sim.read_xdata(XDATA_UNLOCK_ADDR, 1) == b"\x01"

        # Check exposure ceiling in RAM at 0xC96F: 900,000 (0x000DBBA0 in 100us units -> 90.0s)
        ceiling_bytes = sim.read_xdata(XDATA_EXPOSURE_MAX_ADDR, 4)
        ceiling_val = int.from_bytes(ceiling_bytes, "big")
        assert ceiling_val == 900_000

    def test_ac1_group_hold_register_latching(self):
        """Verify REGHOLD (0x3001) isolates staged writes from active registers until committed."""
        sim = SimulatedIMX335Controller()
        sim.unlock_extension_unit()

        # Initial VMAX_L
        initial_vmax_l = sim.read_sensor_reg(REG_VMAX_L)

        # Stage write under REGHOLD = 1
        sim.write_sensor_reg(REG_REGHOLD, 0x01)
        sim.write_sensor_reg(REG_VMAX_L, 0xAA)

        # Active register must remain unchanged while REGHOLD is asserted
        assert sim.read_sensor_reg(REG_VMAX_L) == initial_vmax_l

        # Latch registers by setting REGHOLD = 0
        sim.write_sensor_reg(REG_REGHOLD, 0x00)
        assert sim.read_sensor_reg(REG_VMAX_L) == 0xAA


class TestAC1DarkSubtractionAndCalibration:
    """Verifies AC1 Dark Subtraction and >=90% Hot Pixel Reduction."""

    def test_ac1_master_dark_generation(self, synthetic_gen):
        """Verify Master Dark generation via pixel-wise median stacking and transient rejection."""
        engine = DarkCalibrationEngine()
        dark_stack, true_hot_mask = synthetic_gen.generate_dark_stack(
            count=9, exposure_s=10.0, gain_db=36.0, num_hot_pixels=100
        )

        master_dark = engine.create_master_dark(dark_stack)
        assert master_dark.shape == dark_stack[0].shape
        assert master_dark.dtype == np.uint8

        # Cosmic ray injected into frame index 2 must be rejected by median
        cy1, cx1 = int(master_dark.shape[0] * 0.4), int(master_dark.shape[1] * 0.4)
        cosmic_region = master_dark[cy1:cy1 + 15, cx1:cx1 + 20]
        assert np.max(cosmic_region) < 100, "Median stacking failed to reject transient cosmic ray"

    def test_ac1_hot_pixel_map_extraction(self, synthetic_gen):
        """Verify Hot Pixel Map extraction with threshold DN >= 100."""
        engine = DarkCalibrationEngine()
        dark_stack, true_hot_mask = synthetic_gen.generate_dark_stack(
            count=9, exposure_s=10.0, gain_db=36.0, num_hot_pixels=120
        )
        master_dark = engine.create_master_dark(dark_stack)
        hot_map = engine.build_hot_pixel_map(master_dark, threshold=100)

        assert hot_map.dtype == bool
        assert np.sum(hot_map) >= 100, f"Expected >= 100 hot pixels, detected {np.sum(hot_map)}"

    @pytest.mark.parametrize("exposure_s", [10.0, 30.0])
    def test_ac1_hot_pixel_reduction_mandate(self, exposure_s: float):
        """
        AC1 Mandatory Acceptance Criteria:
        Pipeline must reduce hot pixels (DN >= 100) on long-exposure dark frames by >= 90%.
        Tested on 10.0s and 30.0s exposures using the high-fidelity simulator.
        """
        sim = SimulatedIMX335Controller(width=640, height=480, seed=42)
        sim.open()
        sim.set_hardware_exposure(exposure_s, gain_db=36.0)

        # 1. Acquire 9 dark frames and generate Master Dark
        dark_frames = [sim.capture_frame()[0] for _ in range(9)]
        engine = DarkCalibrationEngine()
        master_dark = engine.create_master_dark(dark_frames)
        hot_map = engine.build_hot_pixel_map(master_dark, threshold=100)

        # 2. Capture a fresh uncalibrated long-exposure frame
        raw_frame, _ = sim.capture_frame()

        # 3. Apply calibration (Dark subtraction + 2D local median interpolation)
        calibrated_frame = engine.calibrate_frame(raw_frame, master_dark, hot_map)

        # 4. Verify reduction >= 90%
        result = verify_ac1_reduction(raw_frame, calibrated_frame, threshold=100)

        assert result["ac1_passed"] is True, f"AC1 failed: {result}"
        assert result["reduction_percentage"] >= 90.0, (
            f"Reduction percentage {result['reduction_percentage']}% is below 90% threshold"
        )
        sim.close()

    def test_ac1_live_stacking_snr_boost_and_sigma_clipping(self, synthetic_gen):
        """Verify Live Stacking improves SNR and clips transient satellite/airplane streaks."""
        stacking_engine = LiveStackingEngine(max_frames=8, kappa=2.2)

        # Feed 8 frames of a stationary star field
        base_night, stars = synthetic_gen.generate_night_sky(num_stars=80, rotation_deg=0.0)

        for i in range(8):
            # Frame with independent noise
            frame, _ = synthetic_gen.generate_night_sky(num_stars=80, rotation_deg=0.0, seed=i + 10)
            if i == 4:
                # Inject satellite streak across frame 4
                import cv2
                cv2.line(frame, (100, 100), (500, 300), (255, 255, 255), 2)
            stacking_engine.add_frame(frame)

        stacked_frame = stacking_engine.compute_stack()
        assert stacked_frame is not None
        assert stacked_frame.shape == base_night.shape

        # Verify satellite streak was clipped out by sigma-clipping
        streak_pixel = stacked_frame[200, 300]
        assert np.max(streak_pixel) < 180, "Live stacking sigma-clipping failed to suppress outlier streak"

    def test_ac1_day_night_auto_exposure_hysteresis(self):
        """Verify Day/Night auto-exposure ephemeris elevation angle transitions with hysteresis."""
        lat = 21.0176
        lon = 105.7536
        ae_engine = DayNightAutoExposure(latitude_deg=lat, longitude_deg=lon)

        # 1. Midnight: elevation < -12 deg -> NIGHT
        profile = ae_engine.update_profile(-25.0)
        assert profile == DayNightProfile.NIGHT
        bounds = ae_engine.get_profile_bounds(profile)
        assert bounds["min_exp"] >= 2.0
        assert bounds["min_gain"] >= 24.0

        # 2. Nautical Dawn: elevation rises to -8.0 deg -> NAUTICAL_TWILIGHT
        profile = ae_engine.update_profile(-8.0)
        assert profile == DayNightProfile.NAUTICAL_TWILIGHT

        # 3. Civil Dawn: elevation rises to -3.0 deg -> CIVIL_TWILIGHT
        profile = ae_engine.update_profile(-3.0)
        assert profile == DayNightProfile.CIVIL_TWILIGHT

        # 4. Sunrise / Midday: elevation rises to +25.0 deg -> DAY
        profile = ae_engine.update_profile(25.0)
        assert profile == DayNightProfile.DAY
        bounds = ae_engine.get_profile_bounds(profile)
        assert bounds["max_exp"] <= 0.05
        assert bounds["max_gain"] <= 6.0

        # 5. Hysteresis test: Sun dipping slightly below 0 (e.g. -0.5 deg) does NOT immediately flip to civil twilight
        profile = ae_engine.update_profile(-0.5)
        assert profile == DayNightProfile.DAY, "Hysteresis band failed to prevent premature daytime exit at -0.5 deg"
