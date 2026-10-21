"""Startrails frame gate: only steady night frames reach the max-pixel stack."""

import numpy as np

from src.pipeline.startrails import StartrailsFrameGate


def _sky(level, shift=0, seed=0):
    rng = np.random.default_rng(seed)
    img = np.full((480, 640, 3), level, np.uint8)
    # static foreground (buildings) that phase correlation locks onto
    img[300:480, 40:200] = 140
    img[200:480, 450:600] = 30
    img[150:200, 500:560] = 200
    img = np.clip(img.astype(int) + rng.integers(-3, 4, img.shape), 0, 255).astype(np.uint8)
    return np.roll(img, shift, axis=1) if shift else img


def test_rejects_daylight_flash_dark_and_bumped_frames():
    g = StartrailsFrameGate()
    assert g.check(_sky(60), sun_elev=-5.0) == "daylight"
    for i in range(3):
        assert g.check(_sky(60, seed=i), sun_elev=-30.0) is None
    assert g.check(_sky(250), sun_elev=-30.0) == "saturated"
    assert g.check(_sky(110), sun_elev=-30.0) == "too_bright"
    assert g.check(_sky(20), sun_elev=-30.0) == "too_dark"
    assert g.check(_sky(60, shift=24), sun_elev=-30.0) == "misaligned"
    assert g.check(_sky(62, seed=9), sun_elev=-30.0) is None
    assert g.accepted == 4
    assert g.rejected == {"saturated": 1, "too_bright": 1, "too_dark": 1, "misaligned": 1}


def test_persistent_change_is_adopted_after_streak():
    g = StartrailsFrameGate(reset_after=4)
    for i in range(3):
        assert g.check(_sky(50, seed=i), sun_elev=-30.0) is None
    # moonrise: the sky stays brighter, so after the streak the new level is accepted
    results = [g.check(_sky(90, seed=10 + i), sun_elev=-30.0) for i in range(6)]
    assert results[:4] == ["too_bright"] * 4
    assert results[4] is None and results[5] is None
