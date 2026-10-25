import numpy as np

from src.pipeline.highlights import HIGHLIGHT_FULL, apply_virtual_nd, desaturate_highlights


def test_clipped_fringe_becomes_neutral_and_not_darker():
    f = np.zeros((2, 2, 3), np.uint8)
    f[0, 0] = (120, 250, 255)  # orange clipped cloud edge (BGR)
    f[0, 1] = (100, 104, 98)  # normal sky stays untouched
    desaturate_highlights(f)
    assert tuple(f[0, 0]) == (255, 255, 255)
    assert tuple(f[0, 1]) == (100, 104, 98)


def test_bright_weakly_coloured_sky_keeps_its_colour():
    f = np.full((8, 8, 3), (200, 190, 180), np.uint8)  # hazy bright sky, chroma 20, below CLIP_START
    before = f.copy()
    desaturate_highlights(f)
    assert np.array_equal(f, before)


def test_near_clipped_low_chroma_tile_is_neutralised_without_square_gap():
    f = np.full((128, 128, 3), (255, 255, 244), np.uint8)  # cyan-tinted sun interior (chroma 11, > CLIP_FULL)
    desaturate_highlights(f)
    assert np.all(f == 255)


def test_virtual_nd_darkens_midtones_and_keeps_smooth_highlight_shoulder():
    ramp = np.repeat(np.arange(256, dtype=np.uint8)[:, None, None], 3, axis=2)
    apply_virtual_nd(ramp, 3.0)
    vals = ramp[:, 0, 0].astype(int)
    assert vals[0] == 0 and vals[255] == 255
    assert 60 <= vals[135] <= 75  # midtones darkened by ~3 stops equivalent
    assert np.all(np.diff(vals) >= 0)  # strictly monotonic
    assert np.max(np.diff(vals)) <= 5  # smooth C1 shoulder up to 255, no hard wall


def test_ramp_partially_neutralises():
    f = np.zeros((1, 1, 3), np.uint8)
    f[0, 0] = (100, HIGHLIGHT_FULL - 25, 100)
    desaturate_highlights(f)
    b, g, r = (int(v) for v in f[0, 0])
    assert g == HIGHLIGHT_FULL - 25 and 100 < b < g and b == r
