# Software Design Document: Zenivo

| | |
|---|---|
| Document version | 1.0.0 (2026-11-13), describes Zenivo 1.0.0 |
| Repository | `github.com/hoanb1/zenivo` (private) |
| Production | https://station.example.org (Raspberry Pi 4, `/home/pi/zenivo`, systemd `zenivo`; legacy symlink `/home/pi/allsky-ng` kept) |

## 1. Purpose and scope

Zenivo (all-sky observatory software) is the edge software for one all-sky camera. It captures frames and runs auto-exposure
and dark calibration. It builds the observing products (keogram, startrails, timelapse),
estimates cloud cover, detects sky events, assists focusing, plate-solves the camera pointing,
serves a web UI and optionally pushes frames and telemetry to a configured ingestion server (off by default).

This document covers the edge station only. The hoan.uk server is a separate project. It provides
the `/api/allsky/ingest/*` endpoints and the plate solver.

**Names.** The product is Zenivo (see [BRAND.md](BRAND.md)). Internal identifiers keep the original
name and are not renamed: logger names `allsky.*`, the browser storage key `allsky.ui.prefs.v1`,
upload paths `/uploads/allsky/...`, the sync API `/api/allsky/ingest/*`, the `ALLSKY_*` environment
variables, the class `AllSkyService` and the script name `run_allsky.py`. Live units are named `zenivo` and install folders `~/zenivo` (legacy symlink `~/allsky-ng`). The version
has one source, `src/version.py` (`__version__`), exposed by `--version`, `GET /api/version` and the UI.
Installation, configuration and the API are in [INSTALL.md](INSTALL.md), [CONFIGURATION.md](CONFIGURATION.md)
and [API.md](API.md).

### 1.1 Design constraints

1. **Lightweight first.** The software must run on a Pi 4 with 1.8 GB RAM next to other services
   (systemd `MemoryMax=700M`, `Nice=5`).
   - Heavy analysis is sampled (cloud/star stats every 300 s on a 960 px copy) and never runs in daylight.
   - OpenCV and BLAS use 1 thread.
   - malloc is tuned to reuse frame buffers.
   - Timelapse re-encodes are rare and use the cheapest available encoder.
2. **Hardware-agnostic.** The software works with an RTS5869/IMX335 USB camera, a Pi CSI camera, any
   UVC webcam, or a simulator.
3. **Pull-driven capture.** There is no free-running capture loop. Frames are taken when a consumer
   asks: WebSocket/MJPEG/SSE viewers, REST snapshot or telemetry calls, or the sync worker.
   Captures are cached for `max(min_capture_interval_s, 0.9 × exposure)`.
4. **Local time.** All recorded times use the system's local time zone (section 8).

## 2. Architecture

```
            ┌────────────────────── run_allsky.py (one process) ───────────────────────┐
 camera ──► │ src/hardware  ──►  AllSkyService (src/api/service.py, singleton)          │
            │  driver factory     capture_frame → dark → transform → AE → star boost   │
            │                     → live image → archive → products → sky events       │
            │                     get_telemetry → cloud/star sampling                  │
            │ src/calibration   dark library, auto-exposure, plate solvers            │
            │ src/pipeline      keogram, startrails, timelapse, star boost            │
            │ src/analytics     cloud cover/history, sky events, traffic (ADS-B, FR24, │
            │                   satellites, radiosondes), day objects, focus          │
            │ src/storage       AVIF frame codec, retention                           │
            │ src/api           FastAPI routes, WS/MJPEG/SSE streams, page shell       │
            │ src/web           UI (index.html + app/01..08-*.js, focus.js, ...)     │
            │ _sync_worker ───► SyncClient ──► hoan.uk /api/allsky/ingest/*            │
            └───────────────────────────────────────────────────────────────────────────┘
 browser ◄── nginx :80 ◄── cloudflared (station.example.org)      data/ (frames, products, logs, config)
```

### 2.1 Modules

