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


def _dots(n_frames, path, value=220, radius=3, skip=()):
    """Short exposures: an aircraft is one dot per frame along `path` (list of (x, y) per frame)."""
    frames = [_sky(i, i * 0.5) for i in range(n_frames)]
    for i, (x, y) in enumerate(path):
        if i not in skip:
            cv2.circle(frames[i + 1], (int(x), int(y)), radius, (value, value, value), -1)
    return frames


def test_moving_dot_is_plane():
    path = [(200 + 70 * k, 800 - 40 * k) for k in range(7)]
    events = _run(_dots(9, path))
    assert [e["type"] for e in events] == ["plane"]
    ev = events[0]
    assert ev["frames"] == 7 and ev["moving_dot"]
    assert 400 < ev["length_px"] < 600
    assert ev["_crop"].size > 0 and ev["_overview"].shape[1] == 640


def test_blinking_dot_is_plane_even_when_faint():
    path = [(1000 - 50 * k, 150 + 45 * k) for k in range(8)]
    assert [e["type"] for e in _run(_dots(10, path, value=90, skip=(3,)))] == ["plane"]


def test_faint_steady_dot_is_satellite():
    path = [(300 + 30 * k, 300 + 10 * k) for k in range(6)]
    assert [e["type"] for e in _run(_dots(8, path, value=60, radius=2))] == ["satellite"]
    assert _run(_dots(8, path, value=60, radius=2), enabled={"meteor", "plane"}) == []


def test_dots_that_do_not_move_steadily_are_ignored():
    rng = np.random.default_rng(7)
    random_path = [(rng.uniform(100, 1200), rng.uniform(100, 900)) for _ in range(7)]
    assert _run(_dots(9, random_path)) == []
    assert _run(_dots(9, [(640, 480)] * 7)) == []  # twinkling / flickering in place
    assert _run(_dots(5, [(200 + 70 * k, 800 - 40 * k) for k in range(3)])) == []  # too few positions


def test_fuzzy_glint_is_not_a_meteor():
    """A raindrop / smudge on the lens lighting up: a fuzzy blob, not a sharp line."""
    frames = [_sky(i, i * 0.5) for i in range(4)]
    blob = np.zeros((H, W), np.float32)
    cv2.ellipse(blob, (700, 600), (34, 16), 20, 0, 360, 90.0, -1)
    blob = cv2.GaussianBlur(blob, (0, 0), 6)
    frames[2] = cv2.add(frames[2], cv2.cvtColor(np.clip(blob, 0, 255).astype(np.uint8), cv2.COLOR_GRAY2BGR))
    assert [e["type"] for e in _run(frames)] == []


def test_meteor_repeating_at_the_same_spot_is_a_glint():
    det = SkyEventDetector()
    out = []
    for i in range(12):
        f = _sky(i, i * 0.5)
        if i in (2, 8):  # the same streak twice, 90 s apart
            cv2.line(f, (300, 200), (520, 330), (230, 230, 230), 2, cv2.LINE_AA)
        out += det.process(f, ALL, ts=1000.0 + 15 * i, exposure_s=10.0, gain_db=30.0)
    assert [e["type"] for e in out] == ["meteor"]


def test_drifting_cloud_is_not_a_plane():
    """The bright core of a soft cloud drifting slowly across lumpy sky moves like a dot but is no aircraft."""
    rng = np.random.default_rng(3)
    lumps = cv2.GaussianBlur(rng.normal(0, 1, (H, W)).astype(np.float32), (0, 0), 9)
    lumps = 40.0 * lumps / (np.abs(lumps).max() + 1e-6)
    frames = []
    for i in range(10):
        f = _sky(i, i * 0.5).astype(np.float32)
        cloud = np.zeros((H, W), np.float32)
        cv2.ellipse(cloud, (300 + 12 * i, 500 + 4 * i), (60, 30), 15, 0, 360, 70.0, -1)
        cloud = cv2.GaussianBlur(cloud, (0, 0), 10) + np.roll(lumps, 12 * i, axis=1) * (cloud > 5)
        frames.append(np.clip(f + cloud[..., None], 0, 255).astype(np.uint8))
    assert [e["type"] for e in _run(frames) if e["type"] == "plane"] == []


