import cv2
import numpy as np

from src.analytics.day_objects import DayObjectTracker

W, H = 640, 480


def _sky(seed):
    rng = np.random.default_rng(seed)
    base = np.full((H, W), 190.0, np.float32) + np.linspace(0, 10, W, dtype=np.float32)[None, :]
    img = np.clip(base + rng.normal(0, 2.0, base.shape), 0, 255).astype(np.uint8)
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)


def _run(frames, dt=1.0, t0=1000.0):
    tr = DayObjectTracker()
    out = []
    for i, f in enumerate(frames):
        out += tr.process(f, t0 + i * dt)
    out += tr.process(_sky(999), t0 + len(frames) * dt + 60.0)  # a long gap flushes open tracks
    return tr, out


def test_empty_sky_no_tracks():
    _, out = _run([_sky(i) for i in range(20)])
    assert out == []


def test_flapping_dark_bird_is_a_bird():
    frames = []
    for i in range(20):
        f = _sky(i)
        r = 4 + (3 if i % 2 else 0)  # wing beat: size flickers
        cv2.circle(f, (60 + 25 * i, 200 + int(10 * np.sin(i))), r, (60, 60, 60), -1)
        frames.append(f)
    _, out = _run(frames)
    assert [t["cls"] for t in out] == ["bird"]
    assert out[0]["dark"] == 1.0


def test_steady_slow_straight_dot_is_a_plane():
    frames = []
    for i in range(30):
        f = _sky(i)
        cv2.circle(f, (100 + 8 * i, 100 + 3 * i), 2, (90, 90, 90), -1)
        frames.append(f)
    _, out = _run(frames)
    assert [t["cls"] for t in out] == ["plane"]


def test_static_speck_and_slow_drift_are_ignored():
    frames = []
    for i in range(30):
        f = _sky(i)
        cv2.circle(f, (300, 300), 3, (60, 60, 60), -1)                 # dirt: never moves
        cv2.circle(f, (100 + i // 3, 400), 6, (150, 150, 150), -1)      # creeping wisp: ~0.3 px/s
        frames.append(f)
    _, out = _run(frames)
    assert out == []


def test_whole_frame_change_is_not_an_object():
    frames = [_sky(i) for i in range(10)]
    frames += [np.clip(_sky(i + 10).astype(np.int16) - 60, 0, 255).astype(np.uint8) for i in range(10)]
    tr, out = _run(frames)
    assert out == [] and tr.skipped >= 1


def _drop_layer(seed):
    rng = np.random.default_rng(seed)
    layer = np.zeros((H, W), np.float32)
    yy, xx = np.ogrid[:H, :W]
    for _ in range(60):  # soft smeared drops: bright centre, dark rim, no sharp edge
        x, y, r, a = rng.uniform(40, W - 40), rng.uniform(40, H - 40), rng.uniform(10, 40), rng.uniform(-45, 45)
        d2 = ((xx - x) ** 2 + (yy - y) ** 2) / float(r * r)
        layer += a * (np.exp(-d2) - 0.5 * np.exp(-d2 / 2.5))
    return layer


def _wet(i, layer):
    g = _sky(i)[..., 0].astype(np.float32) + layer
    return cv2.cvtColor(np.clip(g, 0, 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)


def test_drops_smeared_on_the_dome_mean_rain_and_hide_tracks():
    spots = _drop_layer(5)
    tr = DayObjectTracker()
    out = []
    for i in range(200):
        f = _wet(i, spots)
        cv2.circle(f, (30 + 3 * i, 60 + i // 2), 4, (50, 50, 50), -1)  # a bird crossing during the rain
        out += tr.process(f, 1000.0 + 2.0 * i)
    assert tr.raining and tr.rain_corr > 0.8
    assert out == []


def test_rain_ends_when_the_dome_dries():
    spots = _drop_layer(6)
    tr = DayObjectTracker()
    for i in range(120):
        tr.process(_wet(i, spots), 1000.0 + 2.0 * i)
    assert tr.raining
    for i in range(120, 260):
        tr.process(_sky(i), 1000.0 + 2.0 * i)
    assert not tr.raining


def test_dry_sky_is_not_rain():
    tr = DayObjectTracker()
    for i in range(150):
        tr.process(_sky(i), 1000.0 + 2.0 * i)
    assert not tr.raining
