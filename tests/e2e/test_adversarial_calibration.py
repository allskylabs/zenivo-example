"""
Adversarial Calibration Stress Test Suite for Milestone 1.
Challenger 2 (challenger_m1_2) empirical verification harness.

Scope:
1. AC1 Requirement Stress Testing: Verifies >= 90% hot pixel reduction across a parameter
   grid of exposure times (10s, 20s, 30s) and gain settings (0 dB, 12 dB, 24 dB, 36 dB).
2. Defect Corner Cases:
   - Extreme hot pixel densities: 1%, 5%, 10% on sensor arrays.
   - Contiguous defect clusters: 2x2 and 3x3 defect blocks, verifying 3x3 local median
     and 5x5 adaptive window expansion.
   - Sensor boundary and corner pixels: (0,0), (0, W-1), (H-1, 0), (H-1, W-1), and edge lines.
   - Degenerate sensor states: Zero hot pixels (pristine sensor), all-black frame (0 DN),
     all-white frame (255 DN).
3. Live Stacking Outlier Rejection:
   - Artificial satellite streaks (DN 255) across horizontal, vertical, and diagonal paths.
   - Multi-frame dynamic satellite movement across frames.
   - Buffer size dynamics for sigma-clipping outlier rejection.
   - Preservation of real astronomical point sources (bright stars) while rejecting transient streaks.
"""

import pytest
import numpy as np

from src.hardware.simulator import SimulatedIMX335Controller
from src.calibration.dark import (
    DarkCalibrationEngine,
    verify_ac1_reduction,
)
from src.calibration.stacking import (
    LiveStackingEngine,
    stack_frames_sigma_clipped,
)


class TestAdversarialAC1Reduction:
    """
    Adversarial verification of Acceptance Criteria AC1:
    The Master Dark + Hot Pixel Map pipeline removes at least 90% of hot pixels
    (pixel >= 100) on long exposures (10s - 30s) compared with the uncorrected raw frame.
    """

    @pytest.mark.parametrize("exposure_s", [10.0, 20.0, 30.0])
    @pytest.mark.parametrize("gain_db", [0.0, 12.0, 24.0, 36.0])
    def test_ac1_grid_exposure_and_gain_reduction(self, exposure_s: float, gain_db: float):
        """Verify >= 90% hot pixel reduction across the standard operational matrix."""
        sim = SimulatedIMX335Controller(width=640, height=480, seed=int(exposure_s * 10 + gain_db))
        sim.set_hardware_exposure(exposure_s, gain_db)

        # 9 dark frames for library
        dark_stack = [sim.capture_frame()[0] for _ in range(9)]
        raw_test, _ = sim.capture_frame()

        engine = DarkCalibrationEngine()
        master_dark = engine.create_master_dark(dark_stack)
        hot_mask = engine.build_hot_pixel_map(master_dark, threshold=100)
        calibrated = engine.calibrate_frame(raw_test, master_dark, hot_mask)

        report = verify_ac1_reduction(raw_test, calibrated, threshold=100)

        assert report["ac1_passed"] is True, f"AC1 failed for exp={exposure_s}s gain={gain_db}dB: {report}"
        assert report["reduction_percentage"] >= 90.0, (
            f"Reduction {report['reduction_percentage']}% below 90% for exp={exposure_s}s, gain={gain_db}dB"
        )

    def test_ac1_with_positive_pedestal(self):
        """Verify dark subtraction with noise-floor pedestal preserves >= 90% reduction."""
        sim = SimulatedIMX335Controller(width=640, height=480, seed=2026)
        sim.set_hardware_exposure(10.0, 36.0)

        dark_stack = [sim.capture_frame()[0] for _ in range(9)]
        raw_test, _ = sim.capture_frame()

        engine = DarkCalibrationEngine()
        master_dark = engine.create_master_dark(dark_stack)
        hot_mask = engine.build_hot_pixel_map(master_dark, threshold=100)
        calibrated = engine.calibrate_frame(raw_test, master_dark, hot_mask, pedestal=16)

        report = verify_ac1_reduction(raw_test, calibrated, threshold=100)
        assert report["ac1_passed"] is True
        assert report["reduction_percentage"] >= 90.0

    def test_ac1_grayscale_single_channel_calibration(self):
        """Verify AC1 hot pixel reduction on 2D single-channel monochrome frames."""
        rng = np.random.default_rng(42)
        h, w = 200, 200
        # Background noise 20 DN, 500 hot pixels at 220 DN
        raw_2d = np.clip(rng.normal(20, 2, (h, w)), 0, 255).astype(np.uint8)
        dark_2d = np.clip(rng.normal(15, 2, (h, w)), 0, 255).astype(np.uint8)

        hot_indices = rng.choice(h * w, 500, replace=False)
        ys, xs = np.unravel_index(hot_indices, (h, w))
        raw_2d[ys, xs] = 230
        dark_2d[ys, xs] = 210

        engine = DarkCalibrationEngine()
        hot_mask = engine.build_hot_pixel_map(dark_2d, threshold=100)
        calibrated_2d = engine.calibrate_frame(raw_2d, dark_2d, hot_mask)

        report = verify_ac1_reduction(raw_2d, calibrated_2d, threshold=100)
        assert report["ac1_passed"] is True
        assert report["reduction_percentage"] >= 95.0


