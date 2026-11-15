"""
Unit Test Suite for Astronomical Pipeline (Milestone 2: F9, F10, F11, F12).

Tests:
1. Keogram generation, slice extraction, streaming accumulator, timestamp ruler, disk saving.
2. Startrails maximum-pixel blending, exponential & linear comet tail modes, horizon masking, streaming accumulator.
3. Timelapse video compilation, metadata overlay banner, OpenCV fallback, file reading/writing.
4. Astrometric projections (LST, equatorial to horizontal, equidistant fisheye model, heading offset, canvas rendering).
"""

import os
import math
from datetime import datetime, timezone
import pytest
import numpy as np
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
from src.pipeline.timelapse import (
    render_metadata_overlay,
    compile_timelapse,
)
from src.pipeline.overlay import (
    calculate_local_sidereal_time,
    equatorial_to_horizontal,
    project_alt_az_to_fisheye,
    render_celestial_overlay,
)


class TestKeogramPipeline:
    """Unit tests for Keogram generation and meridian slice extraction."""

    def test_extract_meridian_slice_dimensions_and_channels(self):
        """Test central meridian slice extraction on 3-channel and 1-channel frames."""
        h, w = 480, 640
        frame_bgr = np.zeros((h, w, 3), dtype=np.uint8)
        # Mark center pixel column
        cx = w // 2
        frame_bgr[:, cx, :] = 200

        sl = extract_meridian_slice(frame_bgr, slice_width=3)
        assert sl.shape == (h, 3, 3)
        assert sl.dtype == np.uint8
        assert np.max(sl) == 200

        # Grayscale input conversion
        frame_gray = np.zeros((h, w), dtype=np.uint8)
        sl_gray = extract_meridian_slice(frame_gray, slice_width=4)
        assert sl_gray.shape == (h, 4, 3)
        assert sl_gray.dtype == np.uint8

    def test_extract_meridian_slice_invalid_inputs(self):
        """Invalid inputs should raise ValueError."""
        with pytest.raises(ValueError):
            extract_meridian_slice(np.zeros((0, 0), dtype=np.uint8))
        with pytest.raises(ValueError):
            extract_meridian_slice(None)
        with pytest.raises(ValueError):
            extract_meridian_slice(np.zeros((100, 100, 3), dtype=np.uint8), slice_width=0)
        with pytest.raises(ValueError):
            extract_meridian_slice(np.zeros((100, 100, 3), dtype=np.uint8), slice_width=-5)

    def test_generate_keogram_concatenation(self):
        """Verify keogram concatenation across multiple frames."""
        h, w = 200, 300
        slice_w = 3
        frames = [np.full((h, w, 3), fill_value=i * 10, dtype=np.uint8) for i in range(10)]

        keogram = generate_keogram(frames, slice_width=slice_w)
        assert keogram.shape == (h, 10 * slice_w, 3)
        assert keogram.dtype == np.uint8

        # Slices should match the sequential frame values
        for i in range(10):
            col = keogram[:, i * slice_w : (i + 1) * slice_w, :]
            assert np.all(col == i * 10)

    def test_generate_keogram_empty_raises(self):
        with pytest.raises(ValueError):
            generate_keogram([])

    def test_keogram_accumulator_streaming_and_eviction(self):
        """Test streaming KeogramGenerator with max_slices FIFO buffer."""
        generator = KeogramGenerator(slice_width=2, max_slices=5)
        assert len(generator) == 0

        frame = np.full((100, 100, 3), 50, dtype=np.uint8)
        for i in range(8):
            ts = datetime(2026, 10, 14, 20, i, 0, tzinfo=timezone.utc)
            generator.add_frame(frame, timestamp=ts)

        # Buffer should be capped at max_slices=5
        assert len(generator) == 5
        keo = generator.get_keogram()
        assert keo.shape == (100, 5 * 2, 3)

        generator.reset()
        assert len(generator) == 0
        assert generator.get_keogram().shape == (0, 0, 3)

    def test_keogram_render_with_timestamps(self):
        """Verify timestamp scale/ruler rendering at bottom of keogram."""
        generator = KeogramGenerator(slice_width=3)
        frame = np.full((120, 160, 3), 40, dtype=np.uint8)

        for i in range(30):
            ts = datetime(2026, 10, 14, 20, i, 0, tzinfo=timezone.utc)
            generator.add_frame(frame, timestamp=ts)

        rendered = generator.render_with_timestamp_ruler(ruler_height=30, tick_interval_slices=10)
        # Original height (120) + ruler_height (30) = 150
        assert rendered.shape == (150, 30 * 3, 3)

    def test_keogram_save_to_disk(self, temp_output_dir):
        """Test file serialization."""
        generator = KeogramGenerator(slice_width=2)
        frame = np.full((100, 100, 3), 70, dtype=np.uint8)
        generator.add_frame(frame, timestamp=datetime.now(timezone.utc))

        save_path = os.path.join(temp_output_dir, "subdir", "keogram.png")
        success = generator.save(save_path, add_timestamps=True)
        assert success is True
        assert os.path.exists(save_path)
        assert os.path.getsize(save_path) > 100


