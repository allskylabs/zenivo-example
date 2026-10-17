# IMX335 AllSky (allsky-ng)

Lightweight all-sky observatory software for a single camera: capture, dark calibration,
day/night auto-exposure, live view, keogram / startrails / timelapse, cloud cover, sky events
(meteors, aircraft, satellites), focus assistant, plate solving and sync to hoan.uk.

It runs on a Raspberry Pi 4 (1.8 GB) as well as on a laptop/mini-PC. Keeping CPU and RAM use low
is the main design constraint: heavy analysis is sampled, never done per frame.

Supported cameras (picked automatically with `--camera auto`, in this order):

1. USB 5MP Sony Starvis IMX335 on a Realtek RTS5869 bridge (`0bda:5869`), with direct sensor
   register control (long exposures, 10-bit RAW)
2. Raspberry Pi CSI cameras via picamera2/libcamera (e.g. IMX708 NoIR)
3. Any V4L2/UVC webcam
4. A built-in simulator (CI, tests, no hardware)

## Quick start

```bash
pip install -r requirements.txt fastapi uvicorn opencv-python requests httpx   # picamera2 comes from apt on a Pi
python3 run_allsky.py --port 8080
```

Open http://localhost:8080. Pages: `/live`, `/gallery`, `/settings`, `/calibration`, `/logs`, `/focus`.

Useful flags (`python3 run_allsky.py --help` for all):

| Flag | Default | Meaning |
|------|---------|---------|
| `--camera` | `auto` (`$ALLSKY_CAMERA`) | `auto`, `rts5869`, `picamera`, `v4l2`, `simulator` |
| `--host` / `--port` | `0.0.0.0` / `8080` | Web UI and API bind address |
| `--resolution` | sensor default | Capture size `WIDTHxHEIGHT` |
| `--sync-target` | `http://localhost:3005` | hoan.uk server that receives frames and telemetry |
| `--sync-interval` | `60` | Seconds between pushes |
| `--prune-dir PATH:DAYS` | – | Extra directory cleaned by the retention job |
| `--simulator` | off | Force the simulator |

## Time zone

All recorded times are **system local time** with an explicit offset
(e.g. `2026-10-18T06:34:17+07:00`). Archive folders, frame file names, event folders and dated
products use the local date `YYYY-MM-DD`. Set the machine's time zone (`timedatectl
set-timezone ...`) before running. Astronomical calculations convert to UTC internally.
Archives written before 2026-10-18 06:34 (+07) are named in UTC; their telemetry carries the
offset, so tools read times from telemetry rather than from file names.

## Data

Runtime data lives in `data/` (git-ignored): archived frames per day, live image, logs,
calibration darks, products and `station_config.json` (location, optics, storage policy, ...).
See [docs/SDD.md](docs/SDD.md) for the full layout.

## Tests

```bash
python3 -m pytest -q
```

Some hardware tests open the real camera if one is connected. Stop a running instance first
(`systemctl --user stop allsky-web`), or run only the test files you need while iterating.

## Deployment

`deploy/allsky-ng.service` (systemd) and `deploy/nginx-allsky.hoan.uk.conf` are the production
setup on the Pi behind https://allsky.hoan.uk. Deploy by rsync of the code (never `data/`) and
one `systemctl restart allsky-ng`. Details in [docs/SDD.md](docs/SDD.md#deployment).

## Documentation

- [docs/SDD.md](docs/SDD.md): software design document (architecture, capture cycle, data,
  API, sync, retention, time handling, deployment)