class TestAdversarialDefectCornerCases:
    """
    Stress-tests edge cases and pathological defect distributions:
    - Extreme hot pixel density (1%, 5%, 10%)
    - Contiguous defect clusters (2x2, 3x3)
    - Boundary and corner hot pixels
    - Zero hot pixels (clean sensor)
    - All-black frame (0 DN)
    - All-white frame (255 DN)
    """

    @pytest.mark.parametrize("density", [0.01, 0.05, 0.10])
    def test_extreme_hot_pixel_densities(self, density: float):
        """Probe sensor with 1%, 5%, and 10% defective hot pixel densities."""
        h, w = 200, 200
        total_pixels = h * w
        num_defects = int(total_pixels * density)

        rng = np.random.default_rng(12345)
        raw = np.full((h, w, 3), 20, dtype=np.uint8)
        dark = np.full((h, w, 3), 10, dtype=np.uint8)

        defect_idx = rng.choice(total_pixels, num_defects, replace=False)
        dys, dxs = np.unravel_index(defect_idx, (h, w))
        raw[dys, dxs, :] = 220
        dark[dys, dxs, :] = 200

        engine = DarkCalibrationEngine()
        mask = engine.build_hot_pixel_map(dark, threshold=100)
        calibrated = engine.calibrate_frame(raw, dark, mask)

        report = verify_ac1_reduction(raw, calibrated, threshold=100)
        assert report["ac1_passed"] is True
        assert report["reduction_percentage"] >= 90.0, (
            f"Density {density*100}% achieved {report['reduction_percentage']}% < 90%"
        )

    def test_contiguous_hot_pixel_clusters_2x2_and_3x3(self):
        """
        Verify contiguous defective clusters (2x2 and 3x3).
        A 3x3 block has no valid neighbors in a 3x3 window and requires the 5x5 fallback.
        """
        h, w = 80, 80
        raw = np.full((h, w, 3), 25, dtype=np.uint8)
        dark = np.full((h, w, 3), 12, dtype=np.uint8)

        # 2x2 cluster at (15:17, 15:17)
        raw[15:17, 15:17, :] = 225
        dark[15:17, 15:17, :] = 205

        # 3x3 cluster at (40:43, 40:43)
        raw[40:43, 40:43, :] = 240
        dark[40:43, 40:43, :] = 220

        engine = DarkCalibrationEngine()
        mask = engine.build_hot_pixel_map(dark, threshold=100)
        calibrated = engine.calibrate_frame(raw, dark, mask)

        report = verify_ac1_reduction(raw, calibrated, threshold=100)
        assert report["ac1_passed"] is True
        assert report["reduction_percentage"] == 100.0

        # Verify cluster centers repaired cleanly to background level (~13 DN)
        assert np.all(calibrated[15:17, 15:17, :] < 30)
        assert np.all(calibrated[40:43, 40:43, :] < 30)

    def test_boundary_and_corner_edge_hot_pixels(self):
        """
        Probe hot pixels located at the absolute image boundaries:
        Corners: (0,0), (0, W-1), (H-1, 0), (H-1, W-1)
        Edges: row 0, row H-1, col 0, col W-1.
        """
        h, w = 100, 100
        raw = np.full((h, w, 3), 22, dtype=np.uint8)
        dark = np.full((h, w, 3), 10, dtype=np.uint8)

        boundary_coords = [
            (0, 0),
            (0, w - 1),
            (h - 1, 0),
            (h - 1, w - 1),
            (0, 50),
            (h - 1, 50),
            (50, 0),
            (50, w - 1),
        ]

        for y, x in boundary_coords:
            raw[y, x, :] = 215
            dark[y, x, :] = 195

        engine = DarkCalibrationEngine()
        mask = engine.build_hot_pixel_map(dark, threshold=100)
        calibrated = engine.calibrate_frame(raw, dark, mask)

        report = verify_ac1_reduction(raw, calibrated, threshold=100)
        assert report["ac1_passed"] is True
        assert report["reduction_percentage"] == 100.0

        for y, x in boundary_coords:
            assert np.all(calibrated[y, x, :] < 30), f"Edge defect at ({y}, {x}) was not repaired"

    def test_zero_hot_pixels_clean_sensor(self):
        """A clean sensor with 0 hot pixels should execute seamlessly and return unmodified subtracted frame."""
        h, w = 64, 64
        raw = np.full((h, w, 3), 35, dtype=np.uint8)
        dark = np.full((h, w, 3), 10, dtype=np.uint8)

        engine = DarkCalibrationEngine()
        mask = engine.build_hot_pixel_map(dark, threshold=100)
        assert np.sum(mask) == 0

        calibrated = engine.calibrate_frame(raw, dark, mask)
        assert np.all(calibrated == 25)

        report = verify_ac1_reduction(raw, calibrated, threshold=100)
        assert report["ac1_passed"] is True
        assert report["n_initial_hot_pixels"] == 0
        assert report["n_final_hot_pixels"] == 0
        assert report["reduction_percentage"] == 100.0

    def test_all_black_frame(self):
        """All-black frames (0 DN) in raw and dark must not cause division-by-zero or numerical errors."""
        raw = np.zeros((64, 64, 3), dtype=np.uint8)
        dark = np.zeros((64, 64, 3), dtype=np.uint8)

        engine = DarkCalibrationEngine()
        mask = engine.build_hot_pixel_map(dark, threshold=100)
        assert np.sum(mask) == 0

        calibrated = engine.calibrate_frame(raw, dark, mask)
        assert np.all(calibrated == 0)

        report = verify_ac1_reduction(raw, calibrated, threshold=100)
        assert report["ac1_passed"] is True
        assert report["reduction_percentage"] == 100.0

    def test_all_white_frame_behavior(self):
        """
        All-white saturated frames (255 DN):
        - When dark frame is also saturated, difference evaluates to 0 DN without overflow.
        - When raw is white and dark has isolated hot pixels, interpolation behaves safely.
        """
        raw_white = np.full((64, 64, 3), 255, dtype=np.uint8)
        dark_white = np.full((64, 64, 3), 255, dtype=np.uint8)

        engine = DarkCalibrationEngine()
        mask_white = engine.build_hot_pixel_map(dark_white, threshold=100)
        calibrated_white = engine.calibrate_frame(raw_white, dark_white, mask_white)

        # 255 - 255 = 0
        assert np.all(calibrated_white == 0)

        # Raw white with normal master dark
        dark_normal = np.full((64, 64, 3), 15, dtype=np.uint8)
        dark_normal[32, 32, :] = 200
        mask_normal = engine.build_hot_pixel_map(dark_normal, threshold=100)
        cal_norm = engine.calibrate_frame(raw_white, dark_normal, mask_normal)

        # Non-defective: 255 - 15 = 240
        # Defective (32, 32): median of neighbors (all 240) -> 240
        assert np.all(cal_norm == 240)


