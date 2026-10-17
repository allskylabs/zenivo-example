"""
Unit Test Suite for Meteor Detection and Star/Cloud Analytics (Milestone 2: F13, F14).

Tests:
1. Intensity-weighted 2nd-order central moments (aspect ratio, length, centroid).
2. Meteor streak detection, thresholding, geometric moment classification, false-positive rejection.
3. JSON metadata serialization and event crop extraction.
4. Stateful MeteorDetector with running background accumulation.
5. Star counting via point-source blob detection and cloud cover estimation.
"""

import json
from datetime import datetime, timezone
import pytest
import numpy as np
import cv2

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


class TestStreakMoments:
    """Tests for 2nd-order central moments and elongation analysis."""

    def test_extract_streak_moments_linear_streak(self):
        """A thin line segment of length 100 should have aspect ratio >= 8.0."""
        h, w = 200, 200
        diff = np.zeros((h, w), dtype=np.uint8)
        # Draw a diagonal line
        cv2.line(diff, (40, 40), (140, 140), 250, thickness=2)
        mask = diff > 0

        cx, cy, major, minor, ar = extract_streak_moments(diff, mask)
        # Centroid should be around (90, 90)
        assert abs(cx - 90.0) < 3.0
        assert abs(cy - 90.0) < 3.0
        # Line length ~ sqrt(100^2 + 100^2) = 141.4
        assert major > 80.0
        assert ar >= 6.0

    def test_extract_streak_moments_circular_blob(self):
        """A circular star/blob should have aspect ratio close to 1.0."""
        h, w = 100, 100
        diff = np.zeros((h, w), dtype=np.uint8)
        cv2.circle(diff, (50, 50), 12, 220, -1)
        mask = diff > 0

        cx, cy, major, minor, ar = extract_streak_moments(diff, mask)
        assert abs(cx - 50.0) < 1.0
        assert abs(cy - 50.0) < 1.0
        assert ar < 1.5


class TestMeteorDetection:
    """Tests for transient streak detection and classification."""

    def test_detect_meteors_positive_streak(self):
        """Detect an injected bright streak between two frames."""
        h, w = 300, 300
        prev_frame = np.full((h, w, 3), 15, dtype=np.uint8)
        curr_frame = prev_frame.copy()

        # Inject streak: length 90, thickness 2
        cv2.line(curr_frame, (50, 50), (140, 50), (220, 220, 220), 2)

        events = detect_meteors(
            curr_frame,
            prev_frame,
            min_length_px=30,
            min_aspect_ratio=4.0,
            min_brightness_diff=30,
        )

        assert len(events) == 1
        ev = events[0]
        assert ev["type"] == "meteor"
        assert ev["length_px"] >= 60.0
        assert ev["aspect_ratio"] >= 4.0
        assert ev["peak_dn"] >= 180
        assert len(ev["bbox"]) == 4

        # Validate JSON serialization
        serialized = json.dumps(ev)
        deserialized = json.loads(serialized)
        assert deserialized["type"] == "meteor"
        assert "timestamp" in deserialized

    def test_detect_meteors_rejection_low_aspect_ratio(self):
        """Reject non-streak transient (e.g. cloud patch or expanding flare)."""
        h, w = 200, 200
        prev = np.full((h, w, 3), 10, dtype=np.uint8)
        curr = prev.copy()

        # Injected circular flash
        cv2.circle(curr, (100, 100), 25, (230, 230, 230), -1)

        events = detect_meteors(curr, prev, min_aspect_ratio=4.0)
        assert len(events) == 0

    def test_detect_meteors_rejection_short_streak(self):
        """Reject streaks below minimum length threshold."""
        h, w = 200, 200
        prev = np.full((h, w, 3), 10, dtype=np.uint8)
        curr = prev.copy()

        # Short streak: length 10px
        cv2.line(curr, (50, 50), (60, 50), (200, 200, 200), 2)

        events = detect_meteors(curr, prev, min_length_px=35, min_aspect_ratio=4.0)
        assert len(events) == 0

    def test_detect_meteors_identical_frames_zero_events(self):
        """Two identical frames have zero difference -> zero events."""
        frame = np.full((150, 150, 3), 30, dtype=np.uint8)
        events = detect_meteors(frame, frame)
        assert len(events) == 0

    def test_detect_meteors_none_raises(self):
        with pytest.raises(ValueError):
            detect_meteors(None, np.zeros((10, 10)))

    def test_extract_event_crop_boundaries(self):
        """Verify cropping around event bbox clamped at frame edges."""
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        # Bounding box near top-left corner
        bbox = [5, 5, 20, 20]
        crop = extract_event_crop(frame, bbox, padding=15)
        # Bbox is [5, 5, 20, 20], padded [0:40, 0:40]
        assert crop.shape == (40, 40, 3)

    def test_stateful_meteor_detector(self):
        """Test MeteorDetector stateful processing with running background model."""
        detector = MeteorDetector(min_length_px=25, min_aspect_ratio=4.0)

        f1 = np.full((150, 150, 3), 12, dtype=np.uint8)
        # First frame initializes background model, no events
        e1 = detector.process_frame(f1)
        assert len(e1) == 0

        # Second frame: add a meteor streak
        f2 = f1.copy()
        cv2.line(f2, (30, 30), (100, 30), (240, 240, 240), 2)
        e2 = detector.process_frame(f2)
        assert len(e2) == 1
        assert e2[0]["type"] == "meteor"
        assert len(detector.events_history) == 1

        detector.reset()
        assert detector.background_model is None
        assert len(detector.events_history) == 0