class TestStartrailsPipeline:
    """Unit tests for Startrails aggregation and comet modes."""

    def test_startrails_max_blending(self):
        """Test maximum-pixel blending with multiple point sources."""
        h, w = 150, 150
        f1 = np.zeros((h, w, 3), dtype=np.uint8)
        f2 = np.zeros((h, w, 3), dtype=np.uint8)

        f1[50, 50] = [200, 200, 200]
        f2[60, 60] = [240, 240, 240]

        trails = generate_startrails([f1, f2], comet_mode=False)
        assert trails.shape == (h, w, 3)
        assert np.array_equal(trails[50, 50], [200, 200, 200])
        assert np.array_equal(trails[60, 60], [240, 240, 240])

    def test_startrails_comet_exponential_decay(self):
        """Verify exponential decay attenuates earlier frames relative to latest frame."""
        h, w = 80, 80
        f1 = np.full((h, w, 3), 200, dtype=np.uint8)
        f2 = np.zeros((h, w, 3), dtype=np.uint8)

        alpha = 0.90
        trails = generate_startrails([f1, f2], comet_mode=True, decay_alpha=alpha)
        # First frame decayed once by alpha: 200 * 0.90 = 180
        assert abs(float(np.mean(trails)) - 180.0) < 2.0

    def test_startrails_comet_linear_decay(self):
        """Verify linear decay mode."""
        h, w = 50, 50
        f1 = np.full((h, w, 3), 150, dtype=np.uint8)
        f2 = np.zeros((h, w, 3), dtype=np.uint8)

        trails = generate_startrails([f1, f2], comet_mode=True, decay_alpha=0.90, decay_type="linear")
        # Linear decay subtracts (1 - 0.90)*255 = 25.5 -> 150 - 25.5 ~ 124.5
        assert abs(float(np.mean(trails)) - 124.5) < 3.0

    def test_startrails_horizon_masking(self):
        """Verify unmasked regions do not accumulate startrails."""
        h, w = 100, 100
        mask = np.zeros((h, w), dtype=np.uint8)
        mask[:50, :] = 255  # Only top half is sky

        f1 = np.full((h, w, 3), 50, dtype=np.uint8)
        f2 = np.full((h, w, 3), 150, dtype=np.uint8)

        trails = generate_startrails([f1, f2], comet_mode=False, mask=mask)
        # Top half blended to 150
        assert np.all(trails[:50, :, :] == 150)
        # Bottom half remained at f1 value 50
        assert np.all(trails[50:, :, :] == 50)

    def test_startrails_accumulator_lifecycle(self, temp_output_dir):
        """Test StartrailsAccumulator incremental updates and saving."""
        acc = StartrailsAccumulator(comet_mode=False)
        assert acc.frame_count == 0

        f1 = np.zeros((100, 100, 3), dtype=np.uint8)
        f1[20, 20] = [180, 180, 180]
        acc.add_frame(f1)
        assert acc.frame_count == 1

        f2 = np.zeros((100, 100, 3), dtype=np.uint8)
        f2[30, 30] = [210, 210, 210]
        comp = acc.add_frame(f2)
        assert acc.frame_count == 2
        assert comp[20, 20, 0] == 180
        assert comp[30, 30, 0] == 210

        save_path = os.path.join(temp_output_dir, "trails.jpg")
        assert acc.save(save_path) is True
        assert os.path.exists(save_path)

        acc.reset()
        assert acc.frame_count == 0
        assert acc.get_startrails().shape == (0, 0, 3)

    def test_startrails_empty_raises(self):
        with pytest.raises(ValueError):
            generate_startrails([])


