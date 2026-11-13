# Security model

Zenivo has one administrator and everybody else is a guest. Guests may look; only the admin may change
anything or read logs and settings. The service is meant to be safe behind a public tunnel
(for example cloudflared on the same host), so it never treats a request as trusted because it
comes from `127.0.0.1` or the LAN.

## Who may do what

| What | Guest | Admin |
|------|-------|-------|
| Live view, frames, gallery, events, sky pages, flights and satellites maps, telemetry, `/api/status` | yes (station location rounded, see below) | yes |
| WebSocket `/ws/live`: connect, frames, telemetry, `ping`, `get_*` | yes | yes |
| WebSocket commands that change state (`set_exposure`, `set_gain`, `set_mode`, `trigger_master_dark`, ASC1 commands) | refused: `{"type":"error","code":"auth","message":"Sign in as admin"}` | yes |
| Every `POST`/`PUT`/`DELETE` under `/api/` | refused with 401 | yes |
| `GET /api/logs`, `/api/logs/download`, `/api/community/settings` | 401 | yes |
| Pages `/settings`, `/calibration`, `/focus`, `/logs` | redirect to `/login?next=...` | yes |
| `GET /api/adsb` receiver address and Flightradar24 account e-mail | blank | shown |
| `POST /api/community/announce` (other stations) | open (signature checked, rate limited) | open |
| `POST /api/auth/setup`, `/login`, `/logout` | open (throttled) | open |

The rule is **default deny**: a request that is not `GET`, `HEAD` or `OPTIONS` needs an admin session
unless its path is in the short allowlist in `src/auth/policy.py` (`OPEN_MUTATIONS`). One function,
`classify()`, decides; `AuthMiddleware` (`src/auth/middleware.py`) applies it to every HTTP request
and to the WebSocket handshake. A new write route is protected without anyone remembering to add a check.

## First start and the setup code

Until a password exists, every protected thing is locked for everybody. At start the service writes a
one-time code to `data/admin_setup_code.txt` (mode 0600) and logs one warning line that names the file
(never the code). On the station:

```bash
cat data/admin_setup_code.txt
```

Open `/login`, enter the code and choose a password (10 to 256 characters). The code file is deleted and
you are signed in. Alternatives:

```bash
.venv/bin/python run_allsky.py --set-admin-password   # asks twice; reads one line from stdin when not a terminal
.venv/bin/python run_allsky.py --reset-admin          # forget the password and all sessions; a new code is issued at next start
```

Both work while the service runs for the password and sessions (a changed password signs everybody out);
a new setup code appears at the next start after `--reset-admin`. Forgot the password: run `--reset-admin`
and restart the service.

## Password and sessions

- Password: `scrypt` (n=2^14, r=8, p=1, random 16-byte salt) in `data/admin.json`, compared in constant time.
- Session: a random 32-byte token in the cookie `zenivo_session` (HttpOnly, SameSite=Lax, Path=/, `Secure`
  when the request is HTTPS, directly or through `X-Forwarded-Proto: https`; 30 days). The server keeps only
  the SHA-256 of the token in `data/admin_sessions.json`, so a restart does not sign you out and a leaked
  file cannot be replayed. The expiry slides with use; at most 20 sessions exist (the oldest is dropped).
- Changing the password, `--set-admin-password` and "Sign out everywhere" (Settings, Admin) end every session.

## Brute force

Five wrong passwords (or setup codes) from one client within 5 minutes lock that client for 60 s, doubling
on repeated lockouts up to 15 min (`429` with `Retry-After`). A global limit of 20 failures in 5 minutes
guards against many addresses. The client key is `CF-Connecting-IP`, else the first `X-Forwarded-For`
hop, else the peer address; it is used only for throttling, never for authorisation.

## Cross-site requests

Every state-changing request (except `/api/community/announce`, which other stations send without a
browser) is refused with 403 when it carries an `Origin` header whose host:port differs from the `Host`
header. `SameSite=Lax` covers requests without an Origin. WebSocket commands get the same check on the
handshake. CORS is open for reads without credentials, so another site can never use your session.

## Machine ingest

`LOCAL_INGEST` in `src/auth/policy.py` lists routes a local process may call without a session. A request
qualifies only when the TCP peer is loopback **and** it carries none of `X-Forwarded-For`, `X-Real-IP`,
`Forwarded`, `CF-Connecting-IP`, `CF-Ray` (a tunnelled client always has some of them). The list is empty:
the ADS-B feeder (`tools/adsb_feeder.py`) only reads `GET /api/adsb/aircraft`, and the sync client
(`/api/allsky/ingest/*`) pushes out to a hoan.uk server rather than receiving.

## What guests see of the station location

`public_precision_deg` (Settings, Admin, default 0.1) rounds the station latitude and longitude for
everyone who is not signed in, in every payload that carries them: `/api/station/config`, `/api/status`,
`/api/telemetry`, SSE and WebSocket telemetry, the plate-solve status, and the `station` object of the
aircraft and satellite responses. Rounded payloads carry `"location_rounded": true`. The same value
decides what the community card publishes. The server keeps calculating with exact coordinates, so
overlays, aircraft and satellite positions stay correct.

