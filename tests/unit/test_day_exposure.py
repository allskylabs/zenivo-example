"""Daytime auto exposure on a live camera whose sensor floor is reached (IMX708 NoIR, 53 us)."""

from datetime import datetime, timezone

import numpy as np

from src.calibration.auto_exposure import DayNightAutoExposure

NOON = datetime(2026, 10, 19, 5, 0, tzinfo=timezone.utc)  # ~12:00 local in Hanoi, sun high


def _frame(sky: int, sun_frac: float = 0.15) -> np.ndarray:
    f = np.full((1296, 2304, 3), sky, dtype=np.uint8)
    rows = int(f.shape[0] * sun_frac)
    f[:rows] = 255  # sun + raw-saturated glare: clipped whatever the exposure
    return f


def _ae(exposure_s: float) -> DayNightAutoExposure:
    ae = DayNightAutoExposure(latitude_deg=21.05, longitude_deg=105.91, initial_exposure_s=exposure_s, initial_gain_db=0.0)
    ae.current_profile = "day"
    ae.hw_min_exposure_s = 53e-6 / 8  # sensor floor less three ISP-darkened stops
    return ae


def test_bright_day_sky_darkens_below_the_sensor_floor():
    ae = _ae(53e-6)
    exp, gain, _ = ae.process_frame_feedback(_frame(225), dt=NOON, live_camera=True)
    assert exp < 53e-6 and gain == 0.0
    for _ in range(10):
        exp, gain, _ = ae.process_frame_feedback(_frame(225), dt=NOON, live_camera=True)
    assert abs(exp - 53e-6 / 8) <= 1e-6  # AE rounds to 1 us; the camera clamps to its minimum


def test_sun_clipping_alone_does_not_hold_a_dark_day_sky_down():
    ae = _ae(20e-6)
    exp, _, _ = ae.process_frame_feedback(_frame(80), dt=NOON, live_camera=True)
    assert exp > 20e-6
    assert exp < 20e-6 * 1.5  # fine steps below 100 us, not +20 us (2x) at once


def test_mid_grey_day_sky_with_clipped_sun_is_held():
    ae = _ae(30e-6)
    exp, _, _ = ae.process_frame_feedback(_frame(125), dt=NOON, live_camera=True)
    assert exp == 30e-6
