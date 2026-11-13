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