class TestTimelapsePipeline:
    """Unit tests for Timelapse video pipeline and overlay banner."""

    def test_metadata_overlay_rendering(self):
        """Verify banner dimensions, placement, and text inclusion."""
        frame = np.full((480, 640, 3), 50, dtype=np.uint8)
        ts = datetime(2026, 10, 14, 22, 30, 0, tzinfo=timezone.utc)
        telemetry = {
            "exposure_s": 4.5,
            "gain_db": 36.0,
            "star_count": 85,
            "cloud_cover_pct": 15.0,
        }

        annotated = render_metadata_overlay(
            frame,
            timestamp=ts,
            telemetry=telemetry,
            station_name="Test Station Hanoi",
            banner_height=35,
        )

        assert annotated.shape == frame.shape
        # Banner region should not be uniform 50 anymore (drawn black background and white text)
        banner_crop = annotated[480 - 35 : 480, :, :]
        assert np.min(banner_crop) == 0
        assert np.max(banner_crop) > 200

    def test_compile_timelapse_opencv_fallback(self, temp_output_dir):
        """Verify compilation into playable MP4 via OpenCV VideoWriter."""
        num_frames = 12
        frames = [np.full((240, 320, 3), fill_value=i * 15, dtype=np.uint8) for i in range(num_frames)]

        out_path = os.path.join(temp_output_dir, "test_timelapse_cv.mp4")
        result_path = compile_timelapse(
            frames,
            output_path=out_path,
            fps=10.0,
            add_overlay=True,
            station_name="Hanoi Station",
        )

        assert result_path == out_path
        assert os.path.exists(out_path)
        assert os.path.getsize(out_path) > 1000

        # Read back video to verify frame count and dimensions
        cap = cv2.VideoCapture(out_path)
        assert cap.isOpened()
        ret_count = 0
        while True:
            ret, f = cap.read()
            if not ret:
                break
            assert f.shape == (240, 320, 3)
            ret_count += 1
        cap.release()
        assert ret_count == num_frames

    def test_compile_timelapse_from_image_paths(self, temp_output_dir):
        """Verify compilation when given file paths."""
        img_paths = []
        for i in range(6):
            p = os.path.join(temp_output_dir, f"frame_{i:02d}.png")
            img = np.full((120, 160, 3), i * 35, dtype=np.uint8)
            cv2.imwrite(p, img)
            img_paths.append(p)

        video_path = os.path.join(temp_output_dir, "from_files.mp4")
        compile_timelapse(img_paths, video_path, fps=12.0)
        assert os.path.exists(video_path)
        assert os.path.getsize(video_path) > 500

    def test_compile_timelapse_skips_corrupt_jpeg(self, temp_output_dir):
        """A truncated frame on disk is skipped instead of failing the whole timelapse."""
        paths = []
        for i in range(5):
            p = os.path.join(temp_output_dir, f"frame_{i:02d}.jpg")
            cv2.imwrite(p, np.full((120, 160, 3), i * 40, dtype=np.uint8))
            paths.append(p)
        with open(paths[2], "r+b") as fp:
            fp.truncate(20)
        out = os.path.join(temp_output_dir, "skip.mp4")
        compile_timelapse(paths, out, fps=12.0)
        cap = cv2.VideoCapture(out)
        n = 0
        while cap.read()[0]:
            n += 1
        cap.release()
        assert n == 4

    def test_compile_timelapse_all_frames_unreadable_raises(self, temp_output_dir):
        p = os.path.join(temp_output_dir, "bad.jpg")
        with open(p, "wb") as fp:
            fp.write(b"\xff\xd8nope")
        with pytest.raises(ValueError):
            compile_timelapse([p], os.path.join(temp_output_dir, "bad.mp4"))

    def test_refused_hw_encoder_falls_back_to_x264(self, temp_output_dir, monkeypatch):
        """If h264_v4l2m2m is refused mid-stream, ffmpeg is rerun with libx264 and hw is disabled."""
        import shutil
        import src.pipeline.timelapse as tl

        if shutil.which("ffmpeg") is None:
            pytest.skip("ffmpeg not installed")
        monkeypatch.setattr(tl, "_HW_ENCODER_OK", True)
        monkeypatch.setattr(tl, "_HW_ENCODER_CHECKED", 0.0)
        real = tl._ffmpeg_video_args
        seen = []

        def fake_args(ffmpeg_bin, crf, allow_hw=True):
            args = real(ffmpeg_bin, crf, allow_hw)
            if "h264_v4l2m2m" in args:
                args = ["-c:v", "h264_v4l2m2m", "-no_such_option", "1"]  # ffmpeg exits non-zero like a refused hw encoder
            seen.append(args[1])
            return args

        monkeypatch.setattr(tl, "_ffmpeg_video_args", fake_args)
        frames = [np.full((120, 160, 3), i * 20, dtype=np.uint8) for i in range(6)]
        out = os.path.join(temp_output_dir, "fallback.mp4")
        compile_timelapse(frames, out, fps=12.0)
        assert seen == ["h264_v4l2m2m", "libx264"]
        assert tl._HW_ENCODER_OK is False and os.path.getsize(out) > 500

    def test_compile_timelapse_removes_stale_temp_files(self, temp_output_dir):
        stale = os.path.join(temp_output_dir, ".latest_tmp_99999.mp4")
        other = os.path.join(temp_output_dir, ".other_tmp_99999.mp4")
        for p in (stale, other):
            with open(p, "wb") as fp:
                fp.write(b"x" * 10)
        frames = [np.full((120, 160, 3), i * 20, dtype=np.uint8) for i in range(4)]
        compile_timelapse(frames, os.path.join(temp_output_dir, "latest.mp4"), fps=12.0)
        assert not os.path.exists(stale) and os.path.exists(other)

    def test_compile_timelapse_empty_raises(self, temp_output_dir):
        with pytest.raises(ValueError):
            compile_timelapse([], os.path.join(temp_output_dir, "empty.mp4"))


