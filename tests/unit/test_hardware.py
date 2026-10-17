"""
Unit tests for Hardware Layer:
- F1: UVC XU Control & XDATA Unlock
- F2: I2C Sensor Registers & Calculation
- F3: Single-Frame Hardware Capture & hw_frame_delta
- F4: Hardware Simulator Fallback
"""

import os
import pytest
import numpy as np

from src.hardware.constants import (
    SENSOR_WIDTH,
    SENSOR_HEIGHT,
    DEFAULT_HMAX,
    DEFAULT_VMAX,
    MIN_EXPOSURE_S,
    MAX_EXPOSURE_S,
    MIN_GAIN_DB,
    MAX_GAIN_DB,
    REG_REGHOLD,
    REG_VMAX_L,
    REG_VMAX_M,
    REG_VMAX_H,
    REG_HMAX_L,
    REG_HMAX_H,
    REG_SHR0_L,
    REG_SHR0_M,
    REG_SHR0_H,
    REG_PGC_L,
    REG_PGC_H,
    XDATA_UNLOCK_ADDR,
    XDATA_EXPOSURE_MAX_ADDR,
)
from src.hardware.registers import (
    calculate_exposure_registers,
    calculate_gain_register,
    decode_exposure_timing,
    format_timing_summary,
)
from src.hardware.simulator import SimulatedIMX335Controller
from src.hardware.driver import (
    CameraDriverInterface,
    RTS5869CameraController,
    get_camera_driver,
)


class TestRegisterCalculations:
    """Verify F2: IMX335 timing and gain register mathematics."""

    def test_short_subframe_exposure(self):
        """For T <= 0.033s, VMAX remains default 4044 lines and SHR0 adjusts."""
        hmax, vmax, shr0, actual_exp = calculate_exposure_registers(0.010)
        assert hmax == DEFAULT_HMAX
        assert vmax == DEFAULT_VMAX
        # Integration lines = 4044 - shr0
        integration_lines = vmax - shr0
        expected_lines = int(round(0.010 / (DEFAULT_HMAX / 74_250_000.0)))
        assert integration_lines == expected_lines
        # Error must be < 0.1%
        assert abs(actual_exp - 0.010) / 0.010 < 0.001

    def test_minimum_exposure_clamp(self):
        """Clamp to MIN_EXPOSURE_S (0.001s)."""
        hmax, vmax, shr0, actual_exp = calculate_exposure_registers(0.0001)
        assert actual_exp >= 0.0009
        assert shr0 < vmax

    def test_mid_range_long_exposures(self):
        """For 0.1s to 8.0s, HMAX remains 612, VMAX scales up, SHR0 is 12."""
        for target_s in [0.1, 0.5, 2.0, 8.0]:
            hmax, vmax, shr0, actual_exp = calculate_exposure_registers(target_s)
            assert hmax == DEFAULT_HMAX
            assert shr0 == 12
            assert vmax > DEFAULT_VMAX
            error_pct = abs(actual_exp - target_s) / target_s * 100.0
            assert error_pct < 0.01, f"Error at {target_s}s was {error_pct}%"

    def test_extreme_long_exposures_hmax_scaling(self):
        """For T > 8.16s up to 90s, HMAX dynamically scales to keep VMAX ~990,000."""
        for target_s in [10.0, 30.0, 90.0]:
            hmax, vmax, shr0, actual_exp = calculate_exposure_registers(target_s)
            assert hmax > DEFAULT_HMAX
            assert shr0 == 12
            assert vmax <= 0xFFFFF
            error_pct = abs(actual_exp - target_s) / target_s * 100.0
            assert error_pct < 0.05, f"Error at {target_s}s was {error_pct}%"

    def test_maximum_exposure_clamp(self):
        """Clamp to MAX_EXPOSURE_S (90.0s)."""
        hmax, vmax, shr0, actual_exp = calculate_exposure_registers(150.0)
        assert actual_exp <= 90.01
        assert vmax <= 0xFFFFF

    def test_gain_register_calculation(self):
        """PGC register is 11-bit with 0.3 dB per step."""
        # 0 dB -> PGC 0
        pgc, gain_db = calculate_gain_register(0.0)
        assert pgc == 0
        assert gain_db == 0.0

        # 36 dB -> PGC 120
        pgc, gain_db = calculate_gain_register(36.0)
        assert pgc == 120
        assert gain_db == 36.0

        # 72 dB -> PGC 240
        pgc, gain_db = calculate_gain_register(72.0)
        assert pgc == 240
        assert gain_db == 72.0

        # Clamping
        pgc_low, _ = calculate_gain_register(-10.0)
        assert pgc_low == 0
        pgc_high, _ = calculate_gain_register(100.0)
        assert pgc_high == 240

    def test_decode_exposure_timing(self):
        line_time_us, exp_s = decode_exposure_timing(vmax=242659, hmax=612, shr0=12)
        assert abs(line_time_us - 8.242) < 0.01
        assert abs(exp_s - 2.0) < 0.001

    def test_format_timing_summary(self):
        summary = format_timing_summary(hmax=612, vmax=242659, shr0=12, pgc=120)
        assert "VMAX" in summary
        assert "HMAX" in summary
        assert "PGC_GAIN" in summary
        assert summary["actual_gain_db"] == 36.0


