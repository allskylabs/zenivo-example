"""One-off: convert archived frame_*.jpg to AVIF in place (day folders before today), updating telemetry.jsonl.

Usage: nice python tools/convert_frames_avif.py data/frames [--keep-jpg]
"""
import json
import os
import sys
from datetime import date

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.storage import frame_codec as fc  # noqa: E402


def convert_day(day_dir: str, keep: bool) -> tuple:
    done = saved = 0
    renames = {}
    for name in fc.list_frames(day_dir):
        if not name.endswith(".jpg"):
            continue
        src = os.path.join(day_dir, name)
        img = cv2.imread(src, cv2.IMREAD_COLOR)
        if img is None:
            continue
        out = fc.write_frame(os.path.join(day_dir, name[:-4]), img)
        if not out or not out.endswith(".avif") or fc.read_frame(out) is None:
            continue
        saved += os.path.getsize(src) - os.path.getsize(out)
        renames[name] = os.path.basename(out)
        if not keep:
            os.remove(src)
        done += 1
    tel = os.path.join(day_dir, "telemetry.jsonl")
    if renames and os.path.isfile(tel):
        lines = []
        with open(tel, encoding="utf-8") as fp:
            for ln in fp:
                try:
                    rec = json.loads(ln)
                    rec["filename"] = renames.get(rec.get("filename"), rec.get("filename"))
                    ln = json.dumps(rec) + "\n"
                except ValueError:
                    pass
                lines.append(ln)
        with open(tel + ".tmp", "w", encoding="utf-8") as fp:
            fp.writelines(lines)
        os.replace(tel + ".tmp", tel)
    return done, saved


def main() -> int:
    root = sys.argv[1]
    keep = "--keep-jpg" in sys.argv
    if not fc.HAS_AVIF:
        print("No AVIF encoder (need Pillow >= 11.3 or pillow-avif-plugin)")
        return 1
    today = date.today().isoformat()
    for d in sorted(os.listdir(root)):
        p = os.path.join(root, d)
        if not os.path.isdir(p) or d >= today:
            continue
        n, saved = convert_day(p, keep)
        print(f"{d}: {n} frames, saved {saved / 1e6:.1f} MB", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