| Setting | Grid | Largest error (latitude and longitude combined, mid latitudes) |
|---------|------|--------------------------------------|
| `0` | exact | 0 |
| `0.01` | about 1 km | 0.8 km |
| `0.1` (default) | about 10 km | 7.8 km |
| `1.0` | about 100 km | 78 km |

Elevation stays exact. The station *name* is shown as typed: do not put a street address in it. The
activity log (which is admin only) no longer writes coordinates into its messages.

## Deploying behind a tunnel or proxy

Bind to `127.0.0.1` when a proxy or tunnel on the same host is the only way in. Serve the public side over
HTTPS so the cookie is `Secure`. Keep passing the original `Host` header (the CSRF check compares it with
`Origin`).

## Community hardening

- Card `lat`, `lon` and `updated` must be finite numbers; NaN and Infinity are refused when parsing (announce body, peer replies, the registry file) and never written (`allow_nan=False`).
- A card `url` is an origin only (`https://host[:port]`). Gossip targets and remembered contacts are origins as well, so a peer cannot smuggle a path into the requests this station makes.
- The SSRF guard also refuses IPv6 forms that carry an IPv4 address (NAT64 `64:ff9b::/96` and `64:ff9b:1::/48`, 6to4 `2002::/16`, Teredo `2001::/32`, IPv4-compatible `::a.b.c.d`).
- At most 500 cards are kept; when over the cap, ids that never refreshed their card and were first seen last are dropped, so known stations survive a flood. A new id may be at most 60 s ahead of the clock, a card at most 120 s.
- Remembered contacts: at most 2 per registrable host suffix, 180 days without an answer, never a replacement for the seeds.
- Announces count against a per-client bucket before the signature is verified; the registry is saved at most every 30 s. `/api/community/peers` is served from a 5 s cache.
- Throttling: the global login/setup net pauses for a flat 60 s after 30 failures in 5 min (it cannot lock the admin out for long); per-client state idle for 15 min is purged and the table holds at most 5000 keys.
- WebSocket guests may only send the read-only actions in `policy.WS_GUEST_ACTIONS`; `policy.ws_action_allowed` is the allow-list (default deny).
- `/api/focus*`, `/api/storage`, `/docs`, `/redoc` and `/openapi.json` are admin only.

## Proxy headers

`CF-Connecting-IP`, `X-Forwarded-For`, `X-Forwarded-Host` and `X-Forwarded-Proto` are believed only when the TCP peer is loopback (a proxy or tunnel on the same host); from any other peer they are written by the client and ignored. Behind a loopback proxy the client address is `CF-Connecting-IP`, else the right-most `X-Forwarded-For` hop (the one your proxy appended). Bind to `127.0.0.1` when a local proxy is the only way in; if the service listens on a LAN address, direct clients cannot spoof these headers, but a proxy on another host is not trusted either and all its clients share its address.

## Outbound connections

Everything Zenivo itself (the Python service) connects to. Nothing else is contacted: there is no telemetry,
no update check and no call home to the authors. The service reads no remote configuration.

| Target | What is sent | When | Default | Turn off |
|---|---|---|---|---|
| Sync target (`--sync-target URL`) | Live frame (JPEG), telemetry, device id `zenivo-<hostname>` | Every `--sync-interval` seconds | **Off** (empty). One info line at start names the target when enabled | Leave `--sync-target ""` |
| Remote plate solver (station setting `remote_solver_url`) | A frame, the station latitude/longitude and the time | Only when the local star match is not confident, during a plate-solve | **Off** (empty: the solver reports "local solve not confident; no remote solver configured") | Clear the field in Settings, Station & Optics |
| IP geolocation (`ipwho.is`, `ipapi.co`, https only) | The public IP of this host (implicit in the request) | Only when an admin presses "Locate via GPS / IP" and browser GPS is unavailable | Admin button only | Do not press it; enter coordinates by hand |
| Flightradar24 (`data-cloud.flightradar24.com`, `data-live.flightradar24.com`) | A map box (about +-3 degrees around the station for the traffic log, or the box a viewer asks for on the 3D map), optionally the FR24 account token. The request looks like a browser request because the endpoints are not an official API | Traffic log sampling (every 5 s, only once the camera has a star-solved pointing) and when someone opens the 3D flights map | **On** (`fr24_enabled` defaults to 1) | Settings, ADS-B: switch Flightradar24 off |
| `hexdb.io`, `raw.githubusercontent.com` (OpenFlights airline list) | Aircraft ICAO hex codes of aircraft heard by a local ADS-B receiver | When a new aircraft is heard; cached on disk | Only active with an ADS-B receiver or FR24 traffic | Switch the ADS-B receiver / FR24 off |
| OurAirports (`davidmegginson.github.io`) | Nothing about the station (plain file download) | Monthly refresh for the 3D map | On, cached 30 days | Do not open the 3D map |
| `celestrak.org` | A group name (satellite TLE download) | Daily refresh | On | Do not use satellite features |
| `api.v2.sondehub.org` | Station latitude/longitude and a search radius | While radiosonde data is requested (viewer or overlay), refreshed every 20 s | On when requested | Leave radiosonde overlays unused |
| Community seeds and peers | Station card (name, rounded location, public URL) | Only after the owner joins the community network | **Off** | Community section of Settings |
| Local ADS-B receiver (SBS-1 TCP port 30003) | Nothing (the service only reads) | Continuous when a host is configured | Off until a host is set | Clear the receiver host |
| `tools/adsb_feeder.py` | Aircraft rows to the URL in `VLAB_URL` with `VLAB_KEY` | Only if the separate `zenivo-adsb-feeder` unit is installed and configured | Not installed by `install.sh` | Do not install or disable the unit |

