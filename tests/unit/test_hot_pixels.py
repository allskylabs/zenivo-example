"""Automatic hot-pixel map: static points are learned and repaired, drifting stars are left alone."""

import numpy as np

from src.pipeline.hot_pixels import StaticPointTracker, find_peaks

H, W = 300, 400
HOT = [(50, 60), (120, 200), (300, 90), (350, 250)]


def _frame(t_idx, rng):
    img = rng.normal(40, 2, (H, W, 3)).clip(0, 255).astype(np.uint8)
    for x, y in HOT:
        img[y - 1:y + 2, x - 1:x + 2] = 120
        img[y, x] = 220
    for k in range(8):  # stars drifting 4 px per sample
        x, y = 30 + 40 * k + 4 * t_idx, 150 + 10 * (k % 3)
        img[y - 1:y + 2, x - 1:x + 2] = 110
        img[y, x] = 200
    return img


def test_learns_static_points_not_drifting_stars(tmp_path):
    rng = np.random.default_rng(1)
    tr = StaticPointTracker(str(tmp_path / "hot.json"))
    for i in range(9):
        tr.sample(_frame(i, rng), now=1000.0 + 90 * i)
    assert set(tr.confirmed) == set(HOT)

    img = _frame(9, rng)
    out = tr.repair(img.copy())
    for x, y in HOT:
        assert out[y, x, 0] < 60
    # stars untouched
    x, y = 30 + 4 * 9, 150
    assert out[y, x, 0] == 200

    # persisted and reloaded
    again = StaticPointTracker(str(tmp_path / "hot.json"))
    assert set(again.confirmed) == set(HOT)


def test_needs_time_span_and_resets_on_new_shape():
    rng = np.random.default_rng(2)
    tr = StaticPointTracker()
    for i in range(9):
        tr.sample(_frame(0, rng), now=1000.0 + 10 * i)  # 80 s only: not long enough to be sure
    assert not tr.confirmed
    for i in range(9):
        tr.sample(_frame(i, rng), now=2000.0 + 90 * i)
    assert tr.confirmed
    tr.sample(np.zeros((100, 100, 3), np.uint8), now=5000.0)
    assert not tr.confirmed and tr.repair(np.zeros((100, 100, 3), np.uint8)).shape == (100, 100, 3)


def test_sky_mask_keeps_static_lights_off_the_sky():
    rng = np.random.default_rng(3)
    sky = np.zeros((H, W), bool)
    sky[:, :W // 2] = True
    tr = StaticPointTracker()
    for i in range(9):
        tr.sample(_frame(i, rng), now=1000.0 + 90 * i, sky_mask=sky)
    assert set(tr.confirmed) == {p for p in HOT if p[0] < W // 2}


def test_find_peaks_is_point_like():
    img = np.full((50, 50), 40, np.uint8)
    img[10:40, 10:40] = 200  # extended blob: no peaks inside
    img[5, 45] = 250
    pts = {(int(x), int(y)) for x, y, _ in find_peaks(img)}
    assert (45, 5) in pts
    assert not [p for p in pts if 11 <= p[0] <= 38 and 11 <= p[1] <= 38]
