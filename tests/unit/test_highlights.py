import numpy as np

from src.pipeline.highlights import HIGHLIGHT_FULL, desaturate_highlights


def test_clipped_fringe_becomes_neutral_and_not_darker():
    f = np.zeros((2, 2, 3), np.uint8)
    f[0, 0] = (120, 250, 255)  # orange clipped cloud edge (BGR)
    f[0, 1] = (100, 104, 98)  # normal sky stays untouched
    desaturate_highlights(f)
    assert tuple(f[0, 0]) == (255, 255, 255)
    assert tuple(f[0, 1]) == (100, 104, 98)


def test_bright_weakly_coloured_sky_keeps_its_colour():
    f = np.full((8, 8, 3), (215, 205, 190), np.uint8)  # hazy bright sky, chroma 25
    before = f.copy()
    desaturate_highlights(f)
    assert np.array_equal(f, before)


def test_ramp_partially_neutralises():
    f = np.zeros((1, 1, 3), np.uint8)
    f[0, 0] = (100, HIGHLIGHT_FULL - 25, 100)
    desaturate_highlights(f)
    b, g, r = (int(v) for v in f[0, 0])
    assert g == HIGHLIGHT_FULL - 25 and 100 < b < g and b == r