| Package | Responsibility |
|---|---|
| `src/hardware` | Camera backends behind `CameraDriverInterface` (`driver_interface.py`): `driver.py` (RTS5869 + factory), `xu.py` (UVC extension unit), `registers.py` (IMX335 timing math), `v4l2_mmap.py` (mmap capture, RAW10 demosaic, ISP shading correction), `picamera.py`, `uvc_generic.py`, `simulator.py`, `constants.py` |
| `src/calibration` | `dark.py` (master darks, hot-pixel maps), `auto_exposure.py` (solar elevation, day/night AE), `bright_star_solver.py` (local plate solve), `station_solver.py` (remote blind solve), `stacking.py` (unused by the service) |
| `src/pipeline` | `keogram.py`, `startrails.py`, `timelapse.py` (encode + banner), `star_boost.py`, `hot_pixels.py`, `highlights.py`, `overlay.py` (not used live; the sky overlay is drawn client-side) |
| `src/analytics` | `cloud_cover.py`, `cloud_history.py`, `cloud_backfill.py` (CLI), `stars_clouds.py`, `star_clarity.py`, `sky_events.py`, `day_objects.py` (daytime birds, insects, aircraft, rain), `adsb.py`, `fr24.py`, `aircraft_info.py`, `airports.py`, `satellites.py` (SGP4 from TLEs), `radiosonde.py`, `traffic_log.py`, `focus.py`, `focus_tracker.py`, `meteors.py`, `meteor.py` (legacy, tests only) |
| `src/api` | `main.py` (app factory, page shell, static mounts), `app.py` (re-export of the app), `routes.py` (REST), `stream.py` (WS/MJPEG/SSE), `schemas.py`, `histogram.py`, `service_consts.py`, `service.py` (`AllSkyService` core: state, capture cycle, telemetry) and the mixins that add its methods: `calibration_mixin.py`, `products_mixin.py`, `events_mixin.py`, `traffic_mixin.py`, `station_mixin.py`, `focus_mixin.py`, `lens_mixin.py`, `plate_solve_mixin.py`. The public API of the service is unchanged by the split |
| `src/net.py` | Shared outbound HTTPS helper: one SSL context and a keep-alive urllib3 pool reused by every outbound request (certificate verification stays on) |
| `src/storage` | `frame_codec.py` (AVIF frame read/write, legacy JPEG still readable), `retention.py` (age and size cleanup) |
| `src/version.py` | Product name and version, the single source (`__version__`, `PRODUCT`) |
| `src/sync` | `client.py` (used in production), `queue.py` and `daemon.py` (SQLite queue and circuit breaker; tested but not wired up) |
| `src/activity.py` | User-facing activity log (`data/logs/activity.jsonl`) |
| `src/web` | Static UI. `index.html` is the shell of `/live`, `/gallery`, `/logs`, `/focus`, `/calibration`, `/settings`; `app/01-core.js` to `08-gallery-adsb.js` are classic scripts with one shared global scope, loaded in numeric order; `focus.js`, `activity.js`, `sky_catalog.js`; separate pages `sky.html`, `flights3d.html`, `satellites3d.html`, `obs_dock.html`, `full.html`; `tokens.css` and `style.css`; `brand/`; `sky/` (built skyview bundle) and `skydata/` (star tiles, catalogues). Navigation, brand block and page head are generated from one definition by `scripts/sync_nav.py` (`--check` verifies) |
| `tools/` | `adsb_feeder.py`, `isp_calibrate.py`, `convert_frames_avif.py`, `skyview/` (TypeScript source of the planetarium bundle) |

## 3. Startup

`run_allsky.py` runs these steps in order:

1. Set `ALLSKY_RUNNER=1`, tune malloc (`M_MMAP_THRESHOLD` 256 MB, `M_TRIM_THRESHOLD` 512 MB,
   `M_ARENA_MAX` 1) and set the OpenCV/BLAS thread count to 1 (`ALLSKY_CV_THREADS`).
2. Set up logging: `data/logs/allsky.log` (5 MB × 3) and the activity log.
3. Open the camera through `get_camera_driver`. If the sun is at or above −2°, start at 0.6 ms / 0 dB.
4. Create the `AllSkyService` singleton.
5. For a real camera, start the camera watchdog. If the service has fallen back to the
   simulator, the watchdog retries the real camera every 30 s and hot-swaps it in.
6. Restore saved manual camera settings, build the FastAPI app, apply the `--prune-dir` specs and
   start `_sync_worker` if `--sync-target` is set.
