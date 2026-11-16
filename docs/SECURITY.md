# Security model

Zenivo has one administrator and everybody else is a guest. Guests may look; only the admin may change
anything or read logs and settings. The service is meant to be safe behind a public tunnel
(for example cloudflared on the same host), so it never treats a request as trusted because it
comes from `127.0.0.1` or the LAN.

For the full list of what Zenivo sends, loads, stores and exposes (every outbound host, third-party browser
loads, files on disk, built-in community seeds), see [TRANSPARENCY.md](TRANSPARENCY.md). This page is the
security model.

## Who may do what

| What | Guest | Admin |
|------|-------|-------|
| Live view, frames, gallery, events, sky pages, flights and satellites maps, telemetry, `/api/status` | yes (station location rounded, see below) | yes |
| WebSocket `/ws/live`: connect, frames, telemetry, `ping`, `get_*` | yes | yes |
| WebSocket commands that change state (`set_exposure`, `set_gain`, `set_mode`, `trigger_master_dark`, ASC1 commands) | refused: `{"type":"error","code":"auth","message":"Sign in as admin"}` | yes |
| Every `POST`/`PUT`/`DELETE` under `/api/` | refused with 401 | yes |
| `GET /api/update/status` (installed and latest version) | yes | yes |
| Check for an update, switch the daily check, **install an update**, roll back, **restart the service**, set the station time zone (`POST /api/update/*`, `/api/system/restart`, `/api/station/timezone`) | refused with 401 (default deny, no allowlist entry) | yes |
| `GET /api/logs`, `/api/logs/download`, `/api/community/settings`, `/api/focus`, `/api/focus/frame`, `/api/focus/overview.jpg`, `/api/storage`, `/api/camera/devices`, `/docs`, `/redoc`, `/openapi.json` | 401 | yes (`ADMIN_GET` in `src/auth/policy.py`) |
| Pages `/settings`, `/calibration`, `/focus`, `/logs`, `/obs/dock` (also `/obs/control`, `/dock`) | redirect to `/login?next=...` | yes |
| `GET /api/adsb` receiver address and Flightradar24 account e-mail | blank | shown |
| Flightradar24 token and password | never returned | never returned (write-only: `GET /api/adsb` gives only `fr24_has_token`; `/api/station/config` strips both; the password is kept in memory only) |
| `POST /api/community/announce` (other stations) | open (signature checked, rate limited) | open |
| `POST /api/community/relay/<id>` (stations without a public URL; 404 when `relay_previews` is off) | open (rate limited, Ed25519 signature over the body, size and decode checks) | open |
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
on repeated lockouts up to 15 min (`429` with `Retry-After`). A global net guards against many addresses: 30
failures in 5 minutes pause login for everybody for a flat 60 s (it does not double, so an anonymous client
cannot keep the admin locked out). The client key is `CF-Connecting-IP`, else the right-most `X-Forwarded-For`
hop, but only when the TCP peer is loopback (a local proxy or tunnel); from any other peer those headers are
ignored and the peer address is used. The key is used only for throttling, never for authorisation.

## Cross-site requests

Every state-changing request (except `/api/community/announce` and `/api/community/relay/<id>`, which other stations send without a
browser) is refused with 403 when it carries an `Origin` header whose host:port differs from the `Host`
header. `SameSite=Lax` covers requests without an Origin. WebSocket commands get the same check on the
handshake. CORS is open for reads without credentials, so another site can never use your session.

## Machine ingest

`LOCAL_INGEST` in `src/auth/policy.py` lists routes a local process may call without a session. A request
qualifies only when the TCP peer is loopback **and** it carries none of `X-Forwarded-For`, `X-Real-IP`,
`Forwarded`, `CF-Connecting-IP`, `CF-Ray` (a tunnelled client always has some of them). The list is empty:
the ADS-B feeder (`tools/adsb_feeder.py`) only reads `GET /api/adsb/aircraft`, and the sync client
(`/api/allsky/ingest/*`) pushes out to a remote server rather than receiving.

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
- Relay uploads (`POST /api/community/relay/<id>`, on by default, the owner can turn off `relay_previews`): 6 per client per hour and 120 in total, counted first; 256 KB cap on a bounded stream; the card must be known, have no url and sign `zenivo-relay-v1`, id, time (+-300 s) and the body hash; one per station per 10 min; the image is decoded (header dimensions and pixel cap checked first) and re-encoded to a plain JPEG of at most 1280 px in a threadpool; stored under a strict `[0-9a-f]{32}` name, 100 stations / 25 MB / 24 h. The public GET serves it with `nosniff` and `Cross-Origin-Resource-Policy: cross-origin`.
- Announces count against a per-client bucket before the signature is verified; the registry is saved at most every 30 s. `/api/community/peers` is served from a 5 s cache.
- Throttling: the global login/setup net pauses for a flat 60 s after 30 failures in 5 min (it cannot lock the admin out for long); per-client state idle for 15 min is purged and the table holds at most 5000 keys.
- WebSocket guests may only send the read-only actions in `policy.WS_GUEST_ACTIONS`; `policy.ws_action_allowed` is the allow-list (default deny).
- `/api/focus*`, `/api/storage`, `/docs`, `/redoc` and `/openapi.json` are admin only.