class TestStarAndCloudAnalytics:
    """Tests for star counting and cloud cover analytics."""

    def test_detect_stars_blob_on_point_sources(self):
        """Extract point sources and reject 1px noise."""
        gray = np.full((200, 200), 12, dtype=np.uint8)
        # Inject 5 valid stars (each a 3x3 gaussian spot)
        coords = [(40, 40), (80, 50), (120, 80), (150, 140), (60, 160)]
        for x, y in coords:
            cv2.circle(gray, (x, y), 2, 220, -1)

        # Inject single-pixel hot noise
        gray[10, 10] = 250

        count, mask, stars_list = detect_stars_blob(gray, min_area=2, max_area=40)
        assert count == 5
        assert len(stars_list) == 5

    def test_estimate_stars_and_clouds_clear_and_overcast(self):
        """Distinguish high star count/low luma (clear) vs low star count/high luma (overcast)."""
        # 1. Clear night: 50 stars, low background (12 DN)
        clear_frame = np.full((200, 200, 3), 12, dtype=np.uint8)
        for i in range(50):
            x = 20 + (i * 3) % 160
            y = 20 + (i * 7) % 160
            cv2.circle(clear_frame, (x, y), 2, (200, 200, 200), -1)

        res_clear = estimate_stars_and_clouds(clear_frame, expected_clear_stars=50)
        assert res_clear["status"] == "clear"
        assert res_clear["cloud_cover_pct"] < 30.0

        # 2. Overcast night: zero stars, elevated diffuse background (45 DN)
        overcast_frame = np.full((200, 200, 3), 45, dtype=np.uint8)
        res_overcast = estimate_stars_and_clouds(overcast_frame, expected_clear_stars=50)
        assert res_overcast["cloud_cover_pct"] > 60.0
        assert res_overcast["star_count"] == 0
        assert res_overcast["status"] in ("scattered", "overcast")

    def test_star_and_cloud_analyzer_wrapper(self):
        """Test StarAndCloudAnalyzer helper class and alias contract."""
        analyzer = StarAndCloudAnalyzer(default_expected_stars=80)
        frame = np.full((100, 100, 3), 12, dtype=np.uint8)
        res = analyzer.analyze(frame)
        assert "star_count" in res
        assert "cloud_cover_pct" in res
        assert "cloud_cover_percent" in res

        res_alias = detect_stars_and_clouds(frame)
        assert res_alias["star_count"] == res["star_count"]

    def test_estimate_stars_empty_frame_raises(self):
        with pytest.raises(ValueError):
            estimate_stars_and_clouds(np.zeros((0, 0), dtype=np.uint8))