class TestOverlayPipeline:
    """Unit tests for Astrometric ephemeris and fisheye projections."""

    def test_local_sidereal_time_calculation(self):
        """Verify LST calculation across known epoch."""
        dt = datetime(2026, 10, 14, 0, 0, 0, tzinfo=timezone.utc)
        lst_greenwich = calculate_local_sidereal_time(dt, longitude_deg=0.0)
        assert 0.0 <= lst_greenwich < 360.0

        # Hanoi longitude ~ 105.85 deg East -> LST is ahead by ~105.85 deg
        lst_hanoi = calculate_local_sidereal_time(dt, longitude_deg=105.85)
        diff = (lst_hanoi - lst_greenwich) % 360.0
        assert abs(diff - 105.85) < 0.1

    def test_equatorial_to_horizontal_polaris(self):
        """Polaris (Dec ~ 89.26 deg) should have altitude approximately equal to latitude."""
        lat = 21.03  # Hanoi latitude
        lst = 100.0
        alt, az = equatorial_to_horizontal(ra_deg=37.95, dec_deg=89.26, lst_deg=lst, latitude_deg=lat)
        # Polaris altitude must be very close to station latitude
        assert abs(alt - lat) < 1.5

    def test_fisheye_lens_projection_zenith_and_cardinals(self):
        """Verify equidistant fisheye model r = f * (90 - Alt)."""
        cx, cy = 1296, 972
        f = 618.8

        # Zenith (Alt = 90) -> (cx, cy)
        assert project_alt_az_to_fisheye(90.0, 0.0, cx, cy, f) == (cx, cy)

        # Horizon North (Alt = 0, Az = 0) -> u = cx, v = cy - f * pi/2
        r_horiz = f * math.radians(90.0)
        pt_n = project_alt_az_to_fisheye(0.0, 0.0, cx, cy, f)
        assert pt_n[0] == cx
        assert abs(pt_n[1] - (cy - r_horiz)) <= 1

        # Horizon South (Alt = 0, Az = 180) -> u = cx, v = cy + f * pi/2
        pt_s = project_alt_az_to_fisheye(0.0, 180.0, cx, cy, f)
        assert pt_s[0] == cx
        assert abs(pt_s[1] - (cy + r_horiz)) <= 1

        # Horizon East (Alt = 0, Az = 90) -> u = cx + f * pi/2, v = cy
        pt_e = project_alt_az_to_fisheye(0.0, 90.0, cx, cy, f)
        assert abs(pt_e[0] - (cx + r_horiz)) <= 1
        assert pt_e[1] == cy

        # Horizon West (Alt = 0, Az = 270) -> u = cx - f * pi/2, v = cy
        pt_w = project_alt_az_to_fisheye(0.0, 270.0, cx, cy, f)
        assert abs(pt_w[0] - (cx - r_horiz)) <= 1
        assert pt_w[1] == cy

        # Negative Alt -> None
        assert project_alt_az_to_fisheye(-10.0, 45.0, cx, cy, f) is None

    def test_fisheye_heading_offset_rotation(self):
        """Verify camera azimuth rotation with heading_offset_deg."""
        cx, cy = 1000, 1000
        f = 500.0
        # If camera is rotated 90 deg clockwise (heading_offset = 90), True North (az=0)
        # projects as if azimuth is 90 deg (to the right, +x)
        pt = project_alt_az_to_fisheye(0.0, 0.0, cx, cy, f, heading_offset_deg=90.0)
        r = f * math.radians(90.0)
        assert abs(pt[0] - (cx + r)) <= 1
        assert pt[1] == cy

    def test_render_celestial_overlay_full_canvas(self):
        """Render overlay onto a blank 2592x1944 canvas and verify output components."""
        canvas = np.zeros((1944, 2592, 3), dtype=np.uint8)
        dt = datetime(2026, 10, 14, 21, 0, 0, tzinfo=timezone.utc)

        annotated = render_celestial_overlay(
            canvas,
            dt=dt,
            latitude_deg=21.0285,
            longitude_deg=105.8542,
            center_x=1296,
            center_y=972,
            focal_length_px=618.8,
            show_cardinal=True,
            show_zenith=True,
            show_constellations=True,
            show_stars=True,
        )

        assert annotated.shape == (1944, 2592, 3)
        # Canvas should contain rendered annotations (non-zero pixels)
        assert np.sum(annotated > 0) > 1000
