#!/usr/bin/env python3
"""Push the Pi's live aircraft list to virtuallab3d (snapshot replaces the previous one).

Reads the local allsky API and POSTs it as JSON. A failed push is dropped, never queued; repeated failures
back off. Config comes from the environment (systemd EnvironmentFile, never committed):
  VLAB_URL        full ingest URL, e.g. https://staging.virtuallab3d.com/api/v1/sky/aircraft
  VLAB_KEY        service key (Bearer, scope sky:ingest)
  VLAB_INTERVAL   seconds between pushes (default 3, min 2)
  ALLSKY_URL      local API base (default http://127.0.0.1:8095)
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

LOCAL = os.environ.get("ALLSKY_URL", "http://127.0.0.1:8095").rstrip("/")
URL = os.environ.get("VLAB_URL", "")
KEY = os.environ.get("VLAB_KEY", "")
INTERVAL = max(2.0, float(os.environ.get("VLAB_INTERVAL", "3")))
BACKOFF_MAX = 60.0
AUTH_BACKOFF = 300.0


def snapshot() -> dict:
    # trail=1 adds lat/lon, station and camera pointing (not long paths); see AllSkyService.get_aircraft_now
    with urllib.request.urlopen(f"{LOCAL}/api/adsb/aircraft?limit=400&trail=1", timeout=5) as r:
        return json.load(r)


def push(body: dict) -> int:
    req = urllib.request.Request(URL, data=json.dumps(body, separators=(",", ":")).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {KEY}",
                                          "User-Agent": "imx335-allsky-feeder/1"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def main() -> int:
    if not URL or not KEY:
        print("VLAB_URL / VLAB_KEY not set", file=sys.stderr)
        return 2
    delay, fails = INTERVAL, 0
    while True:
        t0 = time.time()
        try:
            snap = snapshot()
            if not snap.get("enabled"):
                status = None  # no receiver: nothing to send
            else:
                now = datetime.now(timezone.utc)
                snap.pop("station", None)  # the observatory's own position and camera pointing stay on the Pi
                snap.pop("camera", None)
                status = push({"ts": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
                               "ts_epoch": round(now.timestamp(), 1), "source": "imx335-allsky", **snap})
            if status is None or 200 <= status < 300:
                fails, delay = 0, INTERVAL
            else:
                fails += 1
                delay = AUTH_BACKOFF if status in (401, 403) else min(BACKOFF_MAX, INTERVAL * 2 ** fails)
                print(f"push rejected: HTTP {status}, next try in {delay:.0f}s", file=sys.stderr, flush=True)
        except Exception as e:  # network / local API down: drop this snapshot
            fails += 1
            delay = min(BACKOFF_MAX, INTERVAL * 2 ** fails)
            print(f"push failed: {type(e).__name__}: {e}, next try in {delay:.0f}s", file=sys.stderr, flush=True)
        time.sleep(max(0.5, delay - (time.time() - t0)))


if __name__ == "__main__":
    sys.exit(main())
