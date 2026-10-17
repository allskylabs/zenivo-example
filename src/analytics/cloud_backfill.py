"""
Fill the cloud cover history from frames already archived in data/frames/<day>/.

    nice -n 19 python -m src.analytics.cloud_backfill            # all archived days
    nice -n 19 python -m src.analytics.cloud_backfill 2026-10-16 # selected days

One frame per analysis interval (default 300 s) is analysed, using exposure/gain from the day's
telemetry.jsonl; samples already in the history are skipped. The clear-sky reference is learned
in memory only, so a running service keeps ownership of the reference file.
"""

import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone

import cv2

from src.analytics.cloud_cover import CloudReference, analyze_cloud_cover
from src.analytics.cloud_history import CloudHistory
from src.calibration.auto_exposure import calculate_solar_elevation

DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("days", nargs="*", help="YYYY-MM-DD folders under data/frames (default: all)")
    ap.add_argument("--data", default=DATA_DIR, help="data directory")
    ap.add_argument("--every", type=float, default=None, help="seconds between samples (default: performance setting)")
    args = ap.parse_args()

    cfg = {}
    try:
        with open(os.path.join(args.data, "station_config.json"), "r", encoding="utf-8") as fp:
            cfg = json.load(fp)
    except (OSError, ValueError):
        pass
    lat, lon = float(cfg.get("latitude_deg", 21.0285)), float(cfg.get("longitude_deg", 105.8542))
    roi = cfg.get("sky_roi") or [0.0, 0.0, 1.0, 1.0]
    perf = cfg.get("performance") or {}
    every = args.every or float(perf.get("analysis_interval_s", 300.0))
    aw = int(perf.get("analysis_max_width", 960))

    seed = CloudReference(os.path.join(args.data, "calibration", "cloud_reference.json"))
    reference = CloudReference(None)
    reference.night_cells, reference.night_samples = list(seed.night_cells), seed.night_samples
    reference.day_clear, reference.day_samples = seed.day_clear, seed.day_samples

    history = CloudHistory(os.path.join(args.data, "logs", "cloud_history.jsonl"))
    have = {int(r["t"]) for r in history.query(0, 4e9, max_points=10**9)}

    days = args.days or sorted(os.path.basename(d) for d in glob.glob(os.path.join(args.data, "frames", "????-??-??")))
    added = 0
    for day in days:
        day_dir = os.path.join(args.data, "frames", day)
        tel = {}
        try:
            with open(os.path.join(day_dir, "telemetry.jsonl"), "r", encoding="utf-8") as fp:
                for line in fp:
                    try:
                        r = json.loads(line)
                        tel[r["filename"]] = r
                    except (ValueError, KeyError):
                        continue
        except OSError:
            pass
        last_t = 0.0
        for path in sorted(glob.glob(os.path.join(day_dir, "frame_*.jpg"))):
            name = os.path.basename(path)
            try:
                dt = datetime.strptime(name[6:21], "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            t = dt.timestamp()
            if t - last_t < every:
                continue
            last_t = t
            if int(t) in have:
                continue
            img = cv2.imread(path, cv2.IMREAD_COLOR)
            if img is None:
                continue
            h, w = img.shape[:2]
            if w > aw:
                img = cv2.resize(img, (aw, round(h * aw / w)), interpolation=cv2.INTER_AREA)
            meta = tel.get(name, {})
            sun = float(calculate_solar_elevation(lat, lon, dt))
            res = analyze_cloud_cover(img, meta.get("exposure_s", 1.0), meta.get("gain_db", 0.0), sun, roi=roi, reference=reference)
            history.append({
                "t": int(t), "cc": res["cloud_cover_pct"], "m": res["method"], "conf": res["confidence"],
                "stars": int(res.get("star_count", 0)), "sun": round(sun, 1),
            })
            added += 1
        print(f"{day}: done ({added} samples so far)", flush=True)
    print(f"Added {added} samples to {history.path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
