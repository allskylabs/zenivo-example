"""
Adversarial Stress Test Suite for Hardware Layer (Milestone 1).

Probes:
1. Continuous exposure values (sub-millisecond, boundary transitions at 0.033s, 8.16s, 90.0s, out-of-range >90.0s).
2. Gain mappings from -10 dB up to 100 dB (clipping, rounding, monotonicity, 11-bit bounds).
3. Simulator state machine under rapid concurrent calls, lifecycle transitions, and uninitialized states.
"""

import concurrent.futures
import random
import time
import numpy as np
import pytest

from src.hardware.constants import (
    SENSOR_WIDTH,
    SENSOR_HEIGHT,
    DEFAULT_HMAX,
    DEFAULT_VMAX,
    GAIN_STEP_DB,
    MAX_PGC_REG,
    INCK_HZ,
    REG_REGHOLD,
    REG_VMAX_L,
    REG_HMAX_L,
)
from src.hardware.registers import (
    calculate_exposure_registers,
    calculate_gain_register,
)
from src.hardware.simulator import SimulatedIMX335Controller


class TestAdversarialExposure:
    """Adversarial stress-testing of exposure timing calculations and boundaries."""

    def test_sub_millisecond_continuous_probing(self):
        """Probe sub-millisecond, zero, and negative exposure values for clamping and register validity."""
        test_values = [
            -1000.0,
            -1.0,
            -1e-6,
            -1e-12,
            0.0,
            1e-15,
            1e-9,
            1e-6,
            0.0001,
            0.0005,
            0.00099,
            0.0009999,
            0.001,
        ]
        for exp in test_values:
            hmax, vmax, shr0, act_exp = calculate_exposure_registers(exp)
            # Clamped to MIN_EXPOSURE_S (0.001s)
            assert hmax == DEFAULT_HMAX, f"Expected DEFAULT_HMAX at {exp}, got {hmax}"
            assert vmax == DEFAULT_VMAX, f"Expected DEFAULT_VMAX at {exp}, got {vmax}"
            assert shr0 < vmax, f"SHR0 ({shr0}) must be less than VMAX ({vmax})"
            assert shr0 > 0, f"SHR0 must be positive, got {shr0}"
            # Integration lines: 4044 - shr0
            int_lines = vmax - shr0
            assert int_lines >= 4, f"Integration lines ({int_lines}) must be >= 4"
            # Actual exposure must be within 1 line time (~8.24us) of 1ms (0.1% - 0.3% error)
            assert abs(act_exp - 0.001) < 0.00001, f"Actual exposure {act_exp} too far from 0.001s"

    def test_dense_subframe_sweep_monotonicity(self):
        """Probe dense sweep from 0.001s to 0.050s at 10us steps (4,900 points)."""
        exps = np.linspace(0.001, 0.050, 4901)
        prev_act = 0.0
        inversions = []
        max_err_pct = 0.0

        for exp in exps:
            hmax, vmax, shr0, act = calculate_exposure_registers(float(exp))
            if act < prev_act:
                inversions.append((exp, prev_act, act))
            prev_act = act

            # Quantization error is bounded by 1 line time (~8.24 us)
            line_time = hmax / INCK_HZ
            abs_err = abs(act - exp)
            assert abs_err <= line_time * 1.01, f"Error {abs_err} exceeded 1 line time {line_time}"

            err_pct = abs_err / exp * 100.0
            if err_pct > max_err_pct:
                max_err_pct = err_pct

        assert len(inversions) == 0, f"Found {len(inversions)} monotonicity inversions: {inversions[:5]}"
        assert max_err_pct < 0.5, f"Max error {max_err_pct:.4f}% exceeded 0.5% threshold"

    def test_boundary_transition_at_33ms(self):
        """
        Probe boundary transition around 33.3ms (0.030s to 0.036s) at 5us increments (1,200 points).
        Verifies smooth transition where needed_lines + 12 crosses DEFAULT_VMAX (4044).
        """
        exps = np.linspace(0.030, 0.036, 1201)
        prev_act = 0.0
        inversions = []
        subframe_seen = False
        longexp_seen = False

        for exp in exps:
            hmax, vmax, shr0, act = calculate_exposure_registers(float(exp))
            if act < prev_act:
                inversions.append((exp, prev_act, act))
            prev_act = act

            if shr0 > 12:
                subframe_seen = True
                assert vmax == DEFAULT_VMAX, f"Subframe should have VMAX={DEFAULT_VMAX}, got {vmax}"
            else:
                longexp_seen = True
                assert shr0 == 12, f"Long exposure should lock SHR0=12, got {shr0}"
                assert vmax >= DEFAULT_VMAX, f"Long exposure VMAX must be >= {DEFAULT_VMAX}, got {vmax}"

        assert subframe_seen and longexp_seen, "Must observe both subframe and long exposure modes"
        assert len(inversions) == 0, f"Monotonicity violated across 0.033s boundary: {inversions}"

    def test_boundary_transition_at_8_16s(self):
        """
        Probe boundary transition at ~8.16s (0xF1B30 = 989,999 lines) from 8.10s to 8.25s (1,500 points).
        Verifies dynamic HMAX scaling, VMAX keeping within 20-bit boundary, and zero inversions.
        """
        exps = np.linspace(8.10, 8.25, 1501)
        prev_act = 0.0
        inversions = []
        scaled_hmax_observed = False

        for exp in exps:
            hmax, vmax, shr0, act = calculate_exposure_registers(float(exp))
            if act < prev_act:
                inversions.append((exp, prev_act, act))
            prev_act = act

            assert 0 < hmax <= 0xFFFF, f"HMAX {hmax} out of 16-bit range"
            assert 0 < vmax <= 0xFFFFF, f"VMAX {vmax} out of 20-bit range"
            assert shr0 == 12, f"SHR0 should remain locked at 12, got {shr0}"

            if hmax > DEFAULT_HMAX:
                scaled_hmax_observed = True

            # Error must be < 0.01%
            err_pct = abs(act - exp) / exp * 100.0
            assert err_pct < 0.01, f"Error at {exp}s was {err_pct:.5f}%"

        assert scaled_hmax_observed, "Must observe HMAX scaling above 8.16s"
        assert len(inversions) == 0, f"Monotonicity violated across 8.16s boundary: {inversions[:5]}"

    def test_boundary_transition_at_90s_and_out_of_range(self):
        """Probe boundary at 90.0s and extreme out-of-range values (> 90.0s, up to 1e12s)."""
        # Exact 90.0s
        hmax_90, vmax_90, shr0_90, act_90 = calculate_exposure_registers(90.0)
        assert abs(act_90 - 90.0) < 1e-6, f"90.0s actual exposure was {act_90}"
        assert hmax_90 <= 0xFFFF
        assert vmax_90 <= 0xFFFFF
        assert shr0_90 == 12

        # Out of range values must clamp to exactly 90.0s configuration
        out_of_range_values = [
            90.00001,
            90.01,
            91.0,
            120.0,
            3600.0,
            86400.0,
            1e6,
            1e12,
        ]
        for exp in out_of_range_values:
            hmax, vmax, shr0, act = calculate_exposure_registers(exp)
            assert act == act_90, f"Failed to clamp {exp}s to 90.0s: got {act}"
            assert hmax == hmax_90
            assert vmax == vmax_90
            assert shr0 == shr0_90

    def test_dense_global_exposure_sweep(self):
        """Dense logarithmic sweep across entire dynamic range [0.001s, 90.0s] (10,000 points)."""
        exps = np.geomspace(0.001, 90.0, 10001)
        prev_act = 0.0
        inversions = []
        max_err_pct = 0.0

        for exp in exps:
            hmax, vmax, shr0, act = calculate_exposure_registers(float(exp))
            if act < prev_act:
                inversions.append((exp, prev_act, act))
            prev_act = act

            assert 0 < hmax <= 0xFFFF, f"HMAX {hmax} out of range"
            assert 0 < vmax <= 0xFFFFF, f"VMAX {vmax} out of range"
            assert 0 <= shr0 < vmax, f"Invalid SHR0 {shr0} (VMAX={vmax})"

            err_pct = abs(act - exp) / exp * 100.0
            if err_pct > max_err_pct:
                max_err_pct = err_pct

        assert len(inversions) == 0, f"Global monotonicity failed: {len(inversions)} inversions"
        assert max_err_pct < 0.5, f"Max global error {max_err_pct:.4f}% exceeded 0.5%"

    def test_special_float_exposure_inputs(self):
        """Test resilience against NaN and Infinities."""
        # Inf should clamp to max (90.0s)
        hmax_inf, vmax_inf, shr0_inf, act_inf = calculate_exposure_registers(float("inf"))
        assert act_inf == 90.0

        # -Inf should clamp to min (0.001s)
        hmax_ninf, vmax_ninf, shr0_ninf, act_ninf = calculate_exposure_registers(float("-inf"))
        assert abs(act_ninf - 0.001) < 0.0001

        # NaN should resolve safely without crashing
        hmax_nan, vmax_nan, shr0_nan, act_nan = calculate_exposure_registers(float("nan"))
        assert 0 < hmax_nan <= 0xFFFF
        assert 0 < vmax_nan <= 0xFFFFF


