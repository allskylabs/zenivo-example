<p align="center">
  <img src="src/web/brand/logo-horizontal.svg" alt="Zenivo" width="320">
</p>

# Zenivo

> All-sky observatory software

Zenivo is the software for one all-sky camera. It captures frames, calibrates dark frames, runs
day/night auto-exposure, serves a live web UI, builds keograms, startrails and timelapses,
estimates cloud cover, detects sky events, assists focusing and plate-solves the camera pointing.
It runs on a Raspberry Pi 4 (1.8 GB RAM) as well as on a laptop or mini-PC. Low CPU and memory use
is the main design constraint: heavy analysis is sampled, never done per frame.

## Features

- **Capture and exposure**: pull-driven capture (a frame is taken when a viewer, an API call or the
  sync worker asks for one), day/night auto-exposure from the solar elevation, manual exposure and
  gain, exposure/gain lock, dark-frame library with hot-pixel repair, optional star boost.
- **Live view**: WebSocket stream (binary JPEG packets with telemetry and histograms), MJPEG, SSE
  and snapshot endpoints; constellation, star and RA/Dec grid overlay drawn in the browser.
- **Products**: keogram, startrails and timelapse per day, plus a timelapse builder for a date range.
- **Analytics**: cloud cover (clear / scattered / overcast) with history, star count, rain detection
  on the dome, sky events (meteor, plane, satellite, flash, cloud change) by night and by day.
- **Traffic**: ADS-B aircraft (local receiver and Flightradar24 feed), satellite passes from TLEs,
  radiosondes, an aircraft/satellite log of what crossed the camera view.
- **Focus assistant**: HFR and contrast measurement on a region of interest, motorised lens control
  and autofocus for supported Raspberry Pi lenses.
- **Plate solving**: local bright-star solver, with a remote blind-solve fallback; sky and obstruction mask.
- **Sky pages**: observer sky view, Flights 3D and Satellites 3D, OBS browser source and dock.
- **Community** (opt-in, off by default): share a signed station card with other Zenivo stations by gossip, no central server, and see them on a map; see [docs/COMMUNITY.md](docs/COMMUNITY.md).
- **Admin login**: one admin password protects every change and the logs; guests can watch and see the station location rounded. First start: read the setup code with `cat data/admin_setup_code.txt` and open `/login`; see [docs/SECURITY.md](docs/SECURITY.md).
- **Storage**: AVIF frame archive, age and size based retention, extra directories to prune.
- **Sync**: optional push of frames and telemetry to a hoan.uk server.

## Supported cameras

`--camera auto` tries them in this order:

1. USB 5 MP Sony Starvis IMX335 on a Realtek RTS5869 bridge (`0bda:5869`), with direct sensor
   register control (exposures up to 90 s, 10-bit RAW).
2. Raspberry Pi CSI cameras through picamera2/libcamera (for example IMX708 NoIR).
3. Any V4L2/UVC webcam.
4. A built-in simulator (tests, demos, no hardware).

## Requirements

- Python 3.10 or newer.
- Linux (Raspberry Pi OS or a Debian/Ubuntu-based distribution; the V4L2 camera backends are Linux only).
- For a Pi CSI camera: `python3-picamera2` from apt.
- `ffmpeg` for timelapse videos (Zenivo falls back to OpenCV without it).
- About 8 GB of free disk space for the default retention policy.

## Quick start

With the simulator (no camera needed):

```bash
git clone https://github.com/hoanb1/zenivo.git zenivo
cd zenivo
./install.sh --no-systemd --camera simulator
.venv/bin/python run_allsky.py --camera simulator --port 8080
```

With a real camera, installed as a systemd service:

```bash
./install.sh --camera auto --port 8080
```

Open `http://<host>:8080/`. First run: nobody can change settings until you set the admin password. Read the one-time
setup code with `cat data/admin_setup_code.txt` and enter it at `/login`. Full steps are in [docs/INSTALL.md](docs/INSTALL.md).
Check the running version with `curl http://localhost:8080/api/version`.