def test_streak_across_lumpy_cloud_is_not_a_plane():
    """Cloud bits brightening along a line read as a dashed streak; the lumpy sky around them gives them away."""
    rng = np.random.default_rng(5)
    lumps = cv2.GaussianBlur(rng.normal(0, 1, (H, W)).astype(np.float32), (0, 0), 6)
    lumps = np.clip(160.0 * lumps / (np.abs(lumps).max() + 1e-6), 0, None)[..., None]  # real cloud: texture 13-40 DN
    frames = [np.clip(_sky(i, i * 0.5).astype(np.float32) + lumps, 0, 255).astype(np.uint8) for i in range(4)]
    for k in range(10):
        cv2.circle(frames[2], (600 + k * 30, 700 - k * 12), 3, (240, 240, 240), -1)
    assert _run(frames) == []


def _drifting_bits(n_frames, starts=((200, 300), (300, 650), (750, 420)), step=(40, 10)):
    """City-lit cloud bits blown by the wind: sharp dots that all move the same way at the same speed."""
    frames = [_sky(i, i * 0.5) for i in range(n_frames)]
    for i in range(1, n_frames - 1):
        for x, y in starts:
            cv2.circle(frames[i], (int(x + step[0] * i), int(y + step[1] * i)), 3, (220, 220, 220), -1)
    return frames


def test_dots_drifting_together_are_cloud():
    assert [e for e in _run(_drifting_bits(9)) if e["type"] in ("plane", "satellite")] == []


def test_aircraft_crossing_the_drift_is_kept():
    frames = _drifting_bits(9)
    for k in range(7):  # an aircraft flying its own way across the drifting bits
        cv2.circle(frames[k + 1], (1100 - 60 * k, 150 + 70 * k), 3, (220, 220, 220), -1)
    assert [e["type"] for e in _run(frames)] == ["plane"]


def test_no_meteor_while_cloud_drifts():
    frames = _drifting_bits(9)
    cv2.line(frames[7], (900, 700), (1150, 820), (230, 230, 230), 2, cv2.LINE_AA)
    assert [e for e in _run(frames) if e["type"] == "meteor"] == []


def test_single_frame_streaks_skipped_under_broken_cloud():
    """Many specks brightening every frame (drifting broken cloud): one-frame streaks are not trusted."""
    rng = np.random.default_rng(11)
    frames = []
    for i in range(25):
        f = _sky(i, i * 0.5)
        for x, y in rng.uniform([50, 50], [W - 50, H - 50], (20, 2)):
            cv2.circle(f, (int(x), int(y)), 3, (200, 200, 200), -1)  # cloud bits lighting up at random
        frames.append(f)
    cv2.line(frames[22], (300, 200), (520, 330), (230, 230, 230), 2, cv2.LINE_AA)
    assert [e for e in _run(frames) if e["type"] == "meteor"] == []


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
    assert det.process(bright, ALL, ts=15, exposure_s=10.0, gain_db=30) == []  # held one frame
    assert det.process(_sky(3, 1.0), ALL, ts=30, exposure_s=10.0, gain_db=30) == []
    events = det.process(_sky(4, 1.5), ALL, ts=45, exposure_s=10.0, gain_db=30)
    assert [e["type"] for e in events] == ["flash"] and events[0]["exposure_s"] == 10.0


def test_flash_gate_drops_lasting_and_repeated_jumps():
    bright = cv2.add(_sky(2, 0.5), 80)
    det = SkyEventDetector()  # sky stays bright: exposure / IR-cut change, not a flash
    det.process(_sky(1), ALL, ts=0, exposure_s=10.0, gain_db=30)
    det.process(bright, ALL, ts=15, exposure_s=10.0, gain_db=30)
    assert det.process(cv2.add(_sky(3, 1.0), 80), ALL, ts=30, exposure_s=10.0, gain_db=30) == []
    det = SkyEventDetector()  # repeating pulses within a minute: a lamp
    out = []
    for i, f in enumerate([_sky(1), bright, _sky(3, 1.0), cv2.add(_sky(4, 1.5), 80), _sky(5, 2.0), _sky(6, 2.5)]):
        out += det.process(f, ALL, ts=15.0 * i, exposure_s=10.0, gain_db=30)
    assert [e for e in out if e["type"] == "flash"] == []
    old = SkyEventDetector(flash_gate=False)  # gate switched off: legacy immediate event
    old.process(_sky(1), ALL, ts=0, exposure_s=10.0, gain_db=30)
    assert [e["type"] for e in old.process(bright, ALL, ts=15, exposure_s=10.0, gain_db=30)] == ["flash"]
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


