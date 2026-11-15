# Transparency: what Zenivo sends, stores and exposes

This page lists everything Zenivo connects to, everything a visitor's browser loads from other sites, what
is stored on the host, and who can read what. Every statement names the source file, so you can check it.
If code and this page ever disagree, the code is right and the page is a bug: please report it.

Short answers:

- **No telemetry, no call home.** Nothing is sent to the authors. No usage statistics, no crash reports.
- **No auto-update.** The service never downloads or runs code. You upgrade it yourself.
- **No remote configuration, no remote command channel, no licence check.**
- **No default password, no second account, no bypass header.** See [section 5](#5-guest-and-admin).
- **Some outbound connections are on by default**, because the 3D maps and sky overlays need public data
  (satellite orbits, weather balloons, airports). They are listed in [section 1](#1-outbound-connections-made-by-the-server)
  with exactly what each one sends. The ones that send your station position say so.

Contents: [1 Server outbound](#1-outbound-connections-made-by-the-server) |
[2 Browser third parties](#2-third-party-loads-by-the-browser) | [3 Community seeds](#3-the-built-in-community-seeds) |
[4 What community sharing publishes](#4-what-is-shared-when-community-sharing-is-on) |
[5 Guest and admin](#5-guest-and-admin) | [6 Files on the host](#6-files-stored-on-the-host) |
[7 Network exposure](#7-network-exposure) | [8 Known limitations](#8-known-limitations) |
[9 Verify it yourself](#9-how-to-verify-these-claims-yourself)

## 1. Outbound connections made by the server

"Server" means the Python service (`run_allsky.py` and `src/`). All connections use HTTPS with certificate
verification on (`src/net.py`), except where you type an `http://` address yourself (sync target, remote
solver, your own community seeds, ADS-B receiver). Requests identify themselves as `Zenivo/<version>`, except
Flightradar24 (see its row), adsb.fi and adsb.lol (`zenivo-allsky`) and the sync client (Python `requests` default).

| # | Destination | What is sent | When | Default | Turn off | Source |
|---|---|---|---|---|---|---|
| 1 | `celestrak.org` (`/NORAD/elements/gp.php?GROUP=stations` and `visual`) | Only the group name. Your IP address, as with any request | At most once per 24 h (retry after 30 min on failure), when satellite positions are first needed: a visitor opens the satellite overlay or page, or the traffic log runs at night with a solved camera pointing | **On** | Turn the traffic log off (Settings, Detection) and do not use satellite views | `src/analytics/satellites.py` |
| 2 | `api.v2.sondehub.org` (`/sondes?lat=..&lon=..&distance=..`) | The station position **rounded to a 0.5 degree grid** (about 55 km; the exact position stays on the host and is only used locally to place balloons in the sky) and a 350 km radius | At most every 20 s, while any browser has the Live tab open and visible (it asks `/api/radiosondes` every 20 s) or someone switches the balloon overlay on | **On** while a page asks for it. With the factory station (0, 0) it sends 0, 0 | Settings, Integrations, "Weather balloons (SondeHub)" switch (`radiosondes_enabled`, admin only; set to 0 nothing is requested and the Live page says the balloons are turned off and re-checks only every 5 minutes), or block `api.v2.sondehub.org` in your firewall | `src/analytics/radiosonde.py`, `src/api/traffic_mixin.py` (`get_radiosondes_now`) |
| 3 | `davidmegginson.github.io/ourairports-data` (`airports.csv`, `runways.csv`) | Nothing about the station: a plain file download (about 17 MB). Your IP address | When a visitor opens Flights 3D and the cached file is missing, older than 30 days, or the station moved | **On** (on demand), cached 30 days | Do not open Flights 3D | `src/analytics/airports.py` |
| 4 | `hexdb.io` (`/api/v1/aircraft/<icao hex>`, `/api/v1/route/icao/<callsign>`) | The ICAO hex code and the callsign of an aircraft shown on the map or in the flight list | When a new aircraft is shown (queued, answers cached on disk, unknown hex asked again after 7 days, routes after 12 h) | **On** when a local ADS-B receiver, the public fallback (rows 12 to 14) or the Flightradar24 feed delivers aircraft. Without any of them, nothing is looked up | Clear the ADS-B receiver host and keep the public fallback and Flightradar24 off | `src/analytics/aircraft_info.py` |
| 5 | `raw.githubusercontent.com` (OpenFlights `airlines.dat`) | Nothing: a file download | Once, then cached on disk | **On** with row 4 | Same as row 4 | `src/analytics/aircraft_info.py` |
| 6 | Flightradar24: `data-cloud.flightradar24.com` (`/zones/fcgi/feed.js`), `data-live.flightradar24.com` (`/clickhandler/`), `www.flightradar24.com` (`/user/login`) | A map box (`bounds`) rounded to 0.1 degree. The box is centred on the station (plus or minus 3 degrees for the traffic log; plus or minus 14 degrees of latitude and 18 of longitude in global mode), so Flightradar24 can infer the station position to about 10 km. Also: world boxes in global mode, the flight id when you open a flight, and, only if you enter your Flightradar24 account in Settings, that e-mail and password once at login (the password is not stored; the returned token is) | Traffic log every 5 s once the camera pointing is solved; when someone opens the 3D map; at least 2 s between requests | **Off** (`adsb.fr24_enabled` defaults to 0) | Settings, Integrations, Flightradar24 switch off | `src/analytics/fr24.py`, `src/api/traffic_mixin.py` |
| 7 | `ipwho.is`, then `ipapi.co` if the first fails (`https` only) | Nothing but your public IP address (implicit in the request). They answer with the coordinates of that address | Only when the admin presses "Locate via GPS / IP" (`POST /api/station/auto-locate`, admin only) | **Off** (button) | Do not press it; type the coordinates | `src/calibration/station_solver.py`, `src/api/plate_solve_mixin.py` |
| 8 | Remote plate solver at the address you set in `remote_solver_url` (`<url>/api/platesolve/solve`; `http` or `https`) | A JPEG of the current frame, the latitude, longitude and UTC time, and the field-of-view hint | Only during a plate solve, only when the local bright-star match is not confident (up to 3 min) | **Off** (empty: "no remote solver configured", nothing is sent) | Clear the field in Settings, Station and Optics | `src/calibration/station_solver.py`, `src/api/plate_solve_mixin.py` |
| 9 | Sync target you pass as `--sync-target` | A JPEG frame (width at most `--sync-max-width`), the telemetry JSON, headers `X-Device-Id` (`$ZENIVO_DEVICE_ID` or `zenivo-<hostname>`, so your host name leaves the machine), `X-Device-Token` if set, exposure and gain | Every `--sync-interval` seconds (default 60) | **Off** (empty). One log line at start names the target when on | Start without `--sync-target` | `run_allsky.py`, `src/sync/client.py` |
| 10 | Community: the seed list and a few known stations (`<origin>/api/community/peers`, `<origin>/api/community/announce`) | Your signed station card. See [section 4](#4-what-is-shared-when-community-sharing-is-on) | Every 15 minutes, up to 5 stations per round | **Off**. No key is created and no thread runs while off | Settings, Community, off | `src/community/gossip.py`, `src/community/netguard.py`, `src/community/manager.py` |
| 11 | Local ADS-B receiver (SBS-1, TCP, default port 30003) at the host you enter | Nothing (the service only reads the feed) | Continuously, reconnecting every 20 s | **Off** until you enter a host | Clear the host in Settings, Integrations | `src/analytics/adsb.py`, `src/api/traffic_mixin.py` |
| 12 | `opendata.adsb.fi` (`/api/v3/lat/<lat>/lon/<lon>/dist/150`), public ADS-B network, fallback source | The station position **rounded to a 0.5 degree grid** (about 55 km) and a 150 NM radius; User-Agent `zenivo-allsky`. Your IP address | Every 10 s, only while the fallback switch is on **and** your receiver is missing, not connected or silent for more than 90 s, and a page or recording needs aircraft. Stops within one cycle when the receiver delivers again. Slows down on errors and honours `Retry-After` (at most 5 min between retries, longer only if the server asks) | **Off** (`adsb.public_fallback` defaults to 0) | Settings, Integrations, "2. Public network (fallback)" switch off | `src/analytics/adsb_public.py`, `src/api/traffic_mixin.py` |
| 13 | `api.adsb.lol` (`/v2/point/<lat>/<lon>/150`), failover for rows 12 and 14 | Same as row 12 | Only after 3 failures in a row of the provider before it (or 2 minutes without aircraft), one request per 180 s (under adsb.lol's 500 requests a day per IP); adsb.fi is tried again after 10 minutes | **Off**, same switch as row 12 | Same as row 12 | `src/analytics/adsb_public.py` |
| 14 | Zenivo network server, the origin (`scheme://host[:port]`) of your `remote_solver_url` (`GET /api/v1/adsb/point/<lat>/<lon>/150`), first public fallback provider | The station position **rounded to a 0.5 degree grid** and a 150 NM radius; User-Agent `zenivo-allsky`. Your IP address | Every 10 s, under the same conditions as row 12. After 3 failures in a row, or 2 minutes without aircraft, the next provider (row 12) is used; the network is tried again after 10 minutes. Honours `Retry-After` | **Off** (same switch as row 12) and never used while `remote_solver_url` is empty | Settings, Integrations, "2. Public network (fallback)" switch off, or clear the remote solver URL | `src/analytics/adsb_public.py` |
| 15 | Zenivo network server, same origin as row 14 (`POST /api/v1/adsb/ingest`), contribution | The aircraft **your own receiver** decoded in the last 30 s (at most 300; hex, callsign, position, altitude, speed, track, vertical rate, age of the fix), the station id and public key, and a signature made with this station's key (the community key, created if absent; contributing does not announce the station and works with community sharing off). Your IP address. Never data from adsb.fi, adsb.lol or Flightradar24 | One request per 5 s while the switch is on, the receiver delivers (a message in the last 90 s) and a fix is fresh. Backs off to 5 min on errors, honours `Retry-After` | **Off** (`adsb.contribute` defaults to 0); never while `remote_solver_url` is empty | Settings, Integrations, "1. Your receiver", "Share what my receiver hears" switch off | `src/analytics/adsb_contribute.py`, `src/api/traffic_mixin.py` |

Flightradar24 settings (Settings, Integrations, "Flightradar24" block, admin only): a switch, an optional account
e-mail and password, and an optional subscription token. The token and the password are **write-only**:
`GET /api/adsb` reports only `fr24_has_token` (never the token or the password), `GET /api/station/config` removes
`fr24_token` and `fr24_password` for everybody including the admin (`src/api/plate_solve_mixin.py`
`get_station_config`), and the e-mail is shown to the admin only (guests get it blank, also inside the nested
`fr24` object of `GET /api/adsb`; `src/api/routes.py`, `src/api/guest_view.py`). The password is kept in memory only
and forgotten on restart or when the token is cleared; the token and e-mail are saved in `station_config.json`.

### Aircraft sources: order of use

1. **Your own receiver** (row 11) is the primary source, whenever it delivers.
2. **A public ADS-B network** (rows 14, 12 and 13, in this order: Zenivo network, adsb.fi, adsb.lol) is used only when the fallback switch is on **and** the receiver
   is not configured, not connected, or has sent no message for more than 90 s. As soon as the receiver delivers
   again the public network is no longer asked (within one poll cycle) and its aircraft are dropped. Aircraft from
   this source are labelled "public" and credited to adsb.fi or adsb.lol with a link in the Flights panel and on
   the 3D map, as the providers require. adsb.fi data is offered for personal, non-commercial use and must credit
   adsb.fi; adsb.lol data is under the ODbL 1.0. The position leaves the station only rounded to 0.5 degree. The
   Zenivo network is only in the chain when a remote solver URL is set (its origin is the network server); the next
   provider is used after 3 failures in a row or when the current one has returned no aircraft for 2 minutes, so an
   empty community network cannot blind the station, and the first provider is tried again after 10 minutes.
   Aircraft from the Zenivo network are labelled "public" and credited to "Zenivo network" with a link to its origin.
   **Contribution** (row 15) is separate and off by default: it sends only what your own receiver decoded, never
   anything read from rows 12 to 14 or from Flightradar24 (the contributor holds a reference to the receiver feed
   only). What the server can infer: your IP address, and from the positions your receiver hears (the aircraft it
   decodes stop at its radio horizon) a fair estimate of where the receiver is, finer than the 0.5 degree grid used
   for reading. Do not switch it on if that location must stay private.
3. **Flightradar24** (row 6) is independent and optional: off by default, used only when you switch it on, and not
   changed by the two items above.

Notes on the table:

- Rows 1, 2 and 3 are the only ones that are on in a fresh install. Row 2 sends only the position rounded to
  0.5 degrees; if that still matters to you, switch it off in Settings, Integrations (`radiosondes_enabled`).
- Row 6 uses the public web endpoints of Flightradar24, not an official API, and sends a browser-like
  `User-Agent`, `Origin` and `Referer` (`FR24_HEADERS` in `src/analytics/fr24.py`). These endpoints are not an official public API of
  Flightradar24. The feed is off by default; enabling it is the station owner's responsibility under Flightradar24's
  terms, and a subscription token is the supported way for account holders.
- A visitor can make the server contact rows 1 to 4 and 6 by opening a page that needs the data; a visitor
  cannot choose the destination. Guest map boxes for row 6 are snapped to a 1-degree grid and must lie within
  500 km of the station (`normalize_guest_bounds`).
- Local-only subprocesses: `ffmpeg` (timelapse, `src/pipeline/timelapse.py`) and `v4l2-ctl`
  (`src/hardware/uvc_generic.py`). They do not touch the network.
- `tools/adsb_feeder.py` is a separate optional script, not started by `install.sh`. It reads
  `GET /api/adsb/aircraft` as a guest and POSTs the aircraft list (without the `station` object) to the URL in
  `VLAB_URL` with the key in `VLAB_KEY`. It only runs if you install `deploy/zenivo-adsb-feeder.service` yourself.
- `src/sync/daemon.py` and `src/sync/queue.py` (an offline queue) are not used by `run_allsky.py`.

What is **not** there (checked by searching `src/` and `run_allsky.py` for every network primitive, see
[section 9](#9-how-to-verify-these-claims-yourself)): no telemetry or analytics client, no update or version
check, no download of code or configuration, no licence or activation check, no DNS-based or hidden endpoint.
The installer (`install.sh`) downloads Python packages from PyPI with `pip` (and may use `apt` if you do);
that happens once, on request, and is not part of the running service.

## 2. Third-party loads by the browser

These are requests made by the visitor's browser, not the server. Each host sees the visitor's IP address,
the page's origin (`Referrer-Policy: strict-origin-when-cross-origin`) and what it is asked for.
"SRI" means Subresource Integrity: the browser refuses a file whose hash differs from the pinned one.

| Page | Host | Purpose | SRI |
|---|---|---|---|
| `/` (and `/settings`, `/focus` and the other UI tabs), `/sky`, `/flights3d`, `/satellites3d`, `/community`, `/obs/dock` | `fonts.googleapis.com`, `fonts.gstatic.com` | Inter and JetBrains Mono fonts | No (Google Fonts CSS cannot carry SRI) |
| `/flights3d` | `cdn.jsdelivr.net` (`maplibre-gl@5.24.0` script and stylesheet) | Map engine | Yes (sha384) |
| `/flights3d` | `cdn.jsdelivr.net` (`three@0.160.0` `three.module.js`, `BufferGeometryUtils.js`) | 3D engine, through an import map | Yes (sha384, import map `integrity`) |
| `/satellites3d` | `cdn.jsdelivr.net` (`three@0.160.0` `three.module.js`, `OrbitControls.js`) | 3D engine, through an import map | Yes (sha384, import map `integrity`) |
| `/satellites3d` | `cdn.jsdelivr.net/gh/mrdoob/three.js@r160/examples/textures/planets/` | Earth and moon textures (images) | No |
| `/community` | `unpkg.com` (`leaflet@1.9.4` script and stylesheet) | Map panning and markers | Yes (sha256) |
| `/community` | The https address inside each station card (`<url>/api/community/thumb.jpg`, `preview.jpg`) | Preview image of a listed station. Your browser connects to that station, which sees your IP address | No (images) |
| `/flights3d` | `s3.amazonaws.com/elevation-tiles-prod` | Terrain elevation tiles (the tile numbers reveal the area you view) | No (images) |
| `/flights3d` | `server.arcgisonline.com` | Esri World Imagery base layer | No (images) |
| `/flights3d` | `tile.googleapis.com` | Google 2D map tiles. Only if a Google Maps key is configured and the layer is chosen | No (images) |
| `/flights3d` | `flagcdn.com`, `pics.avs.io` | Country flags, airline logos (by IATA code) | No (images) |

Details:

- The import maps and the SRI hashes are in `src/web/flights3d.html`, `src/web/satellites3d.html` and
  `src/web/community.html`. Browsers that do not support import-map integrity load those two three.js
  modules unchecked.
- Links that only open when you click them (not loaded automatically): `globe.adsbexchange.com`,
  `sondehub.org`, `www.flightradar24.com`, `www.n2yo.com` (`src/web/app/08-gallery-adsb.js`,
  `src/web/flights3d.html`, `src/web/satellites3d.html`).
- `/login` and the full-frame live page (`/full`, `/live/full`, `/obs`) load nothing from other hosts. The
  main UI pages (`/`, `/live`, `/gallery`, `/settings`, `/calibration`, `/focus`, `/logs` are all served from
  `index.html`) load only the Google Fonts. The community basemap is drawn from `src/web/geo/*.json` with no
  map tiles, and the `/sky` viewer code and star data are local files under `/static`.
- The Google Maps key: `GET /api/map/config` returns it to **any visitor** (the map needs it in the
  browser; `src/api/traffic_mixin.py`, `get_map_config`). Treat it as public: restrict it in the Google
  Cloud console to your site's referrer and to the Map Tiles API. It is removed from the other guest
  payloads (`src/api/guest_view.py`).
- To avoid every third party, serve the libraries, fonts and tiles from your own host. The Content Security
  Policy header (`src/api/main.py`) lists exactly the hosts above.

## 3. The built-in community seeds

`DEFAULT_SEEDS` in `src/community/__init__.py` is:

- `https://sky.hoan.uk`
- `https://allsky.hoan.uk`

They are stations run by the project author. They are only a place to start: a new station needs one
reachable address to find the others.

**Are they contacted by default?** No. Community sharing is **off by default** (`DEFAULTS["enabled"] = False`
in `src/community/manager.py`). While it is off there is no gossip thread and no key, and the seeds are never
contacted (`src/community/__init__.py` says so, and `GossipWorker` only starts from `CommunityManager.start()`,
which returns at once when disabled).

**What a seed receives once you turn sharing on:**

- Every 15 minutes (first round about 20 s after you save) your station does `GET <seed>/api/community/peers`
  and then `POST <seed>/api/community/announce` with your signed card (the fields in
  [section 4](#4-what-is-shared-when-community-sharing-is-on)).
- Like any HTTPS server, the seed sees your public IP address, the time, and the user agent
  `Zenivo/<version> community`. If you run behind NAT without a public URL, the IP is your router's.
- Nothing else: no frames (unless you gave a public URL and left the preview on, in which case others may
  fetch it from you, not from the seed), no logs, no settings, no password, no exact coordinates (only the
  rounded ones in the card), no host name.

**What a seed cannot do to you:**

- There is no command channel. Your station reads a seed's reply only as JSON, at most 512 KB, and keeps a
  card only if it passes strict checks: size, field types, finite numbers, https origin, matching public key
  and a valid Ed25519 signature (`verify` in `src/community/card.py`; the reply handling is `run_round` in
  `src/community/gossip.py`). Anything else is discarded. A reply cannot change a setting, run code, or make
  your station contact a private address.
- Your station never follows redirects, only dials public addresses it has checked (blocks LAN, loopback,
  link-local and tunnelled IPv4 ranges), and the body size and time are capped (`src/community/netguard.py`).
- The same applies to `POST /api/community/announce` on your own station: it only stores a card that
  verifies (`accept_announce` in `src/community/manager.py`), rate limited, and never fetches the card's `url`
  at that moment (`src/api/routes.py`, `announce_community_card`).
- What a card can do indirectly: its `url` becomes a candidate for later gossip rounds (up to 5 stations per
  round). Your station will then ask that address for its card list, under the same checks.

**Remove the seeds:** Settings, Community, field "Seed stations": delete the two lines and press Save. The
list is then exactly what you saved, even if empty (`seeds_set`). "Restore built-in" puts them back (nothing is
saved until you press Save). With an empty list your station talks only to stations it already knows. Seeds you
type yourself may use `http` and private addresses (for a LAN); the built-in ones and addresses learned from
other stations never can.

## 4. What is shared when community sharing is on

Source: `build_card` in `src/community/card.py`, `own_card` in `src/community/manager.py`. Sharing is a
switch in Settings, Community. Nothing below is sent while it is off.

| Card field | Meaning |
|---|---|
| `v` | Card format version (1) |
| `id` | Station id: first 16 bytes of the SHA-256 of the public key, in hex |
| `pubkey` | Ed25519 public key (base64) |
| `name` | Display name: the name you set in Community, else your station's location name, else "Zenivo station" (60 characters at most). Shown as typed: never put a street address in it |
| `lat`, `lon` | Station position **rounded** to `public_precision_deg` (see below) |
| `url` | Your public https address, as an origin only (`https://host[:port]`), or null |
| `camera` | Sensor model name (for example the IMX335), or null |
| `software` | `Zenivo <version>` |
| `updated` | Unix time the card was signed |
| `thumb` | Only if you have **no** public URL and preview sharing is on: a JPEG of at most 8 KB inside the card (`data:image/jpeg;base64,...`) |
| `open` | Present as `false` only if you switched off the "Open station" link on the map |
| `preview` | Present as `true` only if you have a public URL and allow the large preview |
| `relays`, `preview_sha`, `preview_t` | Only if you have **no** public URL, preview sharing and the large preview are on and at least one relay station accepted your preview: up to 3 relay origins, SHA-256 of the uploaded image, upload time |
| `sig` | Ed25519 signature over all other fields (canonical JSON: sorted keys, no spaces, UTF-8) |

Rules and limits:

- A card is at most 16 KB (`MAX_CARD_BYTES`). Nothing else is added: no elevation, no pointing, no host name,
  no frames except the preview below.
- **Coordinate rounding** (`public_precision_deg`, `src/auth/rounding.py`, `src/community/card.py`): `0.1`
  by default, about 10 km. Options: `0` (exact, no rounding), `0.01` (about 1 km), `0.1`, `1.0` (about 100 km).
  The same setting rounds what **guests** see of the station position everywhere on the site. It applies even
  if community sharing is off. Choosing `0` publishes your exact position.
- **Preview images** (`src/community/thumb.py`): `/api/community/thumb.jpg` is at most 320 px wide (JPEG
  quality 70) and `/api/community/preview.jpg` at most 1280 px wide (quality 80), both cached for 60 s, and
  public to anyone who can reach the station when sharing is on. They return 404 when the preview switches are
  off. The card thumbnail embedded for stations without a URL is at most 8 KB (160 px down to 64 px).
- **Retention** (`src/community/registry.py`): cards of other stations are kept 30 days after their last
  update, at most 500. Remembered contact addresses (origins that answered a round): at most 50, 180 days
  without an answer, at most 2 per registrable domain. Stored in `data/community_peers.json`.
- **Signatures**: Ed25519. The private key is created on first enable at `data/community_key.pem` (mode
  0600, `src/community/identity.py`). Deleting it gives the station a new id. Without the `cryptography`
  package the feature reports "not available" and everything else works.
- **Preview relays** (`src/community/relay.py`, [COMMUNITY.md](COMMUNITY.md#preview-relays)). Sender, automatic when you have no
  public URL and share the large preview: after announcing to a station that says `relay: true` this station uploads
  its current frame (JPEG, at most 200 KB, at most 1280 px) to `POST <origin>/api/community/relay/<your id>`, signed with
  your station key, at most once per 30 minutes per station and to 3 stations per round. Those stations are listed in your card and in
  Settings. Receiver, **on by default** (`relay_previews`; turn it off under Settings, Community): a station with a reachable address stores and serves, publicly, by default,
  large preview images that other stations (without a public URL) sent: re-encoded JPEG, at most 1280 px, up to 100 stations and
  25 MB, each deleted after 24 hours, when the sender's card disappears or gets a URL, or when you turn the switch
  off (which also makes the station answer 404 to uploads). A station with no reachable address never receives uploads. Honest note: a relay hosts images chosen by other stations. The risk is limited by the signature (only a known
  station key can store, one image per station), the server-side re-encoding (no metadata or active content), the
  caps, the 24 h lifetime and the switch.
- The announce endpoint (`POST /api/community/announce`) is open to other stations without a session or a
  CSRF origin check (`NO_ORIGIN_CHECK` in `src/auth/policy.py`) and is rate limited to 30 per minute per client.

## 5. Guest and admin

Zenivo has one administrator and everybody else is a guest. The rule is **default deny**: one function,
`classify()` in `src/auth/policy.py`, decides, and `AuthMiddleware` (`src/auth/middleware.py`) applies it to
every HTTP request and the WebSocket handshake.

**A guest (no session) can:**

- Read every `GET` that is not in the admin list below: the live view, snapshots, MJPEG, SSE and the
  WebSocket telemetry, gallery, events, frames and `/uploads/...` (keograms, startrails, timelapses, event
  images), sky and satellite and flight data, `/api/status`, `/api/telemetry`, `/api/station/config`,
  `/api/community/status|card|peers|thumb.jpg|preview.jpg`, `/api/map/config` (includes the Google Maps key),
  `/api/auth/status`.
- Use the read-only WebSocket actions `ping`, `get_telemetry`, `get_status` (`WS_GUEST_ACTIONS`).
- Call three `POST` routes: `/api/auth/setup`, `/api/auth/login`, `/api/auth/logout`, and other stations'
  `POST /api/community/announce` (`OPEN_MUTATIONS`).

**Only the admin can:**

- Every `POST`, `PUT`, `DELETE` and `PATCH` on any other path (default deny), and WebSocket state-changing
  actions (`set_exposure`, `set_gain`, `set_mode`, `trigger_master_dark`, binary commands 1 and 2).
- These `GET`s (`ADMIN_GET`): `/api/logs`, `/api/logs/download`, `/api/community/settings`, `/api/focus`,
  `/api/focus/frame`, `/api/focus/overview.jpg`, `/api/storage`, `/docs`, `/redoc`, `/openapi.json`.
- These pages (`ADMIN_PAGES`): `/settings`, `/calibration`, `/focus`, `/logs`, `/obs/dock` (with its aliases `/obs/control`, `/dock`) (guests are redirected to `/login`).
- See the exact station position, the ADS-B receiver address, the Flightradar24 e-mail, the remote solver
  address and the Google Maps key in the settings payloads. Guests get rounded coordinates and these fields
  removed (`src/auth/rounding.py`, `src/api/guest_view.py`, `GET /api/adsb` in `src/api/routes.py`).
  Exception: `/api/map/config` returns the map key to guests, as noted in [section 2](#2-third-party-loads-by-the-browser).

**Limits on guests** (`src/api/stream.py`, `src/api/routes.py`):

- At most 40 live streams in total and 6 per client address (MJPEG, SSE and WebSocket), else HTTP 429 or
  WebSocket close 1013. Admin streams are not limited.
- Guest MJPEG: at most 10 frames per second, at most 1280 px wide, and the stream ends after 10 minutes
  (`GUEST_STREAM_MAX_S = 600`); the page reconnects by itself. Guest SSE ends after 10 minutes too. The WebSocket has no time limit, only the stream-count limit above.
- Flight map boxes snapped to 1 degree and within 500 km of the station; aircraft lists and time ranges are
  capped; guests never trigger a write.

**Authentication** (`src/auth/manager.py`, `src/auth/routes.py`):

- **No default password, no second account, no master key, no bypass.** Until a password exists, nobody is
  admin. A session is valid only if its token matches a stored hash for the current password. I found no
  environment variable, header, query parameter or hidden route that grants admin (search for yourself, see
  [section 9](#9-how-to-verify-these-claims-yourself)).
- First start: the service writes a random one-time setup code to `data/admin_setup_code.txt` (mode 0600). It
  logs only the file's name, never the code. Reading the code needs access to the host. Entering it at `/login`
  (or running `python3 run_allsky.py --set-admin-password`) sets the first password and deletes the file.
- `python3 run_allsky.py --reset-admin` deletes the password and every session; a new code is issued at the
  next start. These are command-line actions on the host, not web routes.
- Password: 10 to 256 characters, hashed with scrypt (n=2^14, r=8, p=1, random 16-byte salt), compared in
  constant time, stored in `data/admin.json`.
- Session cookie `zenivo_session`: random 32-byte token, `HttpOnly`, `SameSite=Lax`, `Path=/`, `Secure` when
  the request is HTTPS (directly or via `X-Forwarded-Proto` from a loopback proxy), 30 days sliding, at most 20
  sessions. Only the SHA-256 of the token is stored (`data/admin_sessions.json`). A password change ends all
  sessions.
- Throttling: 5 wrong passwords or setup codes from one client in 5 minutes lock it for 60 s, doubling to
  15 min; a global net locks everybody for a flat 60 s after 30 failures. The throttle key is never used for
  authorisation.
- Cross-site protection: a state-changing request whose `Origin` host differs from `Host` gets 403.

### Daytime object detection runs locally

Settings, Detection, "Daytime objects" (birds, insects, aircraft) analyses the camera frames on the host only: the
module `src/analytics/day_objects.py` imports no network code, and nothing it produces is sent anywhere. Tracks
(class, size, speed, path as frame fractions) are written to `day_objects/` under the output directory and are
served to visitors through `GET /api/day-objects`. Settings live in `station_config["day_objects"]`, read and
written with `GET`/`POST /api/day-objects/config` (POST is admin only). Keys, defaults and bounds
(`normalize_config`, `CONFIG_DEFAULTS`):

| Key | Default | Bounds |
|---|---|---|
| `enabled` | 1 | 0 or 1 |
| `classes` | `plane`, `bird`, `insect`, `object` | any subset of those four |
| `sensitivity` | `normal` | `low`, `normal`, `high` |
| `min_blob_area` | 2 | 1 to 100 (pixels at 640 px work width) |
| `max_blob_area` | 700 | 5 to 5000 (always above `min_blob_area`) |
| `min_contrast_dn` | none (from the sensitivity preset) | 3.0 to 100.0 |
| `retention_days` | 14 | 1 to 90 |
| `max_mb` | 50 | 5 to 500 |

`GET /api/day-objects/status` now also returns the active `config`, the list of `classes` and the sensitivity
`presets` next to `log` and `tracker`. Old tracks are removed when they pass `retention_days` or the folder passes
`max_mb`, and a change is applied at once (`day_object_log()` in `src/api/events_mixin.py`).

### Settings save model

Every card in Settings saves itself when a field changes, and shows a status chip in its header (saving, saved or
the error). Buttons exist only for actions (locate, solve, clear token, test and similar). All writes are
`POST`/`PUT` requests and therefore admin only.

## 6. Files stored on the host

All under `data/` in the install directory (git-ignored). "0600" means only the service user can read it.
Everything else uses the process's default umask: protect the directory itself.

| File or folder | Content | Sensitive | Mode |
|---|---|---|---|
| `admin.json` | scrypt hash and salt of the admin password | High | 0600 |
| `admin_sessions.json` | SHA-256 of each signed-in browser's token, expiry | High | 0600 |
| `admin_setup_code.txt` | One-time setup code. Exists only while no password is set | High | 0600 |
| `community_key.pem` | Ed25519 private key of the station. Created only when community sharing is first enabled | High | 0600 |
| `station_config.json` | Exact latitude and longitude, name, optics, sky mask, ADS-B receiver host and port, `public_fallback`, Flightradar24 e-mail and token (the FR24 password is never stored), `radiosondes_enabled`, `day_objects`, `google_maps_key`, `remote_solver_url`, retention and other settings | High | 0600 |
| `community.json` | Community settings: switch, name, public URL, precision, preview switches, `relay_previews`, seed list. No secrets | Low | default |
| `community_relay/<station id>.jpg` | While "Relay previews" is on (the default; the folder only fills when a station uploads): large preview images pushed by other stations (re-encoded JPEG, at most 100 files / 25 MB, deleted after 24 h; the folder is emptied when the switch is turned off) | Low | 0640 |
| `community_relays.json` | Only for a station without a public URL: the relay stations that hold your preview (origin, last success, failures) | Low | default |
| `community_peers.json` | Public cards of other stations and remembered station addresses | Low | default |
| `frames/<date>/` | Archived sky frames (AVIF) and `telemetry.jsonl` (exposure, gain, star count, cloud cover). Kept 7 days by default | Medium | default |
| `live/latest.webp` | Latest frame | Medium | default |
| `uploads/allsky/` | Keograms, startrails, timelapses, event images and `events.jsonl`. **Served publicly** at `/uploads` | Medium | default |
| `logs/allsky.log`, `logs/activity.jsonl`, `logs/cloud_history.jsonl` | Service log (5 MB x 3), activity log (2 MB x 5; shown only to the admin), cloud series. Activity messages carry no coordinates | Medium | default |
| `traffic/`, `day_objects/` | Aircraft and satellites that crossed the camera view (14 days, 150 MB by default; `day_objects` 50 MB) | Low | default |
| `cache/` | `sat_tle.json`, `aircraft_db.json`, `airlines.dat`, `adsb_trails.json`, `airports_near.json` (this one records the station position rounded to 0.001 degree) | Medium | default |
| `calibration/` | Master darks, hot-pixel map, `cloud_reference.json`, optional `isp_shading.npz` | Low | default |
| `plate_solve_debug/` | Debug PNGs of plate-solve frames. Only if `plate_solve_debug` is set in the station config | Low | default |

Backups you make by hand (for example `station_config.json.bak-*`) are not created by the service and do not
get the 0600 mode. `data/sync/sync_queue.db` exists only if you use the unused `SyncQueue` code.
Retention rules are in [CONFIGURATION.md](CONFIGURATION.md#retention-and-storage).

## 7. Network exposure

- **Bind address**: `--host` defaults to `127.0.0.1` (this machine only) in `run_allsky.py`, in `install.sh`
  and in the systemd template. Use `--host 0.0.0.0` only on purpose. The default port is `8080` (`--port`).
- **Plain HTTP.** Zenivo does not terminate TLS. For internet access put nginx, Caddy or a tunnel with HTTPS
  in front and keep `--host 127.0.0.1`. On plain HTTP the admin password crosses the network in clear text.
- **Proxy headers** (`src/auth/policy.py`): `X-Forwarded-For`, `X-Forwarded-Host`, `X-Forwarded-Proto` and
  `CF-Connecting-IP` are believed only when the TCP peer is loopback (a proxy or tunnel on the same host).
  From any other peer they are ignored. `uvicorn` is started with `proxy_headers=True` and
  `forwarded_allow_ips="127.0.0.1"` (`run_allsky.py`), so it also trusts them only from loopback. The
  client address is used for throttling, never for authorisation.
- **Response headers** on every HTTP response (`src/api/main.py`): `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: strict-origin-when-cross-origin`, `X-Frame-Options: SAMEORIGIN`,
  `Permissions-Policy: geolocation=(self), camera=(), microphone=()`, and
  `Content-Security-Policy-Report-Only`. **The CSP is report-only**: it lists what the pages load today
  (`'unsafe-inline'` and `'unsafe-eval'` included) and blocks nothing.
- **CORS**: any origin may read (`allow_origins=["*"]`) but only with `GET`, `HEAD`, `OPTIONS`, header
  `Content-Type`, and never with credentials. The community routes add `Access-Control-Allow-Origin: *`
  themselves. Another website therefore cannot use your admin session.
- **Ports opened**: the one HTTP port above. The service listens on nothing else. It connects out to your ADS-B
  receiver (if configured) and to the destinations in [section 1](#1-outbound-connections-made-by-the-server).

## 8. Known limitations

- **Rounding does not hide the station from a determined observer.** Aircraft, satellite and balloon rows
  carry their own positions together with azimuth, elevation and distance from the station, the planets and
  the star field in the image depend on latitude and longitude, and the airport list on the 3D map is the set
  of airports within 450 km of the exact position. Someone who combines these can place the station inside
  the rounding grid. These fields are the product, so they are not removed.
- **The radiosonde lookup sends the position rounded to 0.5 degrees** (about 55 km) to SondeHub (row 2 above),
  and the Flightradar24 box centre is accurate to about 10 km (row 6). The public precision setting does not change
  either of them.
- **Some third-party resources have no SRI**: Google Fonts CSS, planet textures, map tiles, flags and
  logos (images and CSS that cannot or do not carry hashes). The libraries do have it ([section 2](#2-third-party-loads-by-the-browser)).
- **The Content Security Policy is report-only** and allows inline scripts, so it is a record, not a barrier.
- **Guest MJPEG stops after 10 minutes** and is limited to 10 fps and 1280 px; the page reconnects.
- **The map key is public** to anyone who opens `/flights3d` (`/api/map/config`).
- **Community cards are claims, not endorsements.** Anyone can create a new identity. Preview images from
  listed stations load directly from those stations in a visitor's browser.
- **Plain HTTP by default**; TLS is your reverse proxy's job.
- **Station name and location name are shown as typed** to guests and, if you share, in the card.
- **Flightradar24 and OpenFlights data** come from unofficial or third-party sources; availability and terms
  are theirs. The Flightradar24 integration uses web endpoints that are not an official API (row 6).
- **Licences of bundled and loaded third-party components** (libraries, fonts, star and map data, data feeds) are
  listed in [THIRD_PARTY.md](../THIRD_PARTY.md).

## 9. How to verify these claims yourself

From the install directory (`src/` is the whole service):

```bash
# Every file that can open a network connection
grep -rlE "urlopen|pooled_get|requests\.|http\.client|create_connection|HTTPSConnection|socket\.socket" src run_allsky.py --include=*.py

# Every host named in the Python code (the list should match section 1 and 3)
grep -rhoE "https?://[a-zA-Z0-9.-]+" src run_allsky.py --include=*.py | sort -u

# Every host named in the web pages (section 2); the vendored sky viewer under src/web/sky is local code
grep -rhoE "https?://[a-zA-Z0-9.-]+" src/web --include=*.html --include=*.js | sort -u

# Which script and style tags carry integrity hashes
grep -n "integrity" src/web/*.html

# The access rules (section 5)
sed -n 1,80p src/auth/policy.py

# Anything that looks like a bypass, master key or hidden admin route
# (the hits are camera-chip terms: "ISP bypass" RAW mode and the sensor's debug lock in src/hardware/)
grep -rniE "backdoor|bypass|master.?key|X-Admin|ADMIN_TOKEN|override.*password" src run_allsky.py --include=*.py
grep -n "@router\.\|@auth_router\.\|@app\." src/api/*.py src/auth/routes.py | grep -vi "get(" | head -80
```

Watch the traffic itself on the host (as root, replace `eth0`):

```bash
# Which remote addresses the service talks to right now
sudo ss -tnp | grep -i python

# Names it resolves and connects to (leave it running for a few minutes, open the pages you use)
sudo tcpdump -i eth0 -nn 'port 53 or port 443' | head -200

# Prove an "off" setting: block everything but your LAN for the service user and see what still works
# (example with nftables, user "pi"): nft add rule inet filter output meta skuid pi ip daddr != 192.168.0.0/16 drop
```

If you find a connection, a file or a route that is not in this document, that is a documentation bug (or
worse). Please open an issue.

Related: [SECURITY.md](SECURITY.md) (threat model and hardening), [COMMUNITY.md](COMMUNITY.md) (the network
feature), [CONFIGURATION.md](CONFIGURATION.md) (all settings), [INSTALL.md](INSTALL.md).