## Proxy headers

`CF-Connecting-IP`, `X-Forwarded-For`, `X-Forwarded-Host` and `X-Forwarded-Proto` are believed only when the TCP peer is loopback (a proxy or tunnel on the same host); from any other peer they are written by the client and ignored. Behind a loopback proxy the client address is `CF-Connecting-IP`, else the right-most `X-Forwarded-For` hop (the one your proxy appended). Bind to `127.0.0.1` when a local proxy is the only way in; if the service listens on a LAN address, direct clients cannot spoof these headers, but a proxy on another host is not trusted either and all its clients share its address.

## Software update

A station checks the public GitHub releases list of the project once a day (switch in Settings > Admin; the
request carries only `Zenivo/<version>`). It never installs anything by itself: **Update now** is an admin `POST`.
The release archive is downloaded over HTTPS from GitHub, refused if it is larger than 120 MB, contains links or
paths outside its folder, or does not hold the announced version; the new `src/` must byte-compile before any file
is replaced. The program files are backed up first (the last three backups are kept in `data/update_backup/`),
`data/`, the virtual environment and `vendor/` are never touched, and **Go back to the previous version** restores
the backup. The same default-deny rule protects `POST /api/system/restart`. Run Zenivo under systemd so that it comes
back after an update or a restart; run by hand it re-runs its own command line.

## Outbound connections and third-party loads

Everything Zenivo connects to, every third-party file a visitor's browser loads (with SRI status), and what
each one receives is listed in one place: [TRANSPARENCY.md](TRANSPARENCY.md#1-outbound-connections-made-by-the-server).
In short: there is no telemetry, no update check, no remote configuration and no call home to the authors.
Optional connections (sync target, remote plate solver, Flightradar24, community sharing, local ADS-B
receiver, the public ADS-B fallback adsb.fi / adsb.lol) are off until you set them. Satellite orbit data, radiosonde data (the position sent is rounded to 0.5 degrees; switch
`radiosondes_enabled` turns it off) and airport data are fetched on demand, as that page explains.

Two security points that belong here:

- The Google Maps key returned by `/api/map/config` is meant for the browser, so it is visible to anyone who
  can open the 3D map. Restrict it in the Google Cloud console to HTTP referrers (your site) and to the Map
  Tiles API only. Guests do not receive it in `/api/station/config`, status, SSE or WebSocket payloads.
- Subresource Integrity pins the MapLibre, Leaflet and three.js files. Google Fonts CSS, map and terrain tiles
  and planet textures cannot be pinned.

## No backdoor

Facts, checkable in the source (details and the search commands: [TRANSPARENCY.md](TRANSPARENCY.md#5-guest-and-admin)
and [section 9](TRANSPARENCY.md#9-how-to-verify-these-claims-yourself)):

- No remote configuration, no remote command channel and no auto-update. The service never downloads code.
- No default password. Until an admin password is set nothing can change settings; the setup code is a random
  value written to `data/admin_setup_code.txt` on the machine.
- No bypass header or hidden route. Admin decisions come from one place, `src/auth/policy.py`.
- Every outbound connection is listed and each can be switched off or is off by default.
- The only third-party code a browser runs is the CDN list in the transparency page.

## Location rounding limits

The "public location precision" setting rounds the station's own coordinates for guests. It hides the address
from casual viewers only. Aircraft, radiosonde and satellite rows carry their own position plus the azimuth,
elevation and distance from the station, and the live image shows where they appear in the sky. Someone
determined can combine several of these to place the station well inside the rounding grid. These fields are
deliberately not removed, because they are the product.

The rounding applies to what Zenivo publishes to visitors and the community card. It does not change what the
server sends to the public data services it queries: the radiosonde request to SondeHub carries the position
rounded to 0.5 degrees, the public ADS-B fallback (off by default) sends the same 0.5 degree grid position to adsb.fi / adsb.lol, and the Flightradar24 box (only when you switch it on) is centred on the station to 0.1 degree.
Both are listed in [TRANSPARENCY.md](TRANSPARENCY.md#1-outbound-connections-made-by-the-server) and
[its limitations](TRANSPARENCY.md#8-known-limitations).

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

## Response headers

Every response carries `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`,
`X-Frame-Options: SAMEORIGIN` and `Permissions-Policy: geolocation=(self), camera=(), microphone=()` (the
settings page uses browser GPS). `Content-Security-Policy-Report-Only` lists what the pages load today (inline
scripts and the CDNs above); it only reports and blocks nothing. CORS allows any origin for `GET`, `HEAD` and
`OPTIONS` only, never with credentials.
