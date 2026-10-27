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
