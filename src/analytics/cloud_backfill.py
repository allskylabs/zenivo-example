"""
Fill the cloud cover history from frames already archived in data/frames/<day>/.

    nice -n 19 python -m src.analytics.cloud_backfill            # all archived days
    nice -n 19 python -m src.analytics.cloud_backfill 2026-10-16 # selected days
    nice -n 19 python -m src.analytics.cloud_backfill --redo     # recompute (after an algorithm change)

One frame per analysis interval (default 300 s) is analysed, using exposure/gain from the day's
telemetry.jsonl; samples already in the history are skipped (--redo drops the selected days' samples first).
The clear-sky template is learned in memory only (warmed up on the archived nights first when the station
has not learned one yet), so a running service keeps ownership of the reference file.
"""

import argparse
import glob
import json
import os
import sys
from datetime import datetime

import cv2

from src.analytics.cloud_cover import MIN_TEMPLATE_SAMPLES, CloudReference, analyze_cloud_cover
from src.analytics.cloud_history import CloudHistory
from src.storage import frame_codec
from src.calibration.auto_exposure import calculate_solar_elevation

DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("days", nargs="*", help="YYYY-MM-DD folders under data/frames (default: all)")
    ap.add_argument("--data", default=DATA_DIR, help="data directory")
    ap.add_argument("--every", type=float, default=None, help="seconds between samples (default: performance setting)")
    ap.add_argument("--redo", action="store_true", help="drop the days' existing samples and recompute them")
    ap.add_argument("--save-reference", action="store_true",
                    help="store the learned template as the station's reference (only while the service is stopped)")
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
    reference.template = None if seed.template is None else seed.template.copy()
    reference.night_samples = seed.night_samples
    sky_polygons = cfg.get("sky_mask")

    history = CloudHistory(os.path.join(args.data, "logs", "cloud_history.jsonl"))
    days = args.days or sorted(os.path.basename(d) for d in glob.glob(os.path.join(args.data, "frames", "????-??-??")))
    if args.redo:
        for day in days:
            start = datetime.strptime(day, "%Y-%m-%d").astimezone()  # local midnight: folders are local days
            dropped = history.remove_range(start.timestamp(), start.timestamp() + 86400.0)
            print(f"{day}: dropped {dropped} old samples", flush=True)
    have = {int(r["t"]) for r in history.query(0, 4e9, max_points=10**9)}

    def samples():
        """(day, t, sun elevation, downscaled frame, telemetry) per analysis interval, oldest first."""
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
            for path in [os.path.join(day_dir, f) for f in frame_codec.list_frames(day_dir)]:
                name = os.path.basename(path)
                # Telemetry carries the offset (older archives were named in UTC); bare names are local time
                try:
                    ts_iso = tel.get(name, {}).get("timestamp")
                    dt = datetime.fromisoformat(ts_iso) if ts_iso else datetime.strptime(name[6:21], "%Y%m%d_%H%M%S")
                    dt = dt.astimezone()
                except (ValueError, TypeError):
                    continue
                t = dt.timestamp()
                if t - last_t < every:
                    continue
                last_t = t
                img = frame_codec.read_frame(path)
                if img is None:
                    continue
                h, w = img.shape[:2]
                if w > aw:
                    img = cv2.resize(img, (aw, round(h * aw / w)), interpolation=cv2.INTER_AREA)
                yield day, t, float(calculate_solar_elevation(lat, lon, dt)), img, tel.get(name, {})

    if reference.night_samples < MIN_TEMPLATE_SAMPLES:
        # No learned clear-sky template yet: learn it from these nights first, so early samples get it too
        for _, _, sun, img, meta in samples():
            if sun < -12.0:
                analyze_cloud_cover(img, meta.get("exposure_s", 1.0), meta.get("gain_db", 0.0), sun, roi=roi,
                                    reference=reference, sky_polygons=sky_polygons, count_stars=False)
        print(f"Clear-sky template warmed up on {reference.night_samples} night samples", flush=True)
        if args.save_reference and reference.template is not None:
            seed.template, seed.night_samples = reference.template.copy(), reference.night_samples
            seed.save()
            print(f"Saved it to {seed.path}", flush=True)

    added, cur_day = 0, None
    for day, t, sun, img, meta in samples():
        if day != cur_day and cur_day is not None:
            print(f"{cur_day}: done ({added} samples so far)", flush=True)
        cur_day = day
        if int(t) in have:
            continue
        res = analyze_cloud_cover(img, meta.get("exposure_s", 1.0), meta.get("gain_db", 0.0), sun, roi=roi,
                                  reference=reference, sky_polygons=sky_polygons)
        history.append({
            "t": int(t), "cc": res["cloud_cover_pct"], "m": res["method"], "conf": res["confidence"],
            "stars": int(res.get("star_count", 0)), "sun": round(sun, 1),
        })
        added += 1
    if cur_day is not None:
        print(f"{cur_day}: done ({added} samples so far)", flush=True)
    print(f"Added {added} samples to {history.path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
