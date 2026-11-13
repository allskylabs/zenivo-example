"""
Adversarial Stress Test Suite for Milestone 2: Astro Pipeline & Analytics.
Empirical verification harness by Challenger 1 (challenger_m2_1).

Scope:
1. Keogram and Startrails Stress Testing:
   - Corrupted frames (None, zero-size, 1D, invalid slice widths).
   - Empty lists and empty accumulator state handling.
   - Dimension mismatches (height mismatches in frame lists and accumulators).
   - Degenerate luminance extremes: all-black (0 DN), all-white (255 DN), alternating stripes.
   - Startrails comet-mode decay dynamics (exponential, linear, boundary alpha).
2. Meteor & Transient Detector Stress Testing:
   - Complex multi-streak scenes: multiple simultaneous non-touching streaks, crossing X-patterns, fragmented bolides.
   - Curved satellite passes: fisheye arc trajectories at decreasing radii of curvature (aspect ratio degradation).
   - Point-source scintillation rejection: Gaussian fluctuating stars and saturated stars (0 false positives).
   - Faint streaks near threshold: sharp step-function at min_brightness_diff=30 DN, streak fragmentation under Gaussian noise.
   - Stateful detector lifecycle, background adaptation, and reset semantics.
3. Star & Cloud Analyzer Stress Testing:
   - Pristine moonless sky (0% cloud): high star count, low diffuse background, cloud_cover <= 5%, status="clear".
   - Complete overcast (100% cloud): urban skyglow reflection vs dark-site rural overcast floor (60%).
   - Twilight sky transition: astronomical, nautical, and civil twilight background saturation dynamics.
   - Degenerate frames: all-black (0 DN), all-white (255 DN), empty frames.
"""

import math
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any
import numpy as np
import pytest
import cv2

from src.pipeline.keogram import (
    extract_meridian_slice,
    generate_keogram,
    KeogramGenerator,
)
from src.pipeline.startrails import (
    generate_startrails,
    StartrailsAccumulator,
)
from src.analytics.meteor import (
    extract_streak_moments,
    detect_meteors,
    extract_event_crop,
    MeteorDetector,
)
from src.analytics.stars_clouds import (
    detect_stars_blob,
    estimate_stars_and_clouds,
    detect_stars_and_clouds,
    StarAndCloudAnalyzer,
)


# =====================================================================
# 1. Keogram and Startrails Adversarial Stress Tests
# =====================================================================