class TestAdversarialGain:
    """Adversarial stress-testing of gain mapping, clipping, rounding, and monotonicity."""

    def test_gain_clipping_negative_and_excess(self):
        """Verify clipping behavior for gain values < 0.0 dB and > 72.0 dB."""
        negative_gains = [-100.0, -50.0, -10.0, -1.0, -0.01, -1e-6, 0.0]
        for g in negative_gains:
            pgc, act = calculate_gain_register(g)
            assert pgc == 0, f"Negative gain {g} must clamp to PGC=0, got {pgc}"
            assert act == 0.0, f"Negative gain {g} must clamp to 0.0 dB, got {act}"

        excess_gains = [72.0, 72.001, 72.1, 75.0, 90.0, 100.0, 1000.0]
        for g in excess_gains:
            pgc, act = calculate_gain_register(g)
            assert pgc == MAX_PGC_REG, f"Excess gain {g} must clamp to PGC={MAX_PGC_REG}, got {pgc}"
            assert act == 72.0, f"Excess gain {g} must clamp to 72.0 dB, got {act}"

    def test_gain_dense_sweep_monotonicity_rounding(self):
        """Dense sweep from -10.0 dB to 100.0 dB in 0.02 dB increments (5,501 points)."""
        gains = np.linspace(-10.0, 100.0, 5501)
        prev_pgc = -1
        prev_act = -1.0
        inversions_pgc = []
        inversions_act = []

        for g in gains:
            pgc, act = calculate_gain_register(float(g))

            # Monotonicity checks
            if pgc < prev_pgc:
                inversions_pgc.append((g, prev_pgc, pgc))
            if act < prev_act:
                inversions_act.append((g, prev_act, act))

            prev_pgc = pgc
            prev_act = act

            # Exact rounding verification
            clamped_g = max(0.0, min(72.0, float(g)))
            expected_pgc = int(round(clamped_g / GAIN_STEP_DB))
            assert pgc == expected_pgc, f"Rounding mismatch at {g} dB: got {pgc}, expected {expected_pgc}"
            assert act == round(expected_pgc * GAIN_STEP_DB, 2)

            # Register validity (11-bit: 0..2047, max used 240)
            assert 0 <= pgc <= MAX_PGC_REG

        assert len(inversions_pgc) == 0, f"PGC monotonicity violated: {inversions_pgc}"
        assert len(inversions_act) == 0, f"Actual gain monotonicity violated: {inversions_act}"

    def test_gain_register_11bit_split(self):
        """Verify 11-bit register splitting into REG_PGC_L (8-bit) and REG_PGC_H (3-bit)."""
        for pgc in range(MAX_PGC_REG + 1):
            pgc_l = pgc & 0xFF
            pgc_h = (pgc >> 8) & 0x07
            reconstructed = pgc_l | (pgc_h << 8)
            assert reconstructed == pgc
            assert 0 <= pgc_h <= 7, f"PGC_H {pgc_h} exceeded 3 bits (0..7)"

    def test_special_float_gain_inputs(self):
        """Test resilience against NaN and Infinities in gain calculations."""
        pgc_inf, act_inf = calculate_gain_register(float("inf"))
        assert pgc_inf == MAX_PGC_REG
        assert act_inf == 72.0

        pgc_ninf, act_ninf = calculate_gain_register(float("-inf"))
        assert pgc_ninf == 0
        assert act_ninf == 0.0

        pgc_nan, act_nan = calculate_gain_register(float("nan"))
        assert 0 <= pgc_nan <= MAX_PGC_REG