class TestAdversarialLiveStackingOutlierRejection:
    """
    Adversarial verification of Live Stacking outlier rejection with sigma-clipping:
    - Artificial satellite streaks of value 255 across horizontal, vertical, diagonal paths.
    - Dynamic satellite moving across frames.
    - Buffer size dependency for sigma-clipping outlier rejection.
    - Preservation of genuine bright stars vs clipping of transient streaks.
    """

    def test_satellite_streak_rejection_horizontal_vertical_diagonal(self):
        """
        Verify that extreme satellite streaks (DN 255) in any orientation
        are completely rejected by 5-frame live stacking with kappa=2.2.
        """
        h, w = 60, 60
        bg_level = 35

        orientations = ["horizontal", "vertical", "diagonal"]
        for ori in orientations:
            engine = LiveStackingEngine(max_frames=5, kappa=2.2)
            for i in range(5):
                frame = np.full((h, w, 3), bg_level, dtype=np.uint8)
                if i == 2:
                    if ori == "horizontal":
                        frame[30, :, :] = 255
                    elif ori == "vertical":
                        frame[:, 30, :] = 255
                    elif ori == "diagonal":
                        for d in range(min(h, w)):
                            frame[d, d, :] = 255
                engine.add_frame(frame)

            stacked = engine.compute_stack()
            assert stacked.shape == (h, w, 3)
            # The streak must be completely eliminated
            assert np.all(stacked == bg_level), f"Streak in orientation {ori} was not completely rejected!"

    def test_moving_satellite_streak_across_frames(self):
        """
        A moving satellite traverses different pixels on each consecutive frame.
        At any individual pixel, the streak only appears in 1 of the 10 frames.
        """
        engine = LiveStackingEngine(max_frames=10, kappa=2.2)
        h, w = 100, 100
        bg_level = 42

        for i in range(10):
            frame = np.full((h, w, 3), bg_level, dtype=np.uint8)
            if i < 5:
                # Satellite moves 15 pixels down every frame
                row = 10 + i * 15
                frame[row : row + 3, :, :] = 255
            engine.add_frame(frame)

        stacked = engine.compute_stack()
        # Because each row only saw the streak for 1 frame out of 10, all rows should be clean
        assert np.all(stacked == bg_level), "Moving satellite streak leaked into stacked frame!"

    def test_sigma_clipping_frame_count_dynamics(self):
        """
        Characterize sigma-clipping rejection limits as a function of frame count N:
        - For N < 5 with kappa=2.2, a single extreme outlier inflates sample standard deviation
          such that diff <= kappa * sigma (studentized range effect).
        - For N >= 5, kappa=2.2 properly rejects a single extreme outlier (DN 255).
        """
        bg = 40
        outlier_val = 255

        # For N=3 and N=4, verify mathematical property that outlier is not clipped at kappa=2.2
        for n in [3, 4]:
            stack = [np.full((10, 10), bg, dtype=np.uint8) for _ in range(n - 1)]
            streak = np.full((10, 10), bg, dtype=np.uint8)
            streak[5, :] = outlier_val
            stack.append(streak)

            res = stack_frames_sigma_clipped(stack, kappa=2.2)
            assert res[5, 0] > bg, f"Expected sample variance inflation at N={n}"

        # For N >= 5, verify outlier is reliably and strictly clipped
        for n in [5, 6, 8, 10]:
            stack = [np.full((10, 10), bg, dtype=np.uint8) for _ in range(n - 1)]
            streak = np.full((10, 10), bg, dtype=np.uint8)
            streak[5, :] = outlier_val
            stack.append(streak)

            res = stack_frames_sigma_clipped(stack, kappa=2.2)
            assert res[5, 0] == bg, f"Outlier was not clipped at N={n}!"

    def test_preserves_astronomical_stars_while_rejecting_streaks(self):
        """
        Photometric discrimination test:
        - Persistent bright star (DN 255 in all 5 frames) MUST be preserved at DN 255.
        - Transient satellite streak (DN 255 in 1 frame) MUST be rejected back to background (DN 30).
        """
        engine = LiveStackingEngine(max_frames=5, kappa=2.2)
        h, w = 50, 50
        bg_level = 30

        for i in range(5):
            frame = np.full((h, w, 3), bg_level, dtype=np.uint8)
            # Persistent star at (25, 25)
            frame[25, 25, :] = 255
            # Transient streak at row 10 in frame 2 only
            if i == 2:
                frame[10, :, :] = 255
            engine.add_frame(frame)

        stacked = engine.compute_stack()

        # Star must survive with 100% fidelity
        assert np.all(stacked[25, 25, :] == 255), "Astronomical star was incorrectly clipped!"
        # Streak must be rejected
        assert np.all(stacked[10, :, :] == bg_level), "Satellite streak was not rejected!"