class TestAdversarialKeogramAndStartrails:
    """Stress testing Keogram and Startrails generation against degenerate and malformed inputs."""

    def test_keogram_corrupted_and_empty_inputs(self):
        """Verify strict error handling for None, empty arrays, and malformed slice widths."""
        with pytest.raises(ValueError, match="Cannot extract slice from empty or None frame"):
            extract_meridian_slice(None)

        with pytest.raises(ValueError, match="Cannot extract slice from empty or None frame"):
            extract_meridian_slice(np.zeros((0, 0, 3), dtype=np.uint8))

        with pytest.raises(ValueError, match="Slice width must be positive"):
            extract_meridian_slice(np.zeros((100, 100, 3), dtype=np.uint8), slice_width=0)

        with pytest.raises(ValueError, match="Slice width must be positive"):
            extract_meridian_slice(np.zeros((100, 100, 3), dtype=np.uint8), slice_width=-3)

        with pytest.raises(ValueError, match="Cannot generate keogram from empty frame sequence"):
            generate_keogram([])

    def test_keogram_slice_width_exceeds_image_width(self):
        """When slice width exceeds frame width, slice extraction should clamp without crash."""
        frame = np.full((100, 20, 3), 128, dtype=np.uint8)
        # Request slice width 50 on 20px wide frame
        sl = extract_meridian_slice(frame, slice_width=50)
        assert sl.shape[0] == 100
        assert sl.shape[1] <= 20
        assert sl.dtype == np.uint8

    def test_keogram_dimension_mismatch_in_list(self):
        """Sequence with mismatched frame heights must raise ValueError on concatenation."""
        f1 = np.zeros((480, 640, 3), dtype=np.uint8)
        f2 = np.zeros((720, 1280, 3), dtype=np.uint8)

        with pytest.raises(ValueError, match="all the input array dimensions except for the concatenation axis must match exactly"):
            generate_keogram([f1, f2])

    def test_keogram_generator_accumulator_dimension_mismatch(self):
        """Streaming KeogramGenerator skips a frame of another size (resolution change / wrong mode):
        no crash, the strip keeps one consistent height and the timeline stays aligned."""
        gen = KeogramGenerator(slice_width=3)
        gen.add_frame(np.zeros((480, 640, 3), dtype=np.uint8))
        gen.add_frame(np.zeros((720, 1280, 3), dtype=np.uint8))
        gen.add_frame(np.zeros((480, 640, 3), dtype=np.uint8))

        assert len(gen.slices) == len(gen.timestamps) == 2
        assert gen.get_keogram().shape == (480, 6, 3)

    def test_keogram_degenerate_luminance_extremes(self):
        """Test Keogram generation under all-black, all-white, and alternating high-contrast frames."""
        h, w = 120, 160
        black_frames = [np.zeros((h, w, 3), dtype=np.uint8) for _ in range(8)]
        white_frames = [np.full((h, w, 3), 255, dtype=np.uint8) for _ in range(8)]

        # All-black keogram
        k_black = generate_keogram(black_frames, slice_width=4)
        assert k_black.shape == (h, 8 * 4, 3)
        assert np.max(k_black) == 0

        # All-white keogram
        k_white = generate_keogram(white_frames, slice_width=4)
        assert k_white.shape == (h, 8 * 4, 3)
        assert np.min(k_white) == 255

        # Timestamp ruler rendering on degenerate frames
        gen = KeogramGenerator(slice_width=4)
        base_time = datetime(2026, 10, 14, 22, 0, tzinfo=timezone.utc)
        for i in range(10):
            gen.add_frame(np.full((h, w, 3), 255 if i % 2 == 0 else 0, dtype=np.uint8),
                          timestamp=base_time + timedelta(minutes=i * 5))

        rendered = gen.render_with_timestamp_ruler(ruler_height=30)
        assert rendered.shape == (h + 30, 10 * 4, 3)
        assert rendered.dtype == np.uint8

    def test_startrails_corrupted_and_empty_inputs(self):
        """Test Startrails generation with empty sequence and None/corrupted elements."""
        with pytest.raises(ValueError, match="Cannot generate startrails from empty frame sequence"):
            generate_startrails([])

        # StartrailsAccumulator rejects None or empty frame
        acc = StartrailsAccumulator()
        with pytest.raises(ValueError, match="Cannot add empty frame to startrails accumulator"):
            acc.add_frame(None)

        with pytest.raises(ValueError, match="Cannot add empty frame to startrails accumulator"):
            acc.add_frame(np.zeros((0, 0, 3), dtype=np.uint8))

    def test_startrails_dimension_mismatch(self):
        """Batch startrails raises on mixed sizes; the streaming accumulator skips a wrong-size frame instead."""
        f1 = np.zeros((480, 640, 3), dtype=np.uint8)
        f2 = np.zeros((720, 1280, 3), dtype=np.uint8)

        with pytest.raises(ValueError, match="operands could not be broadcast together"):
            generate_startrails([f1, f2])

        acc = StartrailsAccumulator()
        acc.add_frame(f1)
        out = acc.add_frame(f2)
        assert out.shape == f1.shape
        assert acc.frame_count == 1
        assert acc.get_startrails().shape == f1.shape

    def test_startrails_mask_dimension_mismatch(self):
        """Mismatched mask dimensions must raise ValueError during broadcast/where."""
        f1 = np.zeros((200, 200, 3), dtype=np.uint8)
        f2 = np.zeros((200, 200, 3), dtype=np.uint8)
        mismatched_mask = np.ones((100, 100), dtype=np.uint8)

        with pytest.raises(ValueError):
            generate_startrails([f1, f2], mask=mismatched_mask)

    def test_startrails_all_black_and_all_white(self):
        """Startrails maximum blending on pure black, pure white, and mixed extremes."""
        h, w = 100, 100
        black = np.zeros((h, w, 3), dtype=np.uint8)
        white = np.full((h, w, 3), 255, dtype=np.uint8)

        # Black sequence
        s_black = generate_startrails([black, black, black])
        assert np.max(s_black) == 0

        # White dominates black in max blending
        s_mixed = generate_startrails([black, white, black])
        assert np.min(s_mixed) == 255

    def test_startrails_comet_decay_modes_and_alpha(self):
        """Verify exponential vs linear comet decay dynamics and alpha clamping."""
        h, w = 80, 80
        white = np.full((h, w, 3), 255, dtype=np.uint8)
        black = np.zeros((h, w, 3), dtype=np.uint8)

        # 1 white frame followed by 3 black frames with exponential decay alpha=0.80
        frames = [white, black, black, black]
        s_exp = generate_startrails(frames, comet_mode=True, decay_alpha=0.80, decay_type="exponential")
        expected_val = int(round(255.0 * (0.80 ** 3)))
        assert abs(int(s_exp[40, 40, 0]) - expected_val) <= 2

        # Linear decay: step = (1.0 - 0.90) * 255 = 25.5 per frame for 3 frames = 76.5 drop
        s_lin = generate_startrails(frames, comet_mode=True, decay_alpha=0.90, decay_type="linear")
        expected_lin = int(max(0.0, 255.0 - 3 * (1.0 - 0.90) * 255.0))
        assert abs(int(s_lin[40, 40, 0]) - expected_lin) <= 2