class TestAdversarialSimulatorStateMachine:
    """Stress-testing simulator state machine under uninitialized states and concurrency."""

    def test_uninitialized_capture_auto_open(self):
        """Calling capture_frame() without prior open() must auto-open and succeed."""
        sim = SimulatedIMX335Controller()
        assert not sim.is_open

        img, meta = sim.capture_frame()
        assert sim.is_open, "capture_frame() should auto-open"
        assert img.shape == (SENSOR_HEIGHT, SENSOR_WIDTH, 3)
        assert img.dtype == np.uint8
        assert "hw_frame_delta" in meta
        assert meta["simulator"] is True

    def test_uninitialized_registers_default_exposure(self):
        """Default registers before set_hardware_exposure() produce standard ~33ms frame."""
        sim = SimulatedIMX335Controller()
        img, meta = sim.capture_frame()

        assert meta["HMAX"] == DEFAULT_HMAX
        assert meta["VMAX"] == DEFAULT_VMAX
        assert meta["SHR0"] == 12
        assert meta["PGC"] == 0
        assert meta["gain_db"] == 0.0
        # Default ~33.23ms
        assert abs(meta["actual_exposure_s"] - 0.03323) < 0.001

    def test_lifecycle_idempotency(self):
        """Repeated open(), close(), and unlock calls are completely idempotent."""
        sim = SimulatedIMX335Controller()
        for _ in range(5):
            assert sim.open() is True
            assert sim.is_open is True
        for _ in range(5):
            sim.close()
            assert sim.is_open is False
        for _ in range(5):
            assert sim.unlock_extension_unit() is True
            assert sim.is_locked() is False

    def test_lock_state_enforcement(self):
        """PermissionError raised when accessing XDATA / registers while locked."""
        sim = SimulatedIMX335Controller()
        assert sim.is_locked() is True

        with pytest.raises(PermissionError):
            sim.read_xdata(0xC96F, 4)

        with pytest.raises(PermissionError):
            sim.read_sensor_reg(REG_VMAX_L)

        sim.unlock_extension_unit()
        assert sim.is_locked() is False
        assert len(sim.read_xdata(0xC96F, 4)) == 4
        assert isinstance(sim.read_sensor_reg(REG_VMAX_L), int)

    def test_reghold_staged_updates(self):
        """Registers staged during REGHOLD=1 do not commit until REGHOLD=0."""
        sim = SimulatedIMX335Controller()
        sim.unlock_extension_unit()

        orig_val = sim.read_sensor_reg(REG_HMAX_L)
        sim.write_sensor_reg(REG_REGHOLD, 0x01)
        sim.write_sensor_reg(REG_HMAX_L, 0xFE)

        # Still original while staged
        assert sim.read_sensor_reg(REG_HMAX_L) == orig_val

        # Commit
        sim.write_sensor_reg(REG_REGHOLD, 0x00)
        assert sim.read_sensor_reg(REG_HMAX_L) == 0xFE

    def test_concurrent_captures_multithreading(self):
        """
        Stress-test: 8 concurrent threads executing 10 captures each (80 frames total).
        Verifies absence of crashes, deadlocks, NaN values, and invalid dimensions.
        """
        sim = SimulatedIMX335Controller()
        sim.open()
        errors = []

        def capture_task(tid):
            for i in range(10):
                try:
                    img, meta = sim.capture_frame()
                    if img.shape != (SENSOR_HEIGHT, SENSOR_WIDTH, 3):
                        errors.append(f"T{tid}: invalid shape {img.shape}")
                    if img.dtype != np.uint8:
                        errors.append(f"T{tid}: invalid dtype {img.dtype}")
                    if not (0.0 < meta["hw_frame_delta"] < 100000.0):
                        errors.append(f"T{tid}: anomalous hw_frame_delta {meta['hw_frame_delta']}")
                except Exception as e:
                    errors.append(f"T{tid} exception: {e}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            futures = [ex.submit(capture_task, i) for i in range(8)]
            concurrent.futures.wait(futures)

        assert len(errors) == 0, f"Concurrent captures had {len(errors)} errors: {errors[:5]}"

    def test_concurrent_exposure_change_and_capture(self):
        """
        Stress-test: 4 threads hammering set_hardware_exposure() while 4 threads capture frames.
        Verifies register integrity and no race conditions.
        """
        sim = SimulatedIMX335Controller()
        sim.open()
        errors = []
        torn_registers = []
        stop_flag = False

        exposures = [0.001, 0.010, 0.033, 0.1, 0.5, 2.0, 8.0, 10.0]
        gains = [0.0, 12.0, 24.0, 36.0, 72.0]

        def setter_task(tid):
            while not stop_flag:
                exp = random.choice(exposures)
                gain = random.choice(gains)
                try:
                    sim.set_hardware_exposure(exp, gain)
                except Exception as e:
                    errors.append(f"Setter T{tid}: {e}")
                time.sleep(0.0005)

        def capturer_task(tid):
            for _ in range(8):
                try:
                    img, meta = sim.capture_frame()
                    # Verify mathematical consistency of metadata
                    hmax = meta["HMAX"]
                    vmax = meta["VMAX"]
                    shr0 = meta["SHR0"]
                    exp_s = meta["actual_exposure_s"]
                    line_time = hmax / INCK_HZ
                    computed = (vmax - shr0) * line_time
                    if abs(computed - exp_s) > 1e-4:
                        torn_registers.append((hmax, vmax, shr0, exp_s, computed))
                except Exception as e:
                    errors.append(f"Capturer T{tid}: {e}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            setters = [ex.submit(setter_task, i) for i in range(4)]
            capturers = [ex.submit(capturer_task, i) for i in range(4)]
            concurrent.futures.wait(capturers)
            stop_flag = True
            concurrent.futures.wait(setters)

        assert len(errors) == 0, f"Concurrent setter/capturer had errors: {errors}"
        assert len(torn_registers) == 0, f"Observed torn registers: {torn_registers}"

    def test_hardware_frame_delta_tolerance_verification(self):
        """
        AC1 Mandate: hw_frame_delta measured matches target exposure with < 5% error
        at 0.5s, 2.0s, and 10.0s.
        """
        sim = SimulatedIMX335Controller()
        for target_s in [0.5, 2.0, 10.0]:
            sim.set_hardware_exposure(target_s, 18.0)
            img, meta = sim.capture_frame()
            actual_ms = meta["hw_frame_delta"]
            expected_ms = target_s * 1000.0
            error_pct = abs(actual_ms - expected_ms) / expected_ms * 100.0
            assert error_pct < 5.0, (
                f"Target {target_s}s: delta {actual_ms}ms, error {error_pct:.2f}% exceeds 5%"
            )