def test_blooming_strobe_and_multi_frame_gaps_is_plane():
    """Aircraft strobe with optical bloom (area > 70 px at 640px) flashing only every 2nd frame (skipping frame right after seed)."""
    path = [(250 + 45 * k, 750 - 30 * k) for k in range(7)]
    frames = _dots(12, path, value=230, radius=14, skip=(1, 3, 5))
    det = SkyEventDetector()
    events = []
    for i, f in enumerate(frames):
        events += det.process(f, ALL, ts=2000.0 + 2.3 * i, exposure_s=0.25, gain_db=12.0)
    for k in range(4):
        events += det.process(_sky(90 + k, (len(frames) + k) * 0.5), ALL, ts=2000.0 + 2.3 * (len(frames) + k), exposure_s=0.25, gain_db=12.0)
    assert [e["type"] for e in events] == ["plane"]
    assert events[0]["frames"] == 4
    assert events[0]["dashes"] >= 1


def test_short_exposure_twilight_strobe_and_ramp_is_plane():
    """During short twilight exposures (~0.22s ramping up), 2-3 strobe flashes or a single intense flash are caught."""
    det = SkyEventDetector()
    out = []
    # Two strobe flashes separated by 2 dark frames while exposure ramps smoothly
    for i in range(10):
        f = _sky(i, i * 0.2)
        if i in (2, 5):
            cv2.circle(f, (400 + 25 * i, 500 - 15 * i), 12, (220, 220, 220), -1)
        out += det.process(f, ALL, ts=2000.0 + 2.3 * i, exposure_s=0.20 * (1.05 ** i), gain_db=12.0)
    assert [e["type"] for e in out] == ["plane"]




def test_strobe_streak_on_overcast_sky_is_a_plane():
    """Real frames (a live station, 08 Nov 2026 19:31-19:33, 100% cloud): an aircraft strobe lit the overcast deck.
    Glow around the dashes made the streak look fat and fuzzy; it was dropped as 'not a meteor'."""
    import json
    import os
    d = os.path.join(os.path.dirname(__file__), "..", "fixtures", "strobe_overcast")
    mask = json.load(open(os.path.join(d, "sky_mask.json")))
    det = SkyEventDetector()
    out = []
    for i in range(3):
        img = cv2.imread(os.path.join(d, f"f{i}.png"))
        out += det.process(img, ALL, ts=1000.0 + 33 * i, exposure_s=5.4, gain_db=14.9, sky_polygons=mask)
    out += det.process(img, ALL, ts=1000.0 + 99, exposure_s=5.4, gain_db=14.9, sky_polygons=mask)
    assert [e["type"] for e in out] == ["plane"]


def _masked_run(streak_value, y):
    """Sky mask = top half of the frame (the rest is houses / trees); a streak of the given brightness at height y."""
    frames = [_sky(i, i * 0.5) for i in range(4)]
    cv2.line(frames[2], (300, y), (520, y + 60), (streak_value,) * 3, 2, cv2.LINE_AA)
    mask = {"sky": [[[0.0, 0.0], [1.0, 0.0], [1.0, 0.5], [0.0, 0.5]]]}
    det = SkyEventDetector()
    out = []
    for i, f in enumerate(frames + [_sky(99, 2.0), _sky(98, 2.5)]):
        out += det.process(f, ALL, ts=1000.0 + 15 * i, exposure_s=10.0, gain_db=30.0, sky_polygons=mask)
    return out


