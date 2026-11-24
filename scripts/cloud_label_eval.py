"""Offline check of the cloud-cover estimate against the hand-labelled frames in tests/fixtures/cloud_labels.

    python scripts/cloud_label_eval.py        # table + MAE / worst error / overcast frames read below 50 %

Frames are 640 px thumbnails of real archive frames (Hanoi station), labelled by eye (label_pct). Night frames are
judged by colour/luma only here (no star solution, no learned template), so the headline numbers are for the day set
(sun above -6 deg). The station sky mask is the one the Pi uses.
"""
import csv
import os
import sys

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from src.analytics.cloud_cover import analyze_cloud_cover  # noqa: E402

DIR = os.path.join(ROOT, "tests", "fixtures", "cloud_labels")
SKY = {"sky": [[[0.0246, 0.0446], [0.9722, 0.0248], [0.9873, 0.4171], [0.9738, 0.9081], [0.6468, 0.9307],
                [0.377, 0.9617], [0.2929, 0.8714], [0.1603, 0.8404], [0.0563, 0.9405], [0.031, 0.8897],
                [0.023, 0.0488]]], "obstruction": []}


def evaluate(day_abs: bool, legacy: bool = False):
    rows = []
    for r in csv.DictReader(open(os.path.join(DIR, "labels.csv"))):
        frame = cv2.imread(os.path.join(DIR, r["frame"]))
        sun = float(r["sun_elev"])
        out = analyze_cloud_cover(frame, 0.01, 0.0, sun, sky_polygons=SKY, count_stars=False, day_abs=day_abs, legacy=legacy)
        rows.append({"frame": r["frame"][:15], "sun": sun, "label": float(r["label_pct"]),
                     "cc": out["cloud_cover_pct"] if out["cloud_cover_pct"] is not None else 0.0})
    return rows


def summary(rows):
    err = [abs(r["cc"] - r["label"]) for r in rows]
    return {"n": len(rows), "mae": sum(err) / len(err), "worst": max(err),
            "overcast_below_50": sum(1 for r in rows if r["label"] >= 80 and r["cc"] < 50)}


def day(rows):
    return [r for r in rows if r["sun"] > -6.0]


if __name__ == "__main__":
    old, new = evaluate(True, legacy=True), evaluate(True)
    print("frame            sun   label   old    new")
    for o, n in zip(old, new):
        print(f'{o["frame"]} {o["sun"]:6.1f} {o["label"]:5.0f} {o["cc"]:6.1f} {n["cc"]:6.1f}')
    for name, sel in (("day", day), ("all", lambda x: x)):
        print(name, "old", summary(sel(old)), "new", summary(sel(new)))
