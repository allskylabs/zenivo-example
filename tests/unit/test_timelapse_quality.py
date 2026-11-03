"""Timelapse frame quality check: wrong-size and brightness-glitch frames are left out of the video."""

import cv2
import numpy as np

from src.pipeline.timelapse import frame_quality_mask, select_timelapse_frames


def _write(path, value, size=(972, 1296)):
    img = np.full((size[0], size[1], 3), value, np.uint8)
    cv2.imwrite(str(path), img)
    return str(path)


def test_steady_frames_are_kept(tmp_path):
    paths = [_write(tmp_path / f"f{i}.jpg", 100 + i) for i in range(20)]
    assert all(frame_quality_mask(paths))


def test_black_and_saturated_glitches_are_rejected(tmp_path):
    paths = []
    for i in range(20):
        value = {5: 0, 12: 254}.get(i, 100)
        paths.append(_write(tmp_path / f"f{i:02d}.jpg", value))
    keep = frame_quality_mask(paths)
    assert keep[5] is False
    assert keep[12] is False
    assert sum(keep) == 18


def test_wrong_size_frames_are_rejected(tmp_path):
    paths = [_write(tmp_path / f"f{i:02d}.jpg", 100) for i in range(10)]
    paths[4] = _write(tmp_path / "odd.jpg", 100, size=(730, 1296))
    keep = frame_quality_mask(paths)
    assert keep[4] is False
    assert sum(keep) == 9


def test_slow_dawn_ramp_is_kept(tmp_path):
    paths = [_write(tmp_path / f"f{i:02d}.jpg", 10 + 4 * i) for i in range(30)]
    assert all(frame_quality_mask(paths))


def test_keogram_skips_frames_of_another_size():
    from src.pipeline.keogram import KeogramGenerator

    gen = KeogramGenerator(slice_width=3)
    gen.add_frame(np.zeros((972, 1296, 3), np.uint8))
    gen.add_frame(np.zeros((730, 1296, 3), np.uint8))
    gen.add_frame(np.zeros((972, 1296, 3), np.uint8))
    assert len(gen.slices) == 2
    assert len(gen.timestamps) == 2


def test_startrails_skips_frames_of_another_size():
    from src.pipeline.startrails import StartrailsAccumulator

    acc = StartrailsAccumulator(comet_mode=False)
    acc.add_frame(np.zeros((972, 1296, 3), np.uint8))
    before = acc.frame_count
    acc.add_frame(np.zeros((730, 1296, 3), np.uint8))
    assert acc.frame_count == before
    assert acc.composite.shape == (972, 1296, 3)


def test_widescreen_16_9_archive_frames_are_kept(tmp_path):
    paths = [_write(tmp_path / f"w{i:02d}.jpg", 95 + (i % 5), size=(730, 1296)) for i in range(12)]
    assert all(frame_quality_mask(paths))


def test_select_timelapse_frames_spans_full_day_and_night(tmp_path):
    # Simulate 40 frames (first 20 daytime, last 20 nighttime) capped at max_frames=10
    paths = []
    telem = {}
    for i in range(40):
        fname = f"frame_{i:03d}.jpg"
        b = 180.0 if i < 15 else (180.0 - (i - 15) * 14.0 if i < 25 else 40.0)
        p = _write(tmp_path / fname, int(b), size=(730, 1296))
        paths.append(p)
        telem[fname] = {"brightness": b}

    selected, rejected = select_timelapse_frames(paths, max_frames=10, telemetry_map=telem)
    assert len(selected) == 10
    assert rejected == 0
    # Must start with the very first morning frame and end with the very last night frame
    assert selected[0] == paths[0]
    assert selected[-1] == paths[-1]
    # Daytime frames (indices < 20) must make up ~half of the selected video
    day_selected = [p for p in selected if int(p.split("_")[-1].split(".")[0]) < 20]
    assert len(day_selected) >= 4




def test_select_timelapse_frames_always_keeps_event_frames(tmp_path):
    import cv2
    import numpy as np
    from src.pipeline.timelapse import select_timelapse_frames

    paths = []
    for i in range(300):
        p = str(tmp_path / f"frame_{i:04d}.jpg")
        cv2.imwrite(p, np.full((20, 30, 3), 40, np.uint8))
        paths.append(p)
    tel = {"frame_0137.jpg": {"events": ["meteor"]}, "frame_0201.jpg": {"events": ["plane"]}}
    sel, _ = select_timelapse_frames(paths, max_frames=50, telemetry_map=tel)
    names = [p.rsplit("/", 1)[-1] for p in sel]
    assert "frame_0137.jpg" in names and "frame_0201.jpg" in names
    assert names == sorted(names)