def test_faint_streak_outside_the_sky_mask_is_ignored_but_a_bright_one_is_kept():
    in_sky = _masked_run(60, 100)           # top half: sky
    assert [e["type"] for e in in_sky] == ["meteor"]
    assert _masked_run(60, 700) == []       # same streak over the trees: very low sensitivity
    assert [e["type"] for e in _masked_run(250, 700)] == ["meteor"]  # but a really bright one still counts


def test_slow_drifting_star_is_not_a_plane():
    """A twinkling star (Pleiades) drifts ~0.1 px/s for a long time: the same dots as a plane, 50x slower."""
    path = [(200 + 3 * k, 800 - 1.5 * k) for k in range(24)]
    assert _run(_dots(26, path)) == []


def test_strobe_dots_on_a_solid_trail_are_a_plane_not_a_meteor():
    """Long exposure: the aircraft leaves a faint continuous line with a bright dot per strobe flash."""
    frames = [_sky(i, i * 0.5) for i in range(4)]
    cv2.line(frames[2], (400, 150), (800, 450), (110, 110, 110), 2, cv2.LINE_AA)
    for k in range(14):
        cv2.circle(frames[2], (400 + int(k * 400 / 13), 150 + int(k * 300 / 13)), 3, (235, 235, 235), -1)
    assert [e["type"] for e in _run(frames)] == ["plane"]


def test_smooth_streak_with_one_bright_spot_stays_a_meteor():
    frames = [_sky(i, i * 0.5) for i in range(4)]
    cv2.line(frames[2], (300, 200), (520, 330), (150, 150, 150), 2, cv2.LINE_AA)
    cv2.circle(frames[2], (420, 271), 3, (240, 240, 240), -1)
    assert [e["type"] for e in _run(frames)] == ["meteor"]


def _fed_detector(frames):
    det = SkyEventDetector()
    for i, f in enumerate(frames):
        det.process(f, ALL, ts=1000.0 + 90 * i, exposure_s=10.0, gain_db=30.0)
    return det


def test_dots_on_persistent_stars_are_recognised_as_stars():
    """_on_stars: dots sitting where a star is also bright in the other recent frames vs dots that really moved."""
    path = [(300 + 30 * k, 300 + 10 * k) for k in range(6)]
    # work-size positions (the detector works at 640 px wide, the frames are 1296 px)
    s = 640 / W
    pts = [(x * s, y * s, 1000.0 + 90 * (k + 1), 55) for k, (x, y) in enumerate(path)]
    stars = _dots(8, path, value=100, radius=2)
    for f in stars:
        for x, y in path:
            cv2.circle(f, (x, y), 2, (150, 150, 150), -1, cv2.LINE_AA)
    assert _fed_detector(stars)._on_stars(pts)
    assert not _fed_detector(_dots(8, path, value=100, radius=2))._on_stars(pts)


def _streak_frame(y_offsets, width=1):
    base = _sky(1)
    img = _sky(2, 0.5)
    for dy in y_offsets:
        cv2.line(img, (300, 500 + dy), (520, 440 + dy), (200, 200, 200), width, cv2.LINE_AA)
    return base, img


def _meteor_types(y_offsets, width=1, **kw):
    base, img = _streak_frame(y_offsets, width)
    det = SkyEventDetector(**kw)
    out = det.process(base, ALL, ts=0, exposure_s=10.0, gain_db=30)
    out += det.process(img, ALL, ts=15, exposure_s=10.0, gain_db=30)
    out += det.process(_sky(3, 1.0), ALL, ts=30, exposure_s=10.0, gain_db=30)
    out += det.process(_sky(4, 1.5), ALL, ts=45, exposure_s=10.0, gain_db=30)
    return [e["type"] for e in out]


def test_meteor_gate_single_thin_line_kept_double_and_thick_become_plane():
    assert _meteor_types([0]) == ["meteor"]                    # the true-meteor case must survive
    assert set(_meteor_types([0, 25])) == {"plane"}             # landing-light pair
    assert _meteor_types([0], width=10) == ["plane"]           # thick bar (~9 px)
    assert "meteor" in _meteor_types([0, 25], meteor_gate=False)  # gate off = legacy
