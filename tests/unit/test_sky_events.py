"""Sky event detector: meteors, aircraft, satellites, flashes on synthetic night frames."""

import time

import cv2
import numpy as np
import pytest

from src.analytics.sky_events import SkyEventDetector

W, H = 1296, 972
ALL = {"meteor", "plane", "satellite", "flash"}


def _sky(seed: int, shift: float = 0.0) -> np.ndarray:
    """Dark sky with noise and ~300 stars drifting `shift` px to the right."""
    rng = np.random.default_rng(1234)
    stars = np.c_[rng.uniform(0, W, 300), rng.uniform(0, H, 300), rng.uniform(40, 200, 300)]
    img = np.full((H, W), 18.0, np.float32)
    for x, y, b in stars:
        cv2.circle(img, (int(x + shift), int(y)), 1, float(b), -1, cv2.LINE_AA)
    img += np.random.default_rng(seed).normal(0, 3.0, img.shape).astype(np.float32)
    return cv2.cvtColor(np.clip(img, 0, 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)


def _run(frames, enabled=ALL):
    det = SkyEventDetector()
    out = []
    for i, f in enumerate(frames):
        out += det.process(f, enabled, ts=1000.0 + 15 * i, exposure_s=10.0, gain_db=30.0)
    out += det.process(_sky(99, len(frames) * 0.5), enabled, ts=1000.0 + 15 * len(frames), exposure_s=10.0, gain_db=30.0)
    out += det.process(_sky(98, len(frames) * 0.5 + 0.5), enabled, ts=1000.0 + 15 * (len(frames) + 1), exposure_s=10.0, gain_db=30.0)
    return out


def test_quiet_sky_has_no_events():
    assert _run([_sky(i, i * 0.5) for i in range(6)]) == []


def test_single_frame_streak_is_meteor():
    frames = [_sky(i, i * 0.5) for i in range(4)]
    cv2.line(frames[2], (300, 200), (520, 330), (230, 230, 230), 2, cv2.LINE_AA)
    events = _run(frames)
    assert [e["type"] for e in events] == ["meteor"]
    ev = events[0]
    assert 200 < ev["length_px"] < 300
    assert ev["_crop"].size > 0 and ev["_overview"].shape[1] == 640


def test_dashed_streak_is_plane():
    frames = [_sky(i, i * 0.5) for i in range(4)]
    for k in range(10):  # strobe dots along a line
        x = 600 + k * 30
        cv2.circle(frames[2], (x, 700 - k * 12), 3, (240, 240, 240), -1)
    events = _run(frames)
    assert [e["type"] for e in events] == ["plane"]


def test_streak_continuing_next_frame_is_plane_not_meteor():
    frames = [_sky(i, i * 0.5) for i in range(5)]
    cv2.line(frames[2], (100, 500), (400, 420), (230, 230, 230), 2)
    cv2.line(frames[3], (420, 415), (720, 335), (230, 230, 230), 2)
    events = _run(frames)
    assert [e["type"] for e in events] == ["plane"]
    assert events[0]["frames"] == 2


def test_faint_solid_track_is_satellite_and_can_be_disabled():
    frames = [_sky(i, i * 0.5) for i in range(5)]
    cv2.line(frames[2], (900, 100), (1000, 260), (75, 75, 75), 1)
    cv2.line(frames[3], (1010, 276), (1110, 436), (75, 75, 75), 1)
    assert [e["type"] for e in _run(frames)] == ["satellite"]
    assert _run(frames, enabled={"meteor", "plane"}) == []


def test_meteor_disabled_reports_nothing():
    frames = [_sky(i, i * 0.5) for i in range(4)]
    cv2.line(frames[2], (300, 200), (520, 330), (230, 230, 230), 2)
    assert _run(frames, enabled={"plane"}) == []


def test_exposure_change_and_cloud_motion_are_ignored():
    det = SkyEventDetector()
    a, b = _sky(1), _sky(2, 0.5)
    cv2.line(b, (300, 200), (520, 330), (230, 230, 230), 2)
    assert det.process(a, ALL, ts=0, exposure_s=10.0, gain_db=30) == []
    assert det.process(b, ALL, ts=15, exposure_s=5.0, gain_db=30) == []  # exposure changed: not compared
    c = _sky(3, 1.0)
    c[100:600, 100:900] = cv2.add(c[100:600, 100:900], 60)  # big moving cloud
    assert det.process(c, ALL, ts=30, exposure_s=5.0, gain_db=30) == []


def test_flash():
    det = SkyEventDetector()
    det.process(_sky(1), ALL, ts=0, exposure_s=10.0, gain_db=30)
    bright = cv2.add(_sky(2, 0.5), 80)
    events = det.process(bright, ALL, ts=15, exposure_s=10.0, gain_db=30)
    assert [e["type"] for e in events] == ["flash"]
    det2 = SkyEventDetector()
    det2.process(_sky(1), {"meteor"}, ts=0, exposure_s=10.0, gain_db=30)
    assert det2.process(bright, {"meteor"}, ts=15, exposure_s=10.0, gain_db=30) == []


@pytest.mark.parametrize("w,h", [(1296, 972), (2592, 1944)])
def test_cost_per_frame_is_small(w, h):
    det = SkyEventDetector()
    frames = [cv2.resize(_sky(i, i * 0.5), (w, h)) for i in range(3)]
    det.process(frames[0], ALL, ts=0, exposure_s=10.0, gain_db=30)
    t0 = time.perf_counter()
    for i in range(1, 3):
        det.process(frames[i], ALL, ts=15 * i, exposure_s=10.0, gain_db=30)
    per_frame_ms = (time.perf_counter() - t0) / 2 * 1000
    print(f"{w}x{h}: {per_frame_ms:.1f} ms/frame")
    assert per_frame_ms < 60
