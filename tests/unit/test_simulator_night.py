"""The simulator camera shows a night sky at any wall-clock time, and the auto exposure follows it."""

import time
from datetime import datetime, timezone

import numpy as np

from src.calibration.auto_exposure import DayNightAutoExposure
from src.hardware.simulator import SimulatedIMX335Controller

NOON = datetime(2026, 10, 19, 5, 0, tzinfo=timezone.utc)  # ~12:00 local in Hanoi, sun high


def test_simulator_sky_is_a_night_scene_at_noon(monkeypatch):
    noon = time.struct_time((2026, 10, 19, 12, 0, 0, 0, 292, 0))
    monkeypatch.setattr(time, "localtime", lambda *a: noon)
    cam = SimulatedIMX335Controller(width=640, height=480, seed=3, sky=True)
    plane = np.zeros((480, 640), dtype=np.float32)
    cam._add_sky(plane, 2.0, 10.0)
    yy, xx = np.ogrid[:480, :640]
    inside = plane[(yy - 240) ** 2 + (xx - 320) ** 2 < (0.45 * 480) ** 2]
    assert 0.5 < np.median(inside) < 60  # dark but not black
    assert (inside > np.median(inside) + 40).sum() >= 5  # stars stand out of the background


def test_auto_exposure_force_night_overrides_the_sun():
    frame = np.full((480, 640, 3), 60, dtype=np.uint8)
    day = DayNightAutoExposure(latitude_deg=21.05, longitude_deg=105.91)
    for _ in range(4):  # the profile steps through twilight with hysteresis
        day.process_frame_feedback(frame, dt=NOON)
    assert day.current_profile == "day"
    sim = DayNightAutoExposure(latitude_deg=21.05, longitude_deg=105.91)
    sim.force_night = True
    for _ in range(4):
        sim.process_frame_feedback(frame, dt=NOON)
    assert sim.current_profile == "night"