## Pages

| Path | Purpose |
|------|---------|
| `/live` | Live view with overlays, exposure and gain controls, histogram, cloud chart |
| `/gallery` | Keograms, startrails, timelapses and the sky event archive |
| `/logs` | Activity log |
| `/focus` | Focus assistant |
| `/calibration` | Master dark library and previews |
| `/settings` | Image Output, Detection, Station & Optics, Sky Mask, Integrations |
| `/sky` | Observer sky view (WebGL planetarium with aircraft, satellites, events) |
| `/flights3d`, `/satellites3d` | 3D views of aircraft and satellites around the station |
| `/obs`, `/obs/dock` | Clean OBS browser source and its control dock |

## Documentation

| Document | Contents |
|----------|----------|
| [docs/INSTALL.md](docs/INSTALL.md) | Installation on Raspberry Pi OS and Debian/Ubuntu, systemd, nginx, upgrades, uninstall |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | CLI flags, environment variables, `station_config.json`, Settings pages, retention, small-host tuning |
| [docs/API.md](docs/API.md) | REST, WebSocket and SSE endpoint reference |
| [docs/SECURITY.md](docs/SECURITY.md) | Admin login, what guests can do, setup code, sessions, location rounding |
| [docs/SDD.md](docs/SDD.md) | Software design: architecture, capture cycle, data layout, sync, deployment |
| [docs/BRAND.md](docs/BRAND.md) | Name, logo, colours, typography, voice |
| [deploy/README.md](deploy/README.md) | Which deployment file is for what |
| [CHANGELOG.md](CHANGELOG.md) | Release notes |

## Development

```bash
python3 -m pytest -q
```

Some hardware tests open a real camera if one is connected. Stop a running instance first, or run
single test files while iterating. A few tests fail at 1.0.0; see "Known issues" in
[CHANGELOG.md](CHANGELOG.md).

The navigation, brand block and page head of every HTML page are generated from one definition:

```bash
python3 scripts/sync_nav.py          # rewrite src/web/*.html
python3 scripts/sync_nav.py --check  # exit 1 when a page is out of date
```

Layout of the web UI:

- `src/web/index.html`: page shell for `/live`, `/gallery`, `/logs`, `/focus`, `/calibration`, `/settings`.
- `src/web/app/01-core.js` to `08-gallery-adsb.js`: classic scripts that share one global scope and
  load in numeric order (core and routing, controls and calibration, station settings, live view,
  sky overlay, telemetry, cloud, gallery and ADS-B).
- `src/web/focus.js`, `src/web/activity.js`: the Focus and Activity Log pages.
- `src/web/sky.html`, `flights3d.html`, `satellites3d.html`, `obs_dock.html`: separate pages.
- `src/web/tokens.css`, `src/web/style.css`: design tokens and styles.
- `src/web/brand/`: logo, favicon and manifest.

The Python code is documented in [docs/SDD.md](docs/SDD.md).

## Internal names

Some identifiers keep the original "allsky" name for compatibility with running installs and with
the hoan.uk server. They are not renamed: logger names `allsky.*`, the browser storage key
`allsky.ui.prefs.v1`, upload paths `/uploads/allsky/...`, the sync API `/api/allsky/ingest/*`,
environment variables `ALLSKY_*` and the script name `run_allsky.py`. Live hosts use the unit `zenivo` and the folder `~/zenivo`; a legacy symlink `~/allsky-ng` is kept for old scripts.

## Versioning

Zenivo follows [Semantic Versioning](https://semver.org/). The version lives in one place,
`src/version.py`, and is reported by `python3 run_allsky.py --version`, `GET /api/version` and the
UI sidebar. Release notes are in [CHANGELOG.md](CHANGELOG.md).

## License

Not yet specified; all rights reserved by the author until one is added.
