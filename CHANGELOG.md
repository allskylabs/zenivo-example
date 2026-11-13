# Changelog

All notable changes to Zenivo are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-11-13

First release under the Zenivo name.

### Added

- Zenivo name, tagline ("All-sky observatory software"), logo (two rings and one star), colour
  palette, favicon, web app manifest and social image, in `src/web/brand/` and `src/web/tokens.css`.
  Red Vision is a real alternate palette for dark adaptation.
- Single version source `src/version.py`, shown by `python3 run_allsky.py --version`,
  `GET /api/version` and the UI sidebar.
- `install.sh`: creates the virtual environment, installs the dependencies and a systemd unit
  (`--dir`, `--port`, `--host`, `--camera`, `--user`, `--service-name`, `--no-systemd`, `--dry-run`).
- `deploy/zenivo.service` (systemd template), `deploy/nginx-zenivo.example.conf` and
  `deploy/README.md`.
- Documentation: `README.md`, `docs/INSTALL.md`, `docs/CONFIGURATION.md`, `docs/API.md`,
  `docs/BRAND.md`, and an updated `docs/SDD.md`.
- Navigation, brand block and page head generated for every page from one definition:
  `python3 scripts/sync_nav.py [--check]`.

### Changed

- Host services, install folders and the GitHub repository renamed to zenivo (legacy `~/allsky-ng` symlink kept).
- Menu reorganised into Live / Sky / Archive / Camera / Broadcast / Settings. On phones the tab bar
  shows 4 tabs and a More sheet.
- `AllSkyService` (5.6k lines) split into focused mixins: calibration, products, events, traffic,
  station, focus, lens and plate solve. The public API is unchanged.
- The 5.2k-line `app.js` split into 8 ordered scripts in `src/web/app/`. They share one global scope
  and run in the same order as before.
- Outbound HTTPS uses one shared SSL context and a keep-alive connection pool (`src/net.py`)
  instead of a new context per request. This lowers CPU use on a Raspberry Pi.
- The sync worker waits on an event instead of sleeping, so it stops promptly on shutdown.
- Each page loads only the data of its own tab, and `/api/focus` and the sky mask are no longer
  fetched twice.
- `run_allsky.py` has a `--version` flag and a new description.

### Fixed

- Phone tab bar used 7 columns for 5 tabs; toasts now sit compactly above the tab bar; the timelapse
  form no longer overflows on phones; the cloud legend wraps on phones.
- Histogram display is smoothed, so gain-stretched 8-bit frames no longer show a comb pattern.
- Event detection: aircraft strobe trails whose dots never drop below the threshold are classified
  as planes, not meteors; slow, long dot tracks (stars drifting at the sidereal rate) are no longer
  reported as aircraft.

### Known issues

Five tests fail at 1.0.0 (`python3 -m pytest -q`):

- Events limit validation: 2 tests.
- Keogram and startrails dimension mismatch: 2 tests.
- Day-rotation sky-analysis throttle: 1 test.

Other known limits:

- The API has no authentication. Bind to `127.0.0.1` behind a reverse proxy, or use a trusted network.
- The sync client has no persistent queue: frames missed during an outage are not re-sent.
- `--reload` is parsed but not passed to uvicorn.
- The default `--sync-target` is `http://localhost:3005`; pass `--sync-target ""` on installs without a hoan.uk server.
- No licence has been chosen: all rights are reserved by the author until one is added.

## Before 1.0.0

Development started on 2026-10-17 as "allsky-ng" and ran unversioned until the Zenivo rename.
Main steps, newest first:

- **Day and traffic** (November 2026): sky events by day and night; birds, insects and aircraft
  followed by day; rain detection from drops on the dome; a log of aircraft and satellites in the
  camera view; per-pixel ISP shading calibration and AWB lock for the RTS5869 camera; MJPG by day
  and an `ALLSKY_DISABLE_RAW10` switch for hosts where the RAW10 route stalls; exposure before gain
  at dusk and night; RA/Dec grid layer; master dark preview on `/calibration`.
- **Sky pages and plate solving**: `/sky` planetarium view with the camera frame, aircraft,
  satellites and radiosondes; `/flights3d` and `/satellites3d`; station alignment by clicking stars;
  fisheye lens model for the sky overlay; J2000 catalogue precessed to the date of observation.
- **ADS-B and traffic**: local SBS-1 receiver and Flightradar24 feed, aircraft overlay on the live
  view, satellite overlay with SGP4, SondeHub radiosonde tracking, an ADS-B feeder service for
  virtuallab3d.
- **Storage**: frames archived as AVIF with JPEG fallback and a conversion tool.
- **Live review**: frame viewer with an event timeline on the cloud chart; every sky event keeps a
  clean archived frame.
- **Cloud and focus**: cloud cover analysis with history, star-visibility term, focus assistant
  with motorised lens control, plate solving with sky mask, observer-local time handling.
- **Initial import** (2026-10-17): FastAPI service with RTS5869/IMX335 and picamera2 drivers,
  dark calibration, auto-exposure, keogram, startrails and timelapse.