Browser-side third parties (loaded by the visitor's browser, not by the service): Google Fonts
(`fonts.googleapis.com`, `fonts.gstatic.com`), jsDelivr (`cdn.jsdelivr.net`: MapLibre, three.js), unpkg
(`unpkg.com`), Esri World Imagery (`server.arcgisonline.com`), AWS terrain tiles (`s3.amazonaws.com`),
Google photorealistic tiles (`tile.googleapis.com`, only with a Google Maps key configured), `flagcdn.com` and
`pics.avs.io` (aircraft photos and logos). Each one sees the visitor's IP address and the page that asked.
Self-host the assets to avoid that. Residual: the three.js import map entries cannot carry Subresource
Integrity; the MapLibre script and stylesheet on `/flights3d` are pinned with SRI hashes.

The Google Maps key returned by `/api/map/config` is meant for the browser, so it is visible to anyone who can
open the 3D map. Restrict it in the Google Cloud console to HTTP referrers (your site) and to the Map Tiles
API only. Guests do not receive it in `/api/station/config`, status, SSE or WebSocket payloads.

## No backdoor

Facts, checkable in the source:

- No remote configuration, no remote command channel and no auto-update. The service never downloads code.
- No default password. Until an admin password is set nothing can change settings; the setup code is a random
  value written to `data/admin_setup_code.txt` on the machine.
- No bypass header or hidden route. Admin decisions come from one place, `src/auth/policy.py`.
- Every outbound connection is listed above and each can be switched off or is off by default.
- The only third-party code a browser runs is the CDN list above.

To verify: `grep -rn "urlopen\|requests\.\|httpx\|http://\|https://" src --include=*.py` lists every network call;
`grep -rn "hoan.uk" src deploy` shows only example/default text of the author's own deployment (see the
community seed list and `deploy/*hoan.uk*` examples); `grep -rn "X-Forwarded\|CF-Connecting" src` shows the only
headers read for client addresses (used for throttling, never for authorisation).

## Location rounding limits

The "public location precision" setting rounds the station's own coordinates for guests. It hides the address
from casual viewers only. Aircraft, radiosonde and satellite rows carry their own position plus the azimuth,
elevation and distance from the station, and the live image shows where they appear in the sky. Someone
determined can combine several of these to place the station well inside the rounding grid. These fields are
deliberately not removed, because they are the product.

## Guest abuse limits

- Live streams (`/api/stream/mjpeg`, `/api/live/mjpeg`, `/api/stream/sse`, `/api/sse`, `/ws/live`): at most 40
  concurrent streams in total and 6 per client address; over that, HTTP 429 or WebSocket close code 1013. Guests'
  MJPEG is limited to 10 fps and 1280 px, and guest streams end after 10 minutes (clients reconnect). Admins are
  not limited. The client address is only a throttling key (`src.auth.policy.client_key`).
- WebSocket text commands: unknown actions are refused for guests; state-changing actions need an admin session.
- Flightradar24 proxy: bounded caches, flight ids must be lower-case hex, at least 2 s between outbound requests,
  guest map boxes are snapped to a 1-degree grid and must lie within 500 km of the station (else HTTP 400).
- Expensive public GETs (`/api/satellites`, `/api/traffic`, `/api/day-objects`, `/api/frames/near`,
  `/api/calibration/dark-preview`) use short shared caches. Guest limits: 50 satellite ids, 600 s trails, 24 h
  time ranges; invalid timestamps return 400.
- `/api/storage` hides absolute server paths from non-admins.

## Response headers

Every response carries `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`,
`X-Frame-Options: SAMEORIGIN` and `Permissions-Policy: geolocation=(self), camera=(), microphone=()` (the
settings page uses browser GPS). `Content-Security-Policy-Report-Only` lists what the pages load today (inline
scripts and the CDNs above); it only reports and blocks nothing. CORS allows any origin for `GET`, `HEAD` and
`OPTIONS` only, never with credentials.