# =====================================================================
# 2. Meteor & Transient Detector Adversarial Stress Tests
# =====================================================================

class TestAdversarialMeteorDetector:
    """Stress testing Meteor and Transient streak detection under challenging astronomical scenes."""

    def test_meteor_multi_streak_complex_scenes(self):
        """Verify detection of multiple simultaneous independent streaks in a single frame."""
        h, w = 600, 600
        base = np.full((h, w), 20, dtype=np.uint8)
        curr = base.copy()

        # Inject 5 distinct, non-overlapping linear streaks across the sky
        streaks = [
            ((50, 50), (130, 50), 220),     # Horizontal
            ((200, 100), (280, 160), 240),  # Diagonal 1
            ((400, 250), (450, 370), 210),  # Steep diagonal
            ((100, 400), (170, 470), 230),  # Diagonal 2
            ((300, 500), (390, 500), 250),  # Horizontal 2
        ]
        for pt1, pt2, val in streaks:
            cv2.line(curr, pt1, pt2, int(val), 2)

        events = detect_meteors(curr, base, min_length_px=20, min_aspect_ratio=4.0, min_brightness_diff=30)
        assert len(events) == 5, f"Expected 5 detected streaks, got {len(events)}"

        for ev in events:
            assert ev["type"] == "meteor"
            assert ev["aspect_ratio"] >= 4.0
            assert ev["length_px"] >= 30.0

    def test_meteor_crossing_streaks_aspect_ratio_rejection(self):
        """
        When two streaks intersect orthogonally (X shape), their combined connected
        component moment yields an aspect ratio near 1.0 and is rejected.
        Empirically verifies this moment-based filter boundary.
        """
        h, w = 300, 300
        base = np.full((h, w), 20, dtype=np.uint8)
        curr = base.copy()

        # Intersecting orthogonal lines (X shape)
        cv2.line(curr, (100, 100), (200, 200), 240, 2)
        cv2.line(curr, (100, 200), (200, 100), 240, 2)

        events = detect_meteors(curr, base, min_length_px=20, min_aspect_ratio=4.0, min_brightness_diff=30)
        assert len(events) == 0, "Orthogonal X-crossing streaks should be rejected by aspect ratio filter"

        # Verify underlying moment aspect ratio is near 1.0
        diff = np.maximum(0, curr.astype(np.int16) - base.astype(np.int16)).astype(np.uint8)
        _, _, _, _, ar = extract_streak_moments(diff, diff > 30)
        assert ar < 2.0

    def test_meteor_fragmented_bolide_detection(self):
        """A fragmented meteor bolide (multiple collinear segments) is detected as distinct streak events."""
        h, w = 400, 400
        base = np.full((h, w), 20, dtype=np.uint8)
        curr = base.copy()

        # 3 collinear fragments along y=200
        cv2.line(curr, (80, 200), (120, 200), 240, 2)
        cv2.line(curr, (140, 200), (180, 200), 250, 2)
        cv2.line(curr, (200, 200), (240, 200), 230, 2)

        events = detect_meteors(curr, base, min_length_px=15, min_aspect_ratio=4.0, min_brightness_diff=30)
        assert len(events) == 3, f"Expected 3 fragments detected, got {len(events)}"

    def test_meteor_curved_satellite_trajectory_aspect_ratio(self):
        """
        Curved satellite trails in fisheye allsky images:
        - Mild curvature (R >= 50 px): aspect ratio >= 4.0, detected.
        - Tight curvature (R <= 30 px): aspect ratio < 4.0, rejected by streak moments.
        """
        h, w = 600, 600
        base = np.full((h, w), 20, dtype=np.uint8)
        center = (300, 300)

        # 1. Mild curvature R = 150 px
        curr_mild = base.copy()
        theta_mild = (60.0 / 150.0) * (180.0 / math.pi)
        cv2.ellipse(curr_mild, center, (150, 150), 0, -theta_mild / 2, theta_mild / 2, 230, 2)
        events_mild = detect_meteors(curr_mild, base, min_length_px=15, min_aspect_ratio=4.0, min_brightness_diff=30)
        assert len(events_mild) == 1
        assert events_mild[0]["aspect_ratio"] > 8.0

        # 2. Tight curvature R = 25 px
        curr_tight = base.copy()
        theta_tight = (60.0 / 25.0) * (180.0 / math.pi)
        cv2.ellipse(curr_tight, center, (25, 25), 0, -theta_tight / 2, theta_tight / 2, 230, 2)
        events_tight = detect_meteors(curr_tight, base, min_length_px=15, min_aspect_ratio=4.0, min_brightness_diff=30)
        assert len(events_tight) == 0, "Tightly curved trajectory (R=25) should fail aspect ratio filter"

    def test_meteor_point_source_scintillation_rejection(self):
        """
        Atmospheric turbulence causes star scintillation.
        50 point sources with random intensity fluctuations [-40..+60] DN must produce 0 false positives.
        Even saturated stars with delta=155 DN must produce 0 false positives.
        """
        h, w = 500, 500
        rng = np.random.default_rng(42)
        prev = np.full((h, w), 20, dtype=np.uint8)
        curr = np.full((h, w), 20, dtype=np.uint8)

        # 50 scintillating stars
        xs = rng.integers(30, w - 30, size=50)
        ys = rng.integers(30, h - 30, size=50)
        for x, y in zip(xs, ys):
            base_mag = int(rng.integers(60, 180))
            delta = int(rng.integers(-40, 60))
            cv2.circle(prev, (x, y), 2, base_mag, -1)
            cv2.circle(curr, (x, y), 2, int(np.clip(base_mag + delta, 0, 255)), -1)

        events = detect_meteors(curr, prev, min_length_px=15, min_aspect_ratio=4.0, min_brightness_diff=30)
        assert len(events) == 0, f"Scintillation produced {len(events)} false positives (expected 0)"

        # Extreme saturated scintillation on large stars (radius 4, delta 155 DN)
        prev_sat = np.full((h, w), 20, dtype=np.uint8)
        curr_sat = np.full((h, w), 20, dtype=np.uint8)
        for x, y in zip(xs[:10], ys[:10]):
            cv2.circle(prev_sat, (x, y), 4, 100, -1)
            cv2.circle(curr_sat, (x, y), 4, 255, -1)

        events_sat = detect_meteors(curr_sat, prev_sat, min_length_px=15, min_aspect_ratio=4.0, min_brightness_diff=30)
        assert len(events_sat) == 0, f"Saturated star scintillation produced {len(events_sat)} false positives"

    def test_meteor_faint_streak_detection_threshold(self):
        """
        Verify exact threshold boundary at min_brightness_diff = 30 DN:
        Differences <= 30 DN are strictly rejected; differences >= 31 DN are detected.
        """
        h, w = 300, 300
        base = np.full((h, w), 20, dtype=np.uint8)

        # Test streak with peak diff = 29 DN
        curr_29 = base.copy()
        cv2.line(curr_29, (50, 100), (120, 100), int(20 + 29), 2)
        assert len(detect_meteors(curr_29, base, min_length_px=15, min_aspect_ratio=4.0, min_brightness_diff=30)) == 0

        # Test streak with peak diff = 30 DN (OpenCV THRESH_BINARY thresholding is strictly > thresh)
        curr_30 = base.copy()
        cv2.line(curr_30, (50, 100), (120, 100), int(20 + 30), 2)
        assert len(detect_meteors(curr_30, base, min_length_px=15, min_aspect_ratio=4.0, min_brightness_diff=30)) == 0

        # Test streak with peak diff = 31 DN
        curr_31 = base.copy()
        cv2.line(curr_31, (50, 100), (120, 100), int(20 + 31), 2)
        events_31 = detect_meteors(curr_31, base, min_length_px=15, min_aspect_ratio=4.0, min_brightness_diff=30)
        assert len(events_31) == 1
        assert events_31[0]["peak_dn"] == 31

    def test_stateful_meteor_detector_lifecycle(self):
        """Test MeteorDetector stateful processing, running background update, and event logging."""
        detector = MeteorDetector(min_length_px=20, min_aspect_ratio=4.0, min_brightness_diff=30, history_alpha=0.1)

        # Initial frame primes the background model
        f0 = np.full((200, 200, 3), 20, dtype=np.uint8)
        ev0 = detector.process_frame(f0)
        assert len(ev0) == 0
        assert detector.background_model is not None

        # Frame 1: inject streak
        f1 = f0.copy()
        cv2.line(f1, (40, 100), (120, 100), (220, 220, 220), 2)
        ev1 = detector.process_frame(f1)
        assert len(ev1) == 1
        assert len(detector.events_history) == 1

        # Frame 2: return to base background - streak gone, no new detection
        ev2 = detector.process_frame(f0)
        assert len(ev2) == 0

        # Reset cleans history
        detector.reset()
        assert detector.background_model is None
        assert len(detector.events_history) == 0


