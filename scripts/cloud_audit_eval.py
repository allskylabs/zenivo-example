"""Offline audit of the cloud-cover estimate on the 70 hand-labelled frames in tests/fixtures/cloud_audit.

    python scripts/cloud_audit_eval.py            # table + agreement / MAE, offline analyzer vs deployed value

Frames are 960 px copies of real archive frames (Hanoi station, IMX335), labelled by eye (label_pct). Classes:
clear < 25, partly 25..70, overcast >= 70. deployed_pct is what the service actually showed at the time.
Night frames have no star solution here, so the star-clarity cue is not part of the offline number.
"""
import csv
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from src.analytics.cloud_cover import analyze_cloud_cover  # noqa: E402
from src.analytics.riseset import _altaz, moon_phase, moon_radec  # noqa: E402
from src.calibration.auto_exposure import calculate_solar_elevation  # noqa: E402

DIR = os.path.join(ROOT, "tests", "fixtures", "cloud_audit")
LAT, LON = 21.11285, 105.43534
TZ = timezone(timedelta(hours=7))


def cls(pct):
    return "clear" if pct < 25 else ("partly" if pct < 70 else "overcast")


def station():
    st = json.load(open(os.path.join(DIR, "station.json")))
    s = st["sol"]
    cx, cy = s["optical_center_px"]
    circle = (0.5, 0.5, s["focal_px"] * 1.5708 * 0.97 / (2 * cx))
    return st["sky_mask"], circle


def sun_moon(local_time):
    dt = datetime.fromisoformat(local_time).replace(tzinfo=TZ)
    t = dt.timestamp()
    ra, dec, _ = moon_radec(t)
    alt, _az = _altaz(ra, dec, t, LAT, LON)
    return calculate_solar_elevation(LAT, LON, dt), alt, moon_phase(t)["illum"]


def rows(analyze=None):
    """One dict per labelled frame: label, deployed value and the value of `analyze(frame_bgr, sun, moon_alt,
    moon_illum, mask, circle)` (a cloud % or None; default: the analyzer in src/analytics/cloud_cover.py)."""
    mask, circle = station()
    out = []
    for r in csv.DictReader(open(os.path.join(DIR, "labels.csv"))):
        frame = cv2.imread(os.path.join(DIR, r["frame"]))
        sun, malt, mill = sun_moon(r["local_time"])
        if analyze is None:
            res = analyze_cloud_cover(frame, float(r["exp_s"]), 0.0, sun, sky_polygons=mask, circle=circle,
                                      count_stars=False)
            val = res["cloud_cover_pct"]
        else:
            val = analyze(frame, sun, malt, mill, mask, circle)
        out.append({"frame": r["frame"], "t": r["local_time"][5:16], "sun": sun, "moon": malt, "illum": mill,
                    "label": float(r["label_pct"]), "deployed": float(r["deployed_pct"]), "value": val})
    return out


def score(rs, key):
    ok = n = 0
    err = 0.0
    for r in rs:
        v = r[key]
        v = 0.0 if v is None else v
        n += 1
        ok += cls(v) == cls(r["label"])
        err += abs(v - r["label"])
    return {"n": n, "agree_pct": round(100.0 * ok / n, 1), "mae": round(err / n, 1)}


def groups(rs):
    return {"all": rs,
            "day (sun>0)": [r for r in rs if r["sun"] > 0],
            "twilight (-12..0)": [r for r in rs if -12 < r["sun"] <= 0],
            "night moonless (moon<0)": [r for r in rs if r["sun"] <= -12 and r["moon"] < 0],
            "night moon up": [r for r in rs if r["sun"] <= -12 and r["moon"] >= 0]}


if __name__ == "__main__":
    rs = rows()
    for name, g in groups(rs).items():
        if g:
            print(f"{name:26s} deployed {score(g, 'deployed')}  offline {score(g, 'value')}")
    if "-v" in sys.argv:
        for i, r in enumerate(rs, 1):
            print(f"{i:2d} {r['t']} sun {r['sun']:6.1f} moon {r['moon']:6.1f}/{r['illum']:.2f} label {r['label']:4.0f} "
                  f"deployed {r['deployed']:5.1f} offline {r['value'] if r['value'] is not None else -1:5.1f}")
