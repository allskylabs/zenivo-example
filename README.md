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

> **Beta software, use at your own risk.** This is a public beta (1.3.0-beta.1). Not every feature has been tested, and some
> paths (colour ZWO/QHY sensors, QHY cooling, mobile layout, long unattended runs) have never run on real hardware. It is provided
> as is, without warranty (see [LICENSE](LICENSE)); whoever installs it accepts all the risks. Live demo: <https://sky.hoan.uk>.

## Privacy in short

- No telemetry, no call home, no auto-update, no remote control, no licence check.
- No default password: the admin account is created on first start with a one-time setup code ([docs/SECURITY.md](docs/SECURITY.md)).
- Guests can watch but not change anything, and see the station position rounded.
- Community sharing, the public ADS-B fallback, contributing your receiver's aircraft to the Zenivo network, the Flightradar24 feed, the sync target and the remote plate solver are off until you switch them on.
- Every outbound connection, third-party browser load and stored file is listed with its source file in
  [docs/TRANSPARENCY.md](docs/TRANSPARENCY.md). A few public-data lookups (satellite orbits, weather balloons, airports) are on by default and listed there.

## Features

- **Capture and exposure**: pull-driven capture (a frame is taken when a viewer, an API call or the
  sync worker asks for one), day/night auto-exposure from the solar elevation, manual exposure and
  gain, exposure/gain lock, dark-frame library with hot-pixel repair, optional star boost.
- **Live view**: WebSocket stream (binary JPEG packets with telemetry and histograms), MJPEG, SSE
  and snapshot endpoints; constellation, star and RA/Dec grid overlay drawn in the browser.
- **Products**: keogram, startrails and timelapse per day, plus a timelapse builder for a date range.
- **Analytics**: cloud cover (clear / scattered / overcast) with history, star count, rain detection
  on the dome, sky events (meteor, plane, satellite, flash, cloud change) by night and by day.
- **Traffic**: ADS-B aircraft (your own receiver first; an optional public-network fallback, adsb.fi then adsb.lol, off by default; an optional, off-by-default contribution of your own reception to the Zenivo aircraft network; and an optional Flightradar24 feed, see the note below), satellite passes from TLEs,
  radiosondes (position sent to SondeHub rounded to 0.5 degrees, with an off switch), an aircraft/satellite log of what crossed the camera view.
  Daytime birds, insects and aircraft are detected locally with their own settings. Every Settings card saves itself on change.
- **Focus assistant**: HFR and contrast measurement on a region of interest, motorised lens control
  and autofocus for supported Raspberry Pi lenses.
- **Plate solving**: local bright-star solver, with a remote blind-solve fallback; sky and obstruction mask.
- **Sky pages**: observer sky view, Flights 3D and Satellites 3D, OBS browser source and dock.
- **Admin login**: one admin password protects every change and the logs; guests can watch and see the station location rounded. First start: read the setup code with `cat data/admin_setup_code.txt` and open `/login`; see [docs/SECURITY.md](docs/SECURITY.md). Lost password: `python3 run_allsky.py --set-admin-password` or `--reset-admin`.
- **Storage**: AVIF frame archive, age and size based retention, extra directories to prune.
- **Sync**: optional push of frames and telemetry to a remote sync server (off unless you pass `--sync-target`).

**Flightradar24 note:** the integration uses Flightradar24's web endpoints, which are not an official public API. It is off by
default; enabling it is the station owner's responsibility under Flightradar24's terms, and a subscription token is the supported
way for account holders.

## Community network

Optional and off by default. Stations that opt in share a signed station card (the fields are listed in [docs/COMMUNITY.md](docs/COMMUNITY.md) and TRANSPARENCY.md section 4)
with each other by gossip, with no central server, and appear on a map. A new station needs one reachable address to start from:
two built-in seed stations are listed in [docs/COMMUNITY.md](docs/COMMUNITY.md) and can be removed or replaced.

## Supported cameras

`--camera auto` tries them in this order:

1. USB 5 MP Sony Starvis IMX335 on a Realtek RTS5869 bridge (`0bda:5869`), with direct sensor
   register control (exposures up to 90 s, 10-bit RAW).
2. Raspberry Pi CSI cameras through picamera2/libcamera (for example IMX708 NoIR).
3. Any V4L2/UVC webcam (only with `--camera v4l2`).
4. A built-in simulator (tests, demos, no hardware).

ZWO ASI cameras (ASI120 to ASI6200) are selected with `--camera zwo`: they need the `zwoasi` Python package
and ZWO's `libASICamera2` SDK. QHYCCD cameras use `--camera qhy` and QHY's `libqhyccd` SDK (no Python package).
See [docs/INSTALL.md](docs/INSTALL.md), then [docs/FIRST_RUN.md](docs/FIRST_RUN.md) to set up the station.

## Requirements

- Python 3.10 or newer.
- Linux (Raspberry Pi OS or a Debian/Ubuntu-based distribution; the V4L2 camera backends are Linux only).
- For a Pi CSI camera: `python3-picamera2` from apt.
- `ffmpeg` for timelapse videos (Zenivo falls back to OpenCV without it).
- About 8 GB of free disk space for the default retention policy.