# =====================================================================
# 3. Star & Cloud Analyzer Adversarial Stress Tests
# =====================================================================

class TestAdversarialStarAndCloudAnalyzer:
    """Stress testing star extraction and cloud cover estimation under extreme sky conditions."""

    @staticmethod
    def _create_synthetic_sky(h: int, w: int, bg_luma: int, num_stars: int, seed: int = 123) -> np.ndarray:
        """Helper to create a calibrated starry frame."""
        rng = np.random.default_rng(seed)
        img = np.full((h, w), bg_luma, dtype=np.uint8)
        for _ in range(num_stars):
            x = int(rng.integers(30, w - 30))
            y = int(rng.integers(30, h - 30))
            peak = int(rng.integers(120, 255))
            cv2.circle(img, (x, y), 1, peak, -1)
        return img

    def test_star_and_cloud_pristine_moonless_sky(self):
        """
        Pristine moonless sky (0% clouds):
        High star count (150-300 stars), low background luma (~8 DN).
        Cloud cover percentage must be <= 5.0% and status must be 'clear'.
        """
        h, w = 600, 600
        pristine = self._create_synthetic_sky(h, w, bg_luma=8, num_stars=180, seed=42)

        res = estimate_stars_and_clouds(pristine, expected_clear_stars=150)
        assert res["star_count"] >= 140
        assert res["cloud_cover_pct"] <= 5.0
        assert res["status"] == "clear"
        assert res["mean_background_luma"] < 12.0

    def test_star_and_cloud_complete_overcast_urban_skyglow(self):
        """
        Complete overcast with urban light pollution:
        Diffuse cloud luminance elevated (35-60 DN), 0 stars visible.
        Cloud cover percentage must be 100.0% and status must be 'overcast'.
        """
        h, w = 500, 500
        for bg in [35, 45, 60]:
            overcast = np.full((h, w), bg, dtype=np.uint8)
            res = estimate_stars_and_clouds(overcast, expected_clear_stars=150)
            assert res["star_count"] == 0
            assert res["cloud_cover_pct"] == 100.0
            assert res["status"] == "overcast"

    def test_star_and_cloud_complete_overcast_dark_rural_site(self):
        """
        Complete overcast at an unpolluted dark rural site (Bortle 1-2):
        Zero stars visible, background luminance is very dark (4 DN).
        Empirically verifies the mathematical formulation:
        luma_factor = 0.0, star_obscuration = 1.0 -> cloud_cover_pct = 60.0%.
        This reveals the model's dependence on urban light pollution to reach 100%.
        """
        h, w = 500, 500
        dark_overcast = np.full((h, w), 4, dtype=np.uint8)
        res = estimate_stars_and_clouds(dark_overcast, expected_clear_stars=150)
        assert res["star_count"] == 0
        assert res["cloud_cover_pct"] == 60.0
        # 60.0% is categorized as 'scattered' since scattered is [25.0 .. 70.0)
        assert res["status"] == "scattered"

    def test_star_and_cloud_twilight_transition(self):
        """
        Twilight sky conditions:
        Atmospheric solar scattering elevates background luma and washes out stars.
        Demonstrates that un-gated optical cloud estimation classifies bright twilight as overcast.
        """
        h, w = 500, 500

        # Nautical twilight: bg=60 DN, ~10 bright stars visible
        nautical_twi = self._create_synthetic_sky(h, w, bg_luma=60, num_stars=10, seed=99)
        res_naut = estimate_stars_and_clouds(nautical_twi, expected_clear_stars=150)
        assert res_naut["star_count"] <= 12
        assert res_naut["cloud_cover_pct"] >= 90.0
        assert res_naut["status"] == "overcast"

        # Civil twilight: bg=150 DN, only 1 star visible
        civil_twi = self._create_synthetic_sky(h, w, bg_luma=150, num_stars=1, seed=88)
        res_civil = estimate_stars_and_clouds(civil_twi, expected_clear_stars=150)
        assert res_civil["cloud_cover_pct"] >= 98.0
        assert res_civil["status"] == "overcast"

    def test_star_and_cloud_degenerate_extremes(self):
        """
        Test behavior on all-black (0 DN), all-white (255 DN), and invalid inputs.
        """
        h, w = 200, 200

        # All-black (camera lens capped / dead sensor)
        black_frame = np.zeros((h, w), dtype=np.uint8)
        res_black = estimate_stars_and_clouds(black_frame, expected_clear_stars=150)
        assert res_black["star_count"] == 0
        assert res_black["cloud_cover_pct"] == 60.0
        assert res_black["status"] == "scattered"

        # All-white (saturated daytime / direct sunlight)
        white_frame = np.full((h, w), 255, dtype=np.uint8)
        res_white = estimate_stars_and_clouds(white_frame, expected_clear_stars=150)
        assert res_white["star_count"] == 0
        assert res_white["cloud_cover_pct"] == 100.0
        assert res_white["status"] == "overcast"

        # Empty / None frame
        with pytest.raises(ValueError, match="Cannot estimate stars and clouds on empty frame"):
            estimate_stars_and_clouds(None)

        with pytest.raises(ValueError, match="Cannot estimate stars and clouds on empty frame"):
            estimate_stars_and_clouds(np.zeros((0, 0), dtype=np.uint8))
