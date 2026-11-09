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


def test_drops_on_the_dome_mean_rain_and_hide_tracks():
    rng = np.random.default_rng(5)
    spots = [(int(x), int(y)) for x, y in rng.uniform([40, 40], [W - 40, H - 40], (40, 2))]
    tr = DayObjectTracker()
    out = []
    for i in range(60):
        f = _sky(i)
        for k, (x, y) in enumerate(spots):
            if i >= 5 + k:  # drops arrive one after another and stay
                cv2.circle(f, (x, y), 5, (150, 150, 150), -1)
                cv2.circle(f, (x, y), 5, (110, 110, 110), 1)
        cv2.circle(f, (30 + 9 * i, 60 + 2 * i), 4, (50, 50, 50), -1)  # a bird crossing during the rain
        out += tr.process(f, 1000.0 + i)
    assert tr.raining
    assert out == []