## Quick start (simulator, no camera)

Python 3.10 or newer on Linux (the camera backends are Linux only):

```bash
git clone https://github.com/hoanb1/zenivo.git && cd zenivo
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python run_allsky.py --camera simulator --port 8080
```

Open `http://127.0.0.1:8080/` (add `--host 0.0.0.0` to open it on the LAN). First run: nobody can change settings until you set the
admin password. Read the one-time setup code with `cat data/admin_setup_code.txt` and enter it at `/login`.
Check the running version with `curl http://127.0.0.1:8080/api/version`.

## Install on a Raspberry Pi or a mini-PC

```bash
./install.sh --camera auto --port 8080      # venv, dependencies and a systemd unit named "zenivo"
```

Full steps (camera access, time zone, nginx, ADS-B feeder, upgrades, uninstall) are in [docs/INSTALL.md](docs/INSTALL.md).

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
| `/community` | Community map of opted-in stations |
| `/obs`, `/obs/dock` | Clean OBS browser source (also `/full`, `/live/full`) and its control dock |
| `/login` | Admin sign-in and first-start setup |

## Documentation

| Document | Contents |
|----------|----------|
| [docs/INSTALL.md](docs/INSTALL.md) | Installation on Raspberry Pi OS and Debian/Ubuntu, systemd, nginx, upgrades, uninstall |
| [docs/FIRST_RUN.md](docs/FIRST_RUN.md) | **After installing**: admin password, time zone, station position (Settings > Station & Optics), lens, pointing check, sky mask, optional services |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | CLI flags, environment variables, `station_config.json`, Settings pages, retention, small-host tuning |
| [docs/API.md](docs/API.md) | REST, WebSocket and SSE endpoint reference |
| [docs/TRANSPARENCY.md](docs/TRANSPARENCY.md) | **Everything Zenivo sends, loads, stores and exposes**: every outbound host, browser third parties, community seeds, files on disk, guest vs admin, how to verify |
| [docs/SECURITY.md](docs/SECURITY.md) | Admin login, what guests can do, setup code, sessions, location rounding |
| [docs/SDD.md](docs/SDD.md) | Software design: architecture, capture cycle, data layout, sync, deployment |
| [docs/BRAND.md](docs/BRAND.md) | Name, logo, colours, typography, voice |
| [deploy/README.md](deploy/README.md) | Which deployment file is for what (examples to adapt) |
| [THIRD_PARTY.md](THIRD_PARTY.md) | Licences and sources of every bundled or loaded third-party component and data set |
| [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) | How to contribute, report a vulnerability, behave |
| [CHANGELOG.md](CHANGELOG.md) | Release notes |

## Development

```bash
python3 -m pytest -q
```

Some hardware tests open a real camera if one is connected. Stop a running instance first, or run
single test files while iterating. See [CONTRIBUTING.md](CONTRIBUTING.md) for the test and privacy rules.

The navigation, brand block and page head of every HTML page are generated from one definition:

```bash
python3 scripts/sync_nav.py          # rewrite src/web/*.html
python3 scripts/sync_nav.py --check  # exit 1 when a page is out of date
```

Layout of the web UI:

- `src/web/index.html`: page shell for `/live`, `/gallery`, `/logs`, `/focus`, `/calibration`, `/settings`.
- `src/web/app/01-core.js` to `10-admin.js`: classic scripts that share one global scope and
  load in numeric order (core and routing, controls and calibration, station settings, live view,
  sky overlay, telemetry, cloud, gallery and ADS-B, community, admin).
- `src/web/focus/*.js` (six files sharing `window.ZenivoFocus`), `src/web/activity.js`: the Focus and Activity Log pages (`src/web/focus.js` is an empty legacy alias).
- `src/web/sky.html`, `flights3d.html`, `satellites3d.html`, `community.html`, `login.html`, `full.html`, `obs_dock.html`: separate pages.
- `src/web/tokens.css`, `src/web/style.css` (a list of `@import`s of `src/web/css/*.css`, in cascade order): design tokens and styles.
- `src/web/brand/`: logo, favicon and manifest.

The Python code is documented in [docs/SDD.md](docs/SDD.md).

## Internal names

Some identifiers keep the original "allsky" name for compatibility with running installs and with
the remote sync server. They are not renamed: logger names `allsky.*`, the browser storage key
`allsky.ui.prefs.v1`, upload paths `/uploads/allsky/...`, the sync API `/api/allsky/ingest/*`,
environment variables `ALLSKY_*` and the script name `run_allsky.py`. Installs use the unit `zenivo` and the folder `~/zenivo`; older installs may keep a symlink `~/allsky-ng` for old scripts.

## Versioning

Zenivo follows [Semantic Versioning](https://semver.org/). The version lives in one place,
`src/version.py`, and is reported by `python3 run_allsky.py --version`, `GET /api/version` and the
UI sidebar. Release notes are in [CHANGELOG.md](CHANGELOG.md).

## License

[MIT](LICENSE). Third-party components and data keep their own licences: see [THIRD_PARTY.md](THIRD_PARTY.md).
