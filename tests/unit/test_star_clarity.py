from datetime import datetime, timezone

import numpy as np

from src.analytics.adsb import pointing_rotation
from src.analytics.star_clarity import star_clarity
from src.calibration import bright_star_solver as bss

LAT, LON = 21.05, 105.91
WHEN = datetime(2026, 10, 27, 19, 0, tzinfo=timezone.utc)
SOL = {"pointing_az_deg": 138.0, "pointing_alt_deg": 31.0, "roll_deg": 0.0, "focal_px": 845.4,
       "optical_center_px": [576.0, 324.0], "projection": "rectilinear", "distortion_k": 0.0}


def _star_positions():
    stars = [s for s in bss.load_deep_catalog() if s["mag"] <= 3.4]
    aa = bss.stars_alt_az(stars, LAT, LON, WHEN)
    keep = aa[:, 0] >= 8.0
    R = pointing_rotation(SOL["pointing_az_deg"], SOL["pointing_alt_deg"], 0.0)
    xy, ok = bss._project(R, bss._enu(aa[keep, 0], aa[keep, 1]), 845.4, 576.0, 324.0)
    return [(int(round(x)), int(round(y))) for (x, y), g in zip(xy, ok) if g and 20 <= x < 1132 and 20 <= y < 628]


def test_clarity_is_full_with_stars_and_zero_without():
    pos = _star_positions()
    assert len(pos) >= 5
    rng = np.random.default_rng(1)
    sky = (rng.normal(40, 2, (648, 1152))).clip(0, 255).astype(np.uint8)
    blank = star_clarity(sky, SOL, LAT, LON, WHEN)
    assert blank["expected"] >= 5 and blank["clarity"] == 0.0
    lit = sky.copy()
    for x, y in pos:
        lit[y - 1:y + 2, x - 1:x + 2] = 200
    assert star_clarity(lit, SOL, LAT, LON, WHEN)["clarity"] >= 0.9


def test_drifted_pointing_still_finds_the_stars():
    pos = _star_positions()
    rng = np.random.default_rng(2)
    sky = (rng.normal(40, 2, (648, 1152))).clip(0, 255).astype(np.uint8)
    lit = sky.copy()
    for x, y in pos:  # the camera moved: every star sits 14 px right / 9 px up of where the solution says
        lit[y - 10:y - 7, x + 13:x + 16] = 200
    got = star_clarity(lit, SOL, LAT, LON, WHEN)
    dx, dy = got["shift_px"]  # a 5x5 peak window makes shifts within 2 px of the true one equivalent
    assert got["clarity"] >= 0.8 and abs(dx - 14) <= 3 and abs(dy + 9) <= 3


def test_scale_comes_from_the_solved_frame_width_not_from_the_optical_centre():
    """A free-lens fit has its optical centre off the frame centre; the frame it was solved on says the scale."""
    sol = dict(SOL, optical_center_px=[560.0, 330.0], frame_width=1152, frame_height=648)
    stars = [s for s in bss.load_deep_catalog() if s["mag"] <= 3.4]
    aa = bss.stars_alt_az(stars, LAT, LON, WHEN)
    keep = aa[:, 0] >= 8.0
    R = pointing_rotation(sol["pointing_az_deg"], sol["pointing_alt_deg"], 0.0)
    xy, ok = bss._project(R, bss._enu(aa[keep, 0], aa[keep, 1]), 845.4, 560.0, 330.0)
    rng = np.random.default_rng(3)
    half = rng.normal(40, 2, (324, 576)).clip(0, 255).astype(np.uint8)   # shown at half the solved size
    for (x, y), g in zip(xy, ok):
        if g and 20 <= x < 1132 and 20 <= y < 628:
            half[int(y / 2) - 1:int(y / 2) + 2, int(x / 2) - 1:int(x / 2) + 2] = 200
    got = star_clarity(half, sol, LAT, LON, WHEN)
    assert got["clarity"] >= 0.9 and got["shift_px"] == (0, 0)