class TestSimulatorAndStateLocks:
    """Verify F1 & F4: UVC XU lock mechanisms, register state machine, and simulator fallback."""

    def test_simulator_lock_mechanics(self):
        """Simulator begins locked (XDATA[0xC78A] == 0) and rejects reads until unlocked."""
        sim = SimulatedIMX335Controller()
        assert sim.is_locked() is True

        # Reads must raise PermissionError while locked
        with pytest.raises(PermissionError):
            sim.read_xdata(0xC96B)
        with pytest.raises(PermissionError):
            sim.read_sensor_reg(0x3030)

        # Unlocking sets XDATA[0xC78A] = 1 and raises exposure ceiling
        sim.unlock_extension_unit()
        assert sim.is_locked() is False
        assert sim.read_xdata(XDATA_UNLOCK_ADDR)[0] == 1

        # Reading XDATA and sensor registers succeeds
        assert len(sim.read_xdata(XDATA_EXPOSURE_MAX_ADDR, 4)) == 4
        assert sim.read_sensor_reg(REG_VMAX_L) == (DEFAULT_VMAX & 0xFF)

    def test_simulator_group_hold(self):
        """Registers staged during REGHOLD=1 do not take effect until REGHOLD=0."""
        sim = SimulatedIMX335Controller()
        sim.unlock_extension_unit()

        # Initial HMAX
        orig_hmax_l = sim.read_sensor_reg(REG_HMAX_L)

        # Start group hold
        sim.write_sensor_reg(REG_REGHOLD, 0x01)
        sim.write_sensor_reg(REG_HMAX_L, 0xAA)

        # Active register still shows old value
        assert sim.read_sensor_reg(REG_HMAX_L) == orig_hmax_l

        # Release group hold -> latches updated value
        sim.write_sensor_reg(REG_REGHOLD, 0x00)
        assert sim.read_sensor_reg(REG_HMAX_L) == 0xAA

    def test_simulator_firmware_info(self):
        sim = SimulatedIMX335Controller()
        info = sim.get_firmware_info()
        assert info["vid_pid"] == "0bda:5869"
        assert "fw_version" in info
        assert "spi_flash_jedec" in info

    def test_simulator_frame_synthesis_and_metadata(self):
        """Simulator generates 2592x1944 BGR frames with accurate metadata and hw_frame_delta."""
        sim = SimulatedIMX335Controller()
        sim.set_hardware_exposure(0.5, 18.0)
        img, meta = sim.capture_frame()

        assert img.shape == (SENSOR_HEIGHT, SENSOR_WIDTH, 3)
        assert img.dtype == np.uint8
        assert meta["simulator"] is True
        assert meta["gain_db"] == 18.0
        assert meta["VMAX"] > DEFAULT_VMAX
        assert meta["HMAX"] == DEFAULT_HMAX
        assert meta["SHR0"] == 12

        # Verify AC1 hardware frame delta accuracy: error < 5%
        expected_ms = 500.0
        delta_ms = meta["hw_frame_delta"]
        error_pct = abs(delta_ms - expected_ms) / expected_ms * 100.0
        assert error_pct < 5.0, f"hw_frame_delta error {error_pct}% exceeds 5% threshold"


class TestDriverFactoryAndHardwareCapture:
    """Verify driver factory, contract conformity, and live hardware capture if connected."""

    def test_factory_prefer_simulator(self):
        driver = get_camera_driver(prefer_simulator=True)
        assert isinstance(driver, CameraDriverInterface)
        assert isinstance(driver, SimulatedIMX335Controller)
        driver.close()

    def test_hw_frame_delta_accuracy_at_mandated_milestones(self):
        """
        Verify AC1 Requirement:
        hw_frame_delta measured matches target exposure with < 5% error at 0.5s, 2.0s, and 10.0s.
        """
        driver = get_camera_driver(prefer_simulator=True)

        for target_s in [0.5, 2.0, 10.0]:
            driver.set_hardware_exposure(target_s, 24.0)
            img, meta = driver.capture_frame()

            expected_ms = target_s * 1000.0
            actual_ms = meta["hw_frame_delta"]
            error_pct = abs(actual_ms - expected_ms) / expected_ms * 100.0

            assert error_pct < 5.0, (
                f"Target {target_s}s: measured {actual_ms}ms, "
                f"error {error_pct:.2f}% exceeds 5% requirement!"
            )
        driver.close()

    def test_live_camera_capture_if_available(self):
        """If physical camera 0bda:5869 is connected and accessible, test real hardware capture."""
        driver = get_camera_driver(prefer_simulator=False)
        is_real = isinstance(driver, RTS5869CameraController)
        try:
            cfg = driver.set_hardware_exposure(0.1, 12.0)
            img, meta = driver.capture_frame()
            assert img.shape == (SENSOR_HEIGHT, SENSOR_WIDTH, 3)
            assert img.dtype == np.uint8
            assert "hw_frame_delta" in meta
            # For 0.1s exposure, frame delta should be ~100ms
            if is_real:
                assert abs(meta["hw_frame_delta"] - 100.0) / 100.0 < 0.05
        finally:
            driver.close()
