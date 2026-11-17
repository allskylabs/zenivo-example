# Configuring Zenivo

Zenivo is configured in four places:

1. [Command-line flags](#command-line-flags) of `run_allsky.py`, set in the systemd unit.
2. [Environment variables](#environment-variables) for hardware workarounds and thread limits.
3. [`data/station_config.json`](#station-configuration-file), written by the Settings pages.
4. The [Settings pages](#settings-pages) in the web UI, which are the normal way to change 3.

Run `python3 run_allsky.py --help` for the flags of the version you have installed.

## Command-line flags

| Flag | Default | Description |
|------|---------|-------------|
| `--version` | | Print `Zenivo <version>` and exit |
| `--host` | `127.0.0.1` | Network interface to bind (this machine only). Use `0.0.0.0` to open it on the LAN; behind a reverse proxy keep `127.0.0.1` |
| `--port` | `8080` | TCP port of the web UI and API |
| `--device` | auto-detected RTS5869, else `/dev/video0` | V4L2 camera device node |
| `--camera` | `auto` (or `$ALLSKY_CAMERA`) | Camera backend (when left at `auto`, the camera chosen under Settings > Camera is used): `auto`, `rts5869`, `picamera`, `zwo`, `qhy`, `v4l2`, `simulator`. `auto` tries RTS5869, then a Pi CSI camera, then a USB/V4L2 webcam, then the simulator |
| `--camera-index` | `0` | Camera number for `--camera picamera` (libcamera) or `--camera zwo` / `qhy` (camera index) |
| `--resolution` | camera default | Capture size `WIDTHxHEIGHT`. Defaults: IMX335 2592x1944, Pi camera full-FOV binned mode, webcam maximum |
| `--simulator` | off | Force the simulated IMX335 controller (tests, CI, demos): always a night sky with stars at any clock time, auto exposure in night mode. Sets `ALLSKY_SIMULATOR=1` |
| `--exposure` | `0.2` | Starting exposure in seconds before auto-exposure takes over. If the exposure is 0.25 s or less, the gain is the default 12 dB and the saved station position puts the sun at or above -2 degrees, the start is 0.6 ms / 0 dB |
| `--gain` | `12.0` | Starting gain in dB before auto-exposure takes over |
| `--log-level` | `info` | `debug`, `info`, `warning`, `error` |
| `--sync-target` | empty (sync OFF) | URL of an optional ingestion server that receives frames and telemetry. Empty means nothing is uploaded. When set, one info line at start names the target. The device id sent is `$ZENIVO_DEVICE_ID` or `zenivo-<hostname>` |
| `--sync-interval` | `60` | Seconds between pushes (minimum 0.25) |
| `--sync-max-width` | `1600` | Downscale pushed frames to at most this width in pixels |
| `--min-capture-interval` | the saved setting (2.0) | Minimum seconds between captures while viewers are connected. Overrides the Performance setting for this run only |
| `--prune-dir` | none | `PATH:DAYS`. Extra directory the retention job cleans: dated sub-folders or files older than DAYS are deleted. Repeatable. Not saved |
| `--no-access-log` | off | Disable per-request HTTP access logging |
| `--reload` | off | Parsed, but not passed to uvicorn (hot reload does not work) |

Notes:

- With `--camera` other than `simulator` and without `--simulator`, Zenivo keeps retrying the real
  camera in the background if it was not usable at startup. It runs on the simulator meanwhile.
- Sync is off by default (`--sync-target` is empty). Set it only if you run a compatible ingestion
  server; see [TRANSPARENCY.md](TRANSPARENCY.md#1-outbound-connections-made-by-the-server) for every outbound connection.
- The sync protocol (`/api/allsky/ingest/frame` and `/api/allsky/ingest/telemetry`) keeps its
  original name. Pushes are not queued: a missed round is not re-sent.

`--set-admin-password` sets the admin password (asks twice, or reads one line from stdin) and exits;
`--reset-admin` removes the password and all sessions and exits. See [SECURITY.md](SECURITY.md).

## Environment variables

Read by Zenivo:

| Variable | Default | Effect |
|----------|---------|--------|
| `ALLSKY_CAMERA` | `auto` | Default for `--camera` |
| `ALLSKY_CV_THREADS` | `1` | OpenCV thread count. One thread avoids spin-waiting worker pools on small CPUs |
| `OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, `MKL_NUM_THREADS` | `1` | Set to 1 by `run_allsky.py` unless already set. The shipped unit sets `OMP_NUM_THREADS=2` |
| `ALLSKY_SIMULATOR` | `0` | `1` prefers the simulator when the service builds its camera |
| `ALLSKY_DISABLE_RAW10` | unset | `1` keeps the RTS5869 camera on MJPG at night. Needed on a host where writing the Bayer route stops the stream (the mini-PC) |
| `ALLSKY_ISP_WB_K` | unset | Kelvin value (digits only). Locks the RTS5869 camera's own auto white balance; useful when day frames drift magenta |
| `ALLSKY_ISP_CAL` | `data/calibration/isp_shading.npz` | Path of the per-pixel ISP shading calibration made by `tools/isp_calibrate.py` |
| `ALLSKY_ISP_CAL_OFF` | unset | `1` skips the ISP shading correction even when the file exists |
| `LIBCAMERA_LOG_LEVELS` | unset | Read by libcamera. The shipped unit sets `*:WARN` to quiet it |

Internal (do not set by hand): `ALLSKY_RUNNER` is set by `run_allsky.py` so `src/api/main.py` does
not build a second app at import time. `PYTEST_CURRENT_TEST` is set by pytest and turns off
background threads, archiving and live auto-exposure.

Read by the tools:

| Variable | Used by | Effect |
|----------|---------|--------|
| `ALLSKY_CAL_EXPOSURE` | `tools/isp_calibrate.py` | Exposure in seconds for the calibration capture (default `0.0006`) |
| `ALLSKY_URL` | `tools/adsb_feeder.py` | Zenivo API base URL (default `http://127.0.0.1:8095`) |
| `VLAB_URL`, `VLAB_KEY`, `VLAB_INTERVAL` | `tools/adsb_feeder.py` | Ingest URL, service key and push interval in seconds (default 3, minimum 2) |

## Station configuration file

`data/station_config.json` holds everything the Settings pages save. It is merged over the factory
defaults at the top level only: a key present in the file replaces the whole default value for that
key. Zenivo writes the file itself. Edit it only while the service is stopped, and keep a copy
first. Back it up before upgrades.

The file holds the exact station position and may contain a Flightradar24 e-mail and token (`adsb.fr24_email`,
`adsb.fr24_token`; the Flightradar24 password is used once to log in and never stored), a map key
(`google_maps_key`) and the remote solver address (`remote_solver_url`). Zenivo writes it with mode 0600.
Treat it as private and do not commit it. All files under `data/` are listed in
[TRANSPARENCY.md](TRANSPARENCY.md#6-files-stored-on-the-host).

Factory defaults (neutral station "My station" at 0, 0, IMX335 sensor, 1.65 mm fisheye pointing at the zenith; `src/defaults.py`):

| Key | Contents |
|-----|----------|
| `latitude_deg`, `longitude_deg`, `elevation_m` | Station position. Defaults 0.0, 0.0, 0.0 (set your own in Settings, Station & Optics) |
| `location_name`, `geo_source` | Display name and where the coordinates came from (`default`, `manual`, `browser_gps`, `ip_geolocation`) |
| `remote_solver_url` | Remote blind plate solver, chosen from a list in Settings (Zenivo network `https://virtuallab3d.com`, the default, or Off = empty, never upload; no free text). While set, a frame, the station coordinates and the time are sent to it if the local solve is not confident. Settable in Settings, Station & Optics, or `POST /api/station/settings` |
| `plate_solver_endpoint` | Informational: which solver produced the last solution |
| `solve_source`, `solved_at`, `matched_stars_count`, `constellations_in_fov` | Result bookkeeping of the last plate solve |
| `camera_sensor` | Sensor specification (size in pixels, pixel pitch) |
| `optics` | Lens model, projection, focal length in mm, field of view, optical centre in pixels, 90-degree horizon radius in pixels, pixel scale |
| `orientation` | Centre RA/Dec, azimuth, altitude, zenith tilt, north position angle, rotation, CD matrix |
| `image_transform` | `flip_horizontal`, `flip_vertical`, `rotate_deg` (0 or 180); `rotate_isp_180` is added for RTS5869 day frames |
| `output_resolution` | `width`, `height` of the frames Zenivo outputs (default 2592 x 1944) |
| `timezone` | Station time zone: absent = the operating system zone, `coords` = whole-hour offset from the longitude, or an IANA name (`America/Chicago`). The clock, observing day and file names follow it; set it in Settings > Station & Optics |
| `weather` | `enabled` (default 0), `refresh_min` (15, 30, 60 or 180; default 30): weather and 48 h forecast from Open-Meteo (Settings > Integrations). Cache: `data/cache/weather.json` |
| `update` | `auto_check` (default true): daily check for a new release (Settings > Admin > Software update) |
| `camera_select` | `type`, `index` of the camera chosen under Settings > Camera (used at start when `--camera` is `auto`; absent = auto-detect) |

Keys added when you change the matching setting:

| Key | Contents |
|-----|----------|
| `bright_star_solution` | Last local plate solve. Editing azimuth, altitude, tilt or north angle in Settings also rewrites its pointing fields and sets `manual_override`. Overlays read the camera pointing from it |
| `camera_settings` | Saved manual exposure mode, restored at startup |
| `white_balance` | Mode (`auto`, `manual`) and red/blue gains |
| `sky_roi`, `sky_mask` | Sky region for cloud analysis and sky/obstruction polygons (fractions of the displayed frame) |
| `star_boost` | Star boost on/off and bin size (2 to 4) |
| `event_detection` | Per-type switches: `meteor`, `plane`, `satellite`, `clouds`, `flash` |
| `performance` | See [Performance profile](#performance-profile-for-small-hosts) |
| `storage` | Retention policy, see [Retention and storage](#retention-and-storage) |
| `adsb` | `host`, `port` (default 30003), `enabled` (1), `public_fallback` (0), `contribute` (0), `record` (1: read the feed all the time so the 3D map has recent tracks), `fr24_enabled` (0), `fr24_email`, `fr24_token` |
| `radiosondes_enabled` | 1 (default) or 0: weather balloons from SondeHub on the Live page. 0 stops the outbound request. The position sent is rounded to 0.5 degrees |
| `traffic_log`, `day_objects` | Aircraft/satellite log (`data/traffic/`; keys and defaults: `enabled` 1, `interval_s` 5, minimum 1, `retention_days` 14, minimum 1, `max_mb` 150, minimum 5) and the daytime object detection settings. `day_objects` keys (defaults, bounds): `enabled` (1), `classes` (`plane`, `bird`, `insect`, `object`), `sensitivity` (`normal`; `low`, `normal`, `high`), `min_blob_area` (2; 1 to 100), `max_blob_area` (700; 5 to 5000), `min_contrast_dn` (none = from the preset; 3 to 100), `retention_days` (14; 1 to 90), `max_mb` (50; 5 to 500) |
| `obs_overlay` | OBS browser-source overlay options |
| `google_maps_key` | Optional key that enables Google map layers in Flights 3D |
| `focus_record`, `focus_history`, `lens_focus` | Focus results, history and motorised-lens state |
| `plate_solve_debug` | Diagnostics of the last plate solve |

"Reset lens, sensor and mounting" (`POST /api/station/calibration/reset`) restores the camera
defaults for the optics keys and removes `bright_star_solution`.

## Settings pages

New station? Follow the step-by-step [FIRST_RUN.md](FIRST_RUN.md).

Open `/settings`. Every card saves itself when a field changes and shows a status chip in its header; buttons are only for
actions. Most changes take effect without a restart.

### Image Output

- **Image size**: output resolution, chosen from the presets of the attached camera. Smaller sizes
  cut CPU use.
- **Live view**: width and JPEG quality of the live stream (`stream_max_width`, `stream_jpeg_quality`)
  and the night star boost (sum-binning of the display copy, bin window 2 to 4).
- **Star count and cloud cover**: whether the sky analysis runs, its image width and the sampling
  interval (`sky_analysis_enabled`, `analysis_max_width`, `analysis_interval_s`).
- **Capture timing**: minimum interval between captures while someone watches
  (`min_capture_interval_s`), and the capture interval with nobody watching
  (`background_capture_interval_s`; 0 captures back to back, which suits meteor detection).
- **White Balance**: auto, or manual red and blue gains.
- **Orientation**: mirror, flip and 180-degree rotation. Applied right after capture, so archives,
  products, events and plate solving all see the corrected frame.

### Detection

- **Event detection**: switches for meteors, aircraft, satellites, flashes/lightning and clouds
  arriving or clearing. Events are saved under `data/uploads/allsky/events/<date>/`.
- **Cloud Cover**: the sky region used for the analysis (left, top, right, bottom as percentages)
  and a reset of the learned clear-sky reference. Reset it after re-aiming the camera.

### Station & Optics

- **Station Coordinates**: latitude, longitude, elevation, name. The page can use the browser's
  location or an IP lookup. Coordinates keep 5 decimals.
- **Sensor & Lens, Lens & Optics**: sensor size, focal length, projection, optical centre, horizon
  radius, distortion. Normally filled by a plate solve.
- **Camera Mounting**: azimuth, altitude, zenith tilt and north angle. A manual edit takes effect
  in all overlays immediately; run a star solve to refine it.
- **Solver status**: start a plate solve (quick or deep) and see its result. Run it on a clear night.
- **Focus**: the saved focus result and history.

### Sky Mask

Press **Auto-detect** to propose the polygons from the current frame (best at night), then correct them by hand and **Apply**. Or draw the polygons that mark the visible sky and the obstructions (trees, buildings). The star
search and the cloud analysis stay inside the sky polygons. Polygons are stored as fractions of the
displayed frame and turn with the image when you change the orientation.

### Admin

- **Public location precision**: `0` (exact), `0.01` (about 1 km), `0.1` (about 10 km, default) or `1.0` (about 100 km).
  Guests and the community card see the station location rounded to this grid; you see it exact. Stored as
  `public_precision_deg` in `data/community.json`.
- **Relay previews** (Settings, Community): `relay_previews` in `data/community.json`, default on. The same file holds the other community settings (`enabled` off, `name`, `public_url`, `share_thumbnail` on, `share_preview` on, `allow_open` on, `seeds`) (turn it off under Settings, Community). While on, this station stores and serves
  large previews of stations without a public address (up to 100 stations / 25 MB, 24 h; see [COMMUNITY.md](COMMUNITY.md#preview-relays)).
  A station without a public URL needs no setting: it sends its preview to relays automatically while "Share a preview image" and
  "Let visitors enlarge the preview" are on.
- **Password**: change it (signs out other browsers) or sign out everywhere. Files: `data/admin.json`,
  `data/admin_sessions.json`, `data/admin_setup_code.txt` (first start only). See [SECURITY.md](SECURITY.md).

### Integrations

Settings > Integrations starts with **Data sources**, one line per outside source (your ADS-B receiver, the public ADS-B
fallback, Flightradar24, satellites, weather balloons, airports) with a state: *Working*, *Waiting*, *Problem* or
*Off*. The same list is `GET /api/data-sources` (admin).

- **Satellites (orbits)**: a switch, an **orbit source** (CelesTrak by default, or the optional **SatNOGS DB**: one community catalogue of about 1700 satellites, about 250 KB per download, no groups), the CelesTrak groups to track (space stations and the brightest ones by
  default; weather, NOAA, GPS, Galileo and science can be added), how often to download the orbits (6 h to 7 days,
  default 24 h) and **Download now**. Under *Advanced* you can use another orbit source: an `https://` address with
  `{group}` in it that returns TLE sets; it must resolve to a public address. Stored as `satellites` in
  `station_config.json` (`enabled`, `source`, `groups`, `refresh_h`, `tle_url`). Orbits are cached in
  `data/uploads/allsky/cache/sat_tle.json`, so the overlay keeps working offline. No position is sent to the source.


- **ADS-B receiver**: host and port of an SBS-1 / BaseStation feed (usually port 30003), a switch,
  and the Flightradar24 Enterprise feed (email and password for a login, or a token). Aircraft
  come from your own receiver first. While it delivers nothing, the opt-in public fallback
  (`adsb.public_fallback`, off by default) uses a public ADS-B network (adsb.fi, then adsb.lol) and
  sends the station position rounded to 0.5 degrees. `adsb.contribute` (off by default) shares what
  your own receiver hears with the Zenivo aircraft network, signed with the station key; it never
  forwards public-network or Flightradar24 data. Flightradar24 data is added on top when enabled.
  The Flightradar24 feed is off until `adsb.fr24_enabled` is set to 1 (Settings, Integrations, Flightradar24
  block: switch, e-mail, password, token); it is an opt-in outbound connection. The token and the password are
  write-only (never shown again; the password is kept in memory only).
- **Weather balloons (SondeHub)**: one switch (`radiosondes_enabled`, on by default). The position sent to
  sondehub.org is rounded to 0.5 degrees.

## Retention and storage

The retention job runs 30 s after start and then every hour. Defaults:

| Key | Default | Meaning |
|-----|---------|---------|
| `frames_keep_days` | `7` | Days of archived frames kept |
| `products_keep_days` | `60` | Days of dated keograms, startrails and timelapses kept |
| `max_data_gb` | `8` | Cap for the whole `data/` directory |
| `min_free_gb` | `3` | Free space always left on the file system |
| `archive_interval_s` | `30` | Seconds between archived frames (the first 15 frames of a day are taken every 2.5 s) |
| `max_events_per_day` | `1000` | Events saved per day; more are dropped (protects against false-alarm storms) |
| `timelapse_refresh_s` | `1800` | Seconds between live timelapse re-encodes |
| `extra_dirs` | `[]` | `[{"path": ..., "keep_days": N}]`, filled by `--prune-dir` only (not settable through the API or the saved config) |

The job works in two passes. Age rules delete frame days, dated products and event days older than
their limits. Size rules then delete the oldest days, never today's data or the `latest.*` files,
until `data/` is under `max_data_gb` and the free space is above `min_free_gb`. Read the policy with
`GET /api/storage`, change it with `PUT /api/storage/policy`, run it now with
`POST /api/storage/cleanup`. The API accepts `frames_keep_days` 1 to 365, `products_keep_days` 1 to 3650, `max_data_gb` 0.5 to 10000, `min_free_gb` 0.1 to 1000, `archive_interval_s` 5 to 3600 and `timelapse_refresh_s` 60 to 86400; `max_events_per_day` is not settable through it. Archived frames are AVIF files (`frame_*.avif`, 1296 px wide, quality
62); older `frame_*.jpg` files stay readable.

## Performance profile for small hosts

The limits come from `GET /api/camera/performance`, which returns `current`, `defaults` and
`limits`. Values are clamped to the limits.

| Key | Default | Limits | Effect |
|-----|---------|--------|--------|
| `min_capture_interval_s` | `2.0` | 0.4 to 60 | Floor between real captures while viewers poll. Raise it to cut CPU |
| `background_capture_interval_s` | `0` | 0 to 3600 | Capture interval with no viewer. 0 = back to back, N = every N seconds |
| `analysis_max_width` | `960` | 320 to 2592 | Width of the copy used for star and cloud analysis |
| `sky_analysis_enabled` | `1` | 0 or 1 | Turn the star count and cloud cover sampling off |
| `analysis_interval_s` | `300` | 10 to 3600 | Seconds between star/cloud samples. Frames in between reuse the last result |
| `stream_max_width` | `960` | 320 to 2592 | Width of the live WebSocket JPEG |
| `stream_jpeg_quality` | `75` | 40 to 95 | JPEG quality of the live stream |

Settings made in the UI are saved in `station_config.json`. `--min-capture-interval` overrides the
first one for the current run only.

For a Raspberry Pi 4 (1.8 GB) the shipped unit uses `Nice=5`, `MemoryMax=700M` and
`OMP_NUM_THREADS=2`. Other measures that help on small hosts: a smaller output resolution, a
longer `analysis_interval_s` (or switching the sky analysis off), `--log-level warning` with
`--no-access-log`, and `ALLSKY_CV_THREADS=1`. Heavy analysis already runs on a downscaled copy,
only at the sampling interval, and never in daylight.

## Time zone

All recorded times are the system's local time with an explicit offset, for example
`2026-10-18T06:34:17+07:00`. Archive folders, frame names, event folders and dated products use the
local date `YYYY-MM-DD`. Set the machine's time zone (`timedatectl set-timezone ...`) before the
first run. Astronomical calculations convert to UTC internally. Archives written before
2026-10-18 06:34 (+07:00) are named in UTC; their telemetry carries the offset, so tools read times
from telemetry and not from file names.

## Dark calibration

The `/calibration` page (sidebar entry "Master Dark") builds the dark library, either the full
automatic library or one master dark at a chosen exposure and gain, and previews the stored darks
with statistics and warnings. Darks are stored under `data/calibration/darks/`. Exposures of 0.4 s
and longer are dark-subtracted using the closest master dark.