7. Run `uvicorn` as a single process (proxy headers trusted). On exit, stop sync and close the
   camera.

CLI flags: see [CONFIGURATION.md](CONFIGURATION.md#command-line-flags). Environment variables:

| Variable | Effect |
|---|---|
| `ALLSKY_CAMERA` | Default for `--camera` |
| `ALLSKY_CV_THREADS` | OpenCV thread count |
| `ALLSKY_SIMULATOR` | Prefer the simulator |
| `ALLSKY_RUNNER` | Stops `src/api/main.py` from building its module-level app |
| `ALLSKY_DISABLE_RAW10`, `ALLSKY_ISP_WB_K`, `ALLSKY_ISP_CAL`, `ALLSKY_ISP_CAL_OFF` | RTS5869 workarounds, see [CONFIGURATION.md](CONFIGURATION.md#environment-variables) |
| `PYTEST_CURRENT_TEST` | Test mode: no background threads, archiving or live AE |

## 4. Camera backends

`get_camera_driver` tries the candidates in order and uses the first one that opens. If none
opens, it falls back to the simulator (unless told not to).

1. **RTS5869 + IMX335** (`0bda:5869`)
   - Control goes through UVC extension unit 4 (selector 0x0A for commands, 0x0B for data).
     Unlocking sets `XDATA[0xC78A]=1`, and a RAM patch raises the exposure ceiling to 90 s.
   - Sensor registers are written under REGHOLD: VMAX, HMAX, SHR0, PGC (0.3 dB/LSB, 0 to 72 dB).
   - **Day:** MJPG through the ISP, downscaled during decode.
   - **Night or exposure ≥ 0.4 s:** RAW10 Bayer bypass (black level 52), demosaiced with
     optional 2×2 or 4×4 binning and gray-world white balance.
   - The stream stays on (persistent STREAMON) and is reset after errors.
   - **Known host limit (minipc `<minipc-host>`, 2026-11-08):** writing the Bayer route (`XDATA[0x8000..0x8007]`) stops
     the stream on this unit (no frame, even after STREAMOFF/ON; only a USB reset recovers). `ALLSKY_DISABLE_RAW10=1`
     keeps MJPG day and night there; the night RAW10 path is therefore not available on that host (root cause open).
   - **MJPG colour:** `ALLSKY_ISP_WB_K=<kelvin>` locks the camera's own AWB (it drifts magenta under foliage).
     The ISP's radial purple pedestal can be removed per pixel with `tools/isp_calibrate.py` (dark + flat capture
     -> `data/calibration/isp_shading.npz`, applied in `apply_isp_shading`); without the file nothing is applied.
2. **Pi CSI** (picamera2/libcamera)
   - Full field of view, 2×2-binned mode. AE is off.
   - Range 0 to 24 dB, 0.1 ms to 60 s, at most 5 fps (10 fps in focus mode).
   - Supports motorised lenses (IMX708/IMX519).
3. **Generic UVC** (OpenCV)
   - The hardware maximum exposure is 0.5 s. Longer exposures, up to 60 s, are made by summing
     sub-frames.
4. **Simulator**
   - Emulates XDATA, the IMX335 registers and a noise model with hot pixels.
   - Used in CI. It is also the runtime fallback when a real camera fails twice in a row.

## 5. Capture cycle

`AllSkyService.capture_frame()` does the following for each new frame:

1. While dark calibration runs, return the cached frame.
2. Every 60 s, compute the solar elevation. Night mode (RAW10) is on when the sun is below −6°.
3. Capture. On failure: close, reopen and retry, then fall back to the simulator.
4. **Dark subtraction** (exposure ≥ 0.4 s)
   - Use the closest master dark, minimising |Δexposure| + |Δgain|/12.
   - RAW10 frames: subtract the master dark and repair hot pixels before demosaicing.
   - Otherwise: subtract in 8-bit image space.
   - An LRU cache holds 4 darks.
5. Image transform (mirror/flip/rotate 180°), then downscale to the output resolution.
   - **When**: right after the camera frame is captured, dark-subtracted and hot-pixel repaired (steps 3-4 stay in
     native sensor orientation, because the dark library and hot-pixel tracker are native), and before everything
     else: auto-exposure, star boost, JPEG/WebP, archive, timelapse, event detection and plate solving all see the
     transformed ("display") frame.
   - Night (RAW10) frames use `rotate_deg` and the flips. Day (ISP) frames add `rotate_isp_180` on top.
   - **Coordinate spaces.** Solved optics (optical centre, roll, focal length, distortion) are stored in *native
     sensor* coordinates. The sky overlay projects stars in native coordinates and applies the current transform
     itself (`toImgX/toImgY` in `src/web/app/`), so changing the orientation needs no new solve. The plate solver therefore
     un-transforms its input frames first (`_solve_bright_star_pattern`). The sky mask polygons are drawn on the
     displayed image, so they are stored in *display* fractions and are turned with the image whenever
     the orientation changes (`set_image_transform`). Anything in display space (archived frames, event
     boxes) keeps the orientation it was captured with.
6. **Auto-exposure** (auto mode)
   - The sky profile (day, civil, nautical, night) comes from the solar elevation, with 1° hysteresis.
   - Target luma is 142 by day and 85 at night, with deadbands, clipping guards and a fast drop
     at sunrise.
   - In manual mode, a sunrise guard switches back to auto when the frame saturates after dawn.
7. **Star boost** (optional, sun < −6°): sum-bins the display copy only. Analysis uses the raw frame.
8. Write `data/live/latest.webp` at most every 120 s.
9. Unless calibrating or focusing: archive the frame, update the products, track focus and detect
   sky events.

**Archive and products.** The first 15 frames of a day are archived every 2.5 s, then one every
`archive_interval_s` (30 s).
- Each archived frame is a 1296 px AVIF (quality 62, written by `src/storage/frame_codec.py`) in
  `frames/<YYYY-MM-DD>/frame_YYYYmmdd_HHMMSS_mmm.avif`, plus one line in `telemetry.jsonl`.
  Older `frame_*.jpg` files stay readable.
- Each day keeps at most 2880 frames.
- The keogram (central meridian slice) and startrails (max-pixel blend) update with every
  archived frame and are saved as `<date>.jpg` and `latest.jpg`.
- The timelapse (last 600 frames, 12 fps) re-encodes every 1800 s, or every 8 s while the day
  has 20 frames or fewer.
  - Encoder order: `h264_v4l2m2m` (Pi hardware), then libx264 veryfast, then OpenCV.
- At local midnight the day rolls over: the products reset and the activity log records the
  closed day.
- On restart, today's products are restored from their dated JPEGs. The archived frames are
  re-decoded only when a dated product is missing.

**Telemetry sampling.** `get_telemetry()` runs the cloud and star analysis at most every
`analysis_interval_s` (300 s) on a 960 px copy. It appends to the cloud history and feeds the
cloud events.

### 5.1 Analytics

- **Cloud cover:** a structure analysis (detrend and Otsu split) combined with per-cell star
  counts against a learned clear-sky reference at night, and normalised sky radiance by day.
  - Status: clear below 25 %, scattered below 70 %, otherwise overcast.
  - `python -m src.analytics.cloud_backfill` fills the history for archived days.
- **Sky events** (sun < −8°): the frame is differenced on a 640 px grayscale copy, then streaks
  are found with a Hough transform. Classes:
  - meteor: a solid streak in one frame
  - plane: a dashed streak, or one that continues collinearly
  - satellite: a faint streak that continues collinearly
  - flash: a brightness jump at unchanged exposure
  - clouds: a cloud-state change, with hysteresis

  Events are saved under `uploads/allsky/events/<date>/`.
- **Focus assistant** (`/focus`): measures HFR on stars or lights, or contrast, on a native ROI.
  - AE and archiving pause during a focus session.
  - Motorised lenses support a libcamera AF scan or a coarse-then-fine sweep.
  - At night, `FocusTracker` re-probes ±0.04 D every 20 min and compensates for temperature.
- **Plate solving:** about 6 long exposures over 90 s or more.
  - The local `bright_star_solver` keeps only points that drift at the sidereal rate, then
    matches them against the catalog and the planets (TRIAD + Kabsch).
  - If that fails, the frames go to the hoan.uk blind solver.
  - A failed solve never overwrites the optics. A sky/obstruction polygon mask limits where stars
    are searched.

## 6. Interfaces

### 6.1 Web UI

One page shell (`index.html`) is served at `/`, `/live`, `/gallery`, `/settings`, `/calibration`,
`/logs` and `/focus`. The live viewport is pre-sized to the frame's aspect ratio. The eight scripts in
`src/web/app/` (core and routing, controls and calibration, station settings, live view, client-side
sky overlay, telemetry, cloud chart, gallery/events/ADS-B) share one global scope and run in numeric
order. Each page loads only the data of its own tab. `focus.js` and `activity.js` cover their own
pages. `sky_catalog.js` holds the constellations, stars and Messier objects. `/sky`, `/flights3d`,
`/satellites3d`, `/obs` (alias `/full`) and `/obs/dock` are separate pages. The UI keeps viewer
preferences in `localStorage` under the key `allsky.ui.prefs.v1`. The Red Vision theme
(`data-theme="red"`) is an alternate token palette, see [BRAND.md](BRAND.md).

### 6.2 Streams

- **`/ws/live?binary=1`:** ASB1 packets. Each one is a 92-byte little-endian header (exposure,
  gain, fps, star count, cloud %, temperature, registers, histogram stats, calibration state),
  then 4×256 `uint32` histograms (R, G, B, Y), then the JPEG.
  - The push period is clamp(0.95 × exposure, 0.35, 2) s.
  - Client commands use ASC1 packets.
  - Without `binary=1`, the stream speaks legacy JSON.
- **`/api/stream/mjpeg`:** 0.5–30 fps, downscaled.
- **`/api/stream/sse`:** telemetry.

### 6.3 REST (selection)

| Group | Endpoints |
|---|---|
| Status | `GET /api/status`, `GET /api/telemetry`, `/api/live/snapshot.{jpg,webp}` |
| Camera | `POST /api/camera/{exposure,gain,mode,auto}`, `GET/POST /api/camera/{orientation,white-balance,resolution,star-boost,performance}` |
| Sky | `GET /api/sky/clouds/history`, `GET/POST /api/sky/clouds/config` |
| Calibration | `GET /api/calibration/status`, `POST /api/calibration/{auto-library,master-dark}` |
| Station | `GET /api/station/config`, `POST /api/station/{location,settings,auto-locate,plate-solve/start}`, `GET /api/station/plate-solve/status`, `GET/POST /api/station/sky-mask` |
| Focus | `/api/focus`, `/api/focus/{start,stop,finish,config,auto-roi,frame,lens,lens/autofocus,...}` |
| Products | `GET /api/gallery`, `POST /api/timelapse/generate`, `POST /api/timelapse/build`, `GET /api/events`, `GET/POST /api/events/config`, `/api/frames/*`, `/uploads/...` |
| Traffic | `GET /api/adsb/aircraft`, `/api/satellites`, `/api/radiosondes`, `/api/traffic`, `/api/day-objects`, `GET/POST /api/adsb` |
| Meta, OBS | `GET /api/version`, `GET/POST /api/obs/overlay`, `POST /api/obs/reload` |

The full list is in [API.md](API.md).
| Ops | `GET /api/storage`, `POST /api/storage/cleanup`, `PUT /api/storage/policy`, `GET /api/logs`, `/api/logs/download` |

Access control is a single-admin login (docs/SECURITY.md): default deny for every change, guests may watch. Bind to 127.0.0.1 behind nginx
and cloudflared (or a trusted network).

### 6.4 Sync to hoan.uk

`_sync_worker` runs every `--sync-interval` (60 s). Each round it captures a frame, downscales it
to `--sync-max-width` (1600 px, JPEG q82) and sends:

- `POST /api/allsky/ingest/frame`: multipart `image`, with headers `X-Exposure-Sec` and `X-Gain-Db`
- `POST /api/allsky/ingest/telemetry`: JSON

The client sends the header `X-Device-Id: zenivo-<hostname>` (override with `ZENIVO_DEVICE_ID`). There is no retry: the next round
simply tries again. Failures and recoveries are written to the activity log.

`SyncQueue` (SQLite WAL, priorities, exponential backoff with jitter, retry times in local time)
and `SyncDaemon` (circuit breaker) exist and are tested, but are not used in production.

## 7. Data and configuration

Everything is under `data/` (git-ignored). Dates are local `YYYY-MM-DD`.

```
data/
├── station_config.json            station settings (see below)
├── frames/<date>/frame_*.avif     archived frames, 1296 px (legacy frame_*.jpg still read)
│               telemetry.jsonl    {filename, timestamp, exposure_s, gain_db, star_count, cloud_cover_pct}
├── live/latest.webp               refreshed every ≤120 s
├── logs/allsky.log                Python log (5 MB × 3)
│        activity.jsonl            {ts, level, category, message, details} (2 MB × 5)
│        cloud_history.jsonl       {t (epoch), cc, m, conf, stars, sun}
├── calibration/cloud_reference.json
│              darks/master_dark_raw10_exp*_gain*.npy, hot_pixels_*.npy
├── traffic/<date>.jsonl           aircraft and satellites seen in the camera view (retention-limited)
├── day_objects/                   daytime object log (14 days / 50 MB by default)
├── cache/                         TLEs (sat_tle.json), aircraft and airline databases, airports, ADS-B trails
└── uploads/allsky/                served at /uploads
    ├── keograms/<date>.jpg, latest.jpg
    ├── startrails/<date>.jpg, latest.jpg
    ├── timelapses/<date>.mp4, latest.mp4
    └── events/<date>/events.jsonl, <type>_YYYYmmdd_HHMMSS_NNN.jpg, *_sky.jpg
```

`station_config.json` is merged over defaults at the top level only. Keys:

| Key | Contents |
|---|---|
| Location | `latitude_deg`, `longitude_deg`, `elevation_m`, `location_name` |
| `camera_sensor` | Sensor specification |
| `optics` | Projection, focal length, FOV, optical centre, distortion |
| `orientation` | Azimuth, roll, CD matrix |
| `image_transform` | Flip/rotate |
| `output_resolution` | Output frame size |
| `bright_star_solution` | Last local plate solve. A hand edit of azimuth / altitude / tilt / north angle in Settings also rewrites its `pointing_az_deg`, `pointing_alt_deg`, `roll_deg` (and sets `manual_override`), because `/flights3d`, `/sky`, satellites and ADS-B overlays read the pointing from it; run a star solve to refine |
| `camera_settings` | Saved manual mode |
| `white_balance` | White balance mode and gains |
| `sky_roi`, `sky_mask` | Sky region and polygons |
| `star_boost` | Star boost on/off and bin size |
| `event_detection` | Per-type switches |
| `performance` | `min_capture_interval_s` 2, `analysis_max_width` 960, `sky_analysis_enabled` 1, `analysis_interval_s` 300, `stream_max_width` 960, `stream_jpeg_quality` 75 |
| `storage` | Retention policy |
| `focus_record`, `focus_history`, `lens_focus` | Focus results, history and lens state |
| `adsb`, `traffic_log`, `day_objects`, `obs_overlay`, `google_maps_key` | ADS-B/Flightradar24 settings, log retention, OBS overlay, map key |

All keys and defaults are listed in [CONFIGURATION.md](CONFIGURATION.md#station-configuration-file).

### 7.1 Retention

The retention job runs 30 s after start, then hourly. Default policy:

| Key | Default |
|---|---|
| `frames_keep_days` | 7 |
| `products_keep_days` | 60 |
| `max_data_gb` | 8 |
| `min_free_gb` | 3 |
| `archive_interval_s` | 30 |
| `max_events_per_day` | 1000 |
| `timelapse_refresh_s` | 1800 |

1. Age rules delete frame days, dated products and event days older than their limits.
2. Size rules then delete the oldest days, but never today's data or `latest.*`, until the data
   directory is under `max_data_gb` and free space is above `min_free_gb`.
3. `--prune-dir PATH:DAYS` adds external directories. These come from the CLI only and are not saved.

## 8. Time handling

| What | Rule |
|---|---|
| Recorded timestamps (activity log, telemetry, status, events, plate solve, focus, retention reports) | Local ISO 8601 **with offset**, from `datetime.now().astimezone()` |
| Day buckets (frame folders, frame file names, event folders, dated products, retention "today", day rollover) | Local date |
| Epoch values (`cloud_history.t`, event `ts`) | Unix seconds (zone-free) |
| Overlays | Timelapse banner `YYYY-MM-DD HH:MM:SS <zone>`; keogram ruler in local `HH:MM` |
| Sync queue (SQLite) | `datetime('now', 'localtime')` on both the write and the compare side |
| Astronomy (solar elevation, LST, star positions) | Uses aware datetimes, converted to UTC internally |
| Web UI | ISO strings are parsed with `Date` and shown in browser-local time; the clock shows `UTC±H` |

The time zone is the operating system's (`timedatectl`). Both the Pi and the development laptop
use UTC+7.

**Migration note:** before 2026-10-18 06:34 +07, folders and file names used UTC dates. Old
telemetry still carries its `+00:00` offset, so code that needs a frame time reads it from
telemetry (e.g. `cloud_backfill`) rather than from the file name. The 2026-10-17 folder therefore
holds a few hours that are 2026-10-18 in local time.

## 9. Deployment

| Item | Value |
|---|---|
| Host | Raspberry Pi 4 (`<pi-host>`), Wi-Fi. An optional ingestion/plate-solver service may run on the same Pi on :3000 |
| Service | `deploy/zenivo-pi.service` (live unit `zenivo`; new installs use the `deploy/zenivo.service` template through `install.sh`): user `pi`, group `video`, `.venv/bin/python run_allsky.py --host 127.0.0.1 --port 8095 --camera auto --sync-target http://127.0.0.1:3000 --sync-interval 60 --log-level warning --no-access-log --prune-dir …archive:3 --prune-dir …events:90`, `Restart=always`, `Nice=5`, `MemoryMax=700M` |
| Web | `deploy/nginx-allsky.hoan.uk.conf` (the author's example): :80 → 127.0.0.1:8095, WebSocket upgrade, `proxy_buffering off`, 3600 s timeouts. cloudflared maps station.example.org → :80 and is never restarted for allsky |
| Development instance | Service `zenivo` on port 8080 from the repo checkout |
| New installs | `./install.sh` creates `.venv`, installs the dependencies and a `zenivo` unit; see [INSTALL.md](INSTALL.md) |
| Current instance (2026-11-08) | minipc `<minipc-host>` (LAN) / `minipc.example.org` (IPv6 SSH), user `<user>`, `/home/<user>/zenivo`, system service `zenivo` on 127.0.0.1:8080 (`ALLSKY_DISABLE_RAW10=1`, `ALLSKY_ISP_WB_K=5000`, output appended to `data/allsky.log`), no sync target; cloudflared ingress `station.example.org -> 127.0.0.1:8080` (`/etc/cloudflared/config.yml`) |

To deploy:

```bash
rsync -ac --exclude data/ --exclude __pycache__ --exclude '*.pyc' ./src ./run_allsky.py pi@<pi-host>:/home/pi/zenivo/
ssh pi@<pi-host> 'sudo systemctl restart zenivo'
```

Restart the service once per deploy, and run long installs on the Pi with `nohup`.

## 10. Testing

`python3 -m pytest -q` runs the full suite (591 tests collected at 1.0.0) under `tests/unit`,
`tests/e2e` and `tests/integration`. Five tests are known to fail at 1.0.0, see [CHANGELOG.md](../CHANGELOG.md). Synthetic fixtures live in `tests/fixtures` and `conftest.py`.

Caveat: `test_hardware.py::test_live_camera_capture_if_available` opens a real camera. Modules
that import `src.api.main` build a service at collection time, which detects the real camera and
starts threads against `data/`. Stop the `zenivo` service before a full run, and run
single test files while iterating.

## 11. Known gaps

- The edge API has a single-admin login (see docs/SECURITY.md); a visitor who is not signed in can only watch.
- The sync client does not use the persistent queue, so frames missed during an outage are not
  re-sent.
- `src/pipeline/overlay.py`, `src/calibration/stacking.py` and `src/analytics/meteor.py` are not
  used by the live service.
- The `--reload` flag is parsed but not passed to uvicorn.
- The `--min-capture-interval` help text says the default is 1.0 s, but the code default is 2.0 s.
- `--sync-target` defaults to empty (sync off); the sync worker only starts when a URL is given.
