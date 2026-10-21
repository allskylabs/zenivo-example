"""Timelapse frame quality check: wrong-size and brightness-glitch frames are left out of the video."""

import cv2
import numpy as np

from src.pipeline.timelapse import frame_quality_mask


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
