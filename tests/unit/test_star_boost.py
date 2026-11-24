import numpy as np

from src.pipeline.star_boost import sky_polygon_mask, star_bin_boost


def _sky(seed=0, h=240, w=320, level=60, noise=2.0):
    rng = np.random.default_rng(seed)
    g = np.clip(rng.normal(level, noise, (h, w)), 0, 255).astype(np.uint8)
    return np.dstack([g, g, g])


def test_faint_star_gets_brighter_and_sky_stays():
    frame = _sky()
    frame[100:102, 150:152] = 85  # faint 2x2 star
    out = star_bin_boost(frame, 2)
    assert out.shape == frame.shape and out.dtype == np.uint8
    assert out[100:102, 150:152].max() > 110
    # background brightness and noise practically unchanged
    bg_in, bg_out = frame[:60, :60].astype(float), out[:60, :60].astype(float)
    assert abs(bg_out.mean() - bg_in.mean()) < 1.0
    assert bg_out.std() < bg_in.std() * 1.3


def test_bigger_window_is_brighter():
    frame = _sky(1)
    frame[50:53, 60:63] = 80
    peaks = [int(star_bin_boost(frame, n)[50:53, 60:63].max()) for n in (2, 3, 4)]
    # the local noise-floor scaling (4ac19c2) flattens 3 vs 4: allow a few levels of slack
    assert peaks[0] < peaks[1] and peaks[2] > peaks[0] and peaks[2] >= peaks[1] - 6


def test_sky_mask_limits_boost():
    frame = _sky(2)
    frame[100:102, 40:42] = 90   # left half
    frame[100:102, 280:282] = 90  # right half
    mask = sky_polygon_mask(frame.shape, {"sky": [[[0, 0], [0.5, 0], [0.5, 1], [0, 1]]]})
    out = star_bin_boost(frame, 2, sky_mask=mask)
    assert out[100:102, 40:42].max() > 110
    assert np.array_equal(out[100:102, 280:282], frame[100:102, 280:282])


def test_mask_none_without_polygons_and_bin1_passthrough():
    assert sky_polygon_mask((10, 10), {}) is None
    frame = _sky(3)
    assert star_bin_boost(frame, 1) is frame
