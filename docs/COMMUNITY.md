# Community

Community is an opt-in feature: Zenivo stations share a small signed **station card** with each other and the
`/community` page shows the known stations on a map. There is no central server. It is **off by default**: while
it is off nothing is sent, no key is created and no background thread runs.

Turn it on in Settings, Community.

What a seed station (and any other station) receives, what it can and cannot do, and how to remove the built-in
seeds are spelled out in [TRANSPARENCY.md](TRANSPARENCY.md#3-the-built-in-community-seeds). The full list of
card fields is in [section 4](TRANSPARENCY.md#4-what-is-shared-when-community-sharing-is-on) there. This page
describes how the network works.

## What is shared

A card is one JSON object of at most 16 KB:

| Field | Meaning |
|-------|---------|
| `v` | Card version (1) |
| `id`, `pubkey` | Station id (first 16 bytes of the SHA-256 of the public key, hex) and the Ed25519 public key |
| `name` | Station name (60 characters at most; defaults to the station location name) |
| `lat`, `lon` | Location rounded to a grid: about 1 km (0.01 deg), about 10 km (0.1 deg, default) or about 100 km (1 deg). Setting the precision to `0` sends the exact position, so leave it on a grid value unless you want that |
| `url` | Public https address of the station, or null |
| `camera`, `software` | Sensor name and `Zenivo <version>` |
| `updated` | Unix time the card was signed |
| `open`, `preview` | Optional, only with a public URL: `open: false` removes the map's "Open station" link, `preview: true` says `<url>/api/community/preview.jpg` serves a large image (see "What map visitors may do") |
| `thumb` | Optional preview of at most 8 KB, only for stations without a public URL and only when preview sharing is on |
| `relays`, `preview_sha`, `preview_t` | Optional, only for a station without a public URL whose large preview is held by relay stations: up to 3 https origins, the SHA-256 of the image that was uploaded and the upload time. Stations that predate these fields reject such a card as having an unknown field, so they do not show that station; cards without these fields are unchanged |
| `sig` | Ed25519 signature over the card without `sig` (canonical JSON: sorted keys, no spaces, UTF-8) |

Anyone who can reach a station that shares can read its card, and so can every station it exchanges cards with.
The preview image of a station with a public URL is served at `/api/community/thumb.jpg` (320 px wide, JPEG,
cached for a minute); switch "Share a preview image" off to stop serving it.

## How gossip works

Every 15 minutes (first round about 20 seconds after start, timing varied by 20 %) a station:

1. re-signs its own card if the content changed or it is older than 6 hours;
2. picks up to 5 targets: every seed in the Settings list and a few random known stations that have a public
   URL. The list starts with the built-in seeds (`DEFAULT_SEEDS` in `src/community/__init__.py`); the owner can
   remove them, and "Restore built-in" puts them back;
3. for each target reads `GET <url>/api/community/peers`, verifies every card and keeps the newest per station;
4. sends its own card with `POST <url>/api/community/announce`.

A station that cannot be reached is skipped (counted in the status line, never an error). Cards older than 30 days
are dropped and at most 500 are kept (when full, ids that never refreshed their card and were first seen last go first, so a flood of new ids cannot push out long-known stations; `first_seen` is local metadata in the file, never part of a card or the `/peers` payload). A card's `url` must be an origin (`https://host[:port]`, no path, query, fragment or credentials); a path or trailing slash typed in Settings is dropped. Known stations are stored in `data/community_peers.json`, settings in
`data/community.json`.

## Preview relays

A station without a public URL (behind NAT, outbound only) cannot be asked for its large preview, so it parks the
preview on stations that offer to store it. Store-and-forward, no new server:

- **Receiver** (**on by default**; turn it off under Settings, Community, "Relay previews for stations without a public address",
  `relay_previews`: that deletes what is held and answers 404). `GET /api/community/status` and the answer to an announce then say `relay: true`. The station
  accepts `POST /api/community/relay/<station id>` (raw `image/jpeg`, 256 KB at most) and serves the stored image at
  `GET /api/community/relay/<station id>.jpg` (public, `image/jpeg`, `nosniff`, `Cache-Control: public, max-age=300`,
  `Cross-Origin-Resource-Policy: cross-origin`). Checks, cheapest first: 6 uploads per client per hour (120 in
  total), size, the station must have a card we know with **no** `url` (and not be us), headers `X-Zenivo-Time`
  (within 300 s) and `X-Zenivo-Sig` (Ed25519 by the card's key over `zenivo-relay-v1\n<id>\n<time>\n<sha256 of the body>`),
  at most one upload per station per 10 minutes. The image is then decoded and **re-encoded** here (JPEG only,
  header checked before decoding so a decompression bomb is refused, 64 px minimum, 24 megapixels at most, then at most 1280 px
  on the long side, quality 80, no metadata) and stored in `data/community_relay/<id>.jpg`.
- **Limits**: 100 stations, 25 MB in total (the least recently updated goes first), 24 hours. A sweep in every gossip round deletes
  files older than 24 h and those whose card vanished or got a url. Turning the switch off deletes everything held.
  `GET /api/community/peers` adds a top-level `relayed` map (`{id: time}`) for the previews this station holds; it is
  outside the signed cards.
- **Sender** (automatic): sharing on, "Share a preview image" and "Let visitors enlarge the preview" on, and no public URL.
  After announcing to a station that says `relay: true` it uploads the current preview (re-compressed to 200 KB at
  most) through the same SSRF guard (https, public addresses, pinned, no redirects): at most once per 30 minutes per
  target and to 3 targets per round, those that accepted before first. A relay is dropped after 3 failures in a
  row or 24 h without a success. The accepted origins are kept in `data/community_relays.json` and put in the card
  (`relays`, `preview_sha`, `preview_t`); with a public URL nothing is uploaded and the fields are absent. Per-target
  results ("preview relayed", "relay refused: ...") show in Settings under the network health list.
- **Map**: the large preview comes from `<url>/api/community/preview.jpg` if the card has a url, else from the station
  you are on when it holds the image, else from the first reachable origin in `relays`, else the 8 KB thumbnail.

## Aircraft network

The server that hosts the remote solver (the origin of the station's `remote_solver_url`) also keeps a community ADS-B
picture. It is optional and off by default; both parts need that URL to be set.

- **Read**: with Settings, Integrations, "Use a public ADS-B network" on, a station without a working receiver asks
  `GET /api/v1/adsb/point/<lat>/<lon>/150` (position rounded to 0.5 degree) first, then adsb.fi, then adsb.lol. The
  next one is used after 3 failures or 2 minutes without aircraft.
- **Contribute**: with "Share what my receiver hears with the Zenivo network" on, the station posts the aircraft its own
  receiver decoded to `POST /api/v1/adsb/ingest` every 5 s, signed with the station key (Ed25519, headers
  `X-Zenivo-Id`, `-Key`, `-Time`, `-Sig` as for preview uploads, domain `zenivo-adsb-v1`). Data from adsb.fi, adsb.lol
  and Flightradar24 is never forwarded. It works with community sharing off and does not announce the station. The
  server sees the station's IP address and can estimate where the receiver is.

Full list of what is sent: [TRANSPARENCY.md](TRANSPARENCY.md), rows 14 and 15.

## Seeds

Seeds are the first stations a new station asks. The built-in ones are `https://sky.hoan.uk` and
`https://allsky.hoan.uk` (stations run by the project author, `DEFAULT_SEEDS` in `src/community/__init__.py`).
They are never contacted while Community is off. To remove them, delete their lines in Settings, Community,
"Seed stations" and press Save ("Restore built-in" puts them back). Add your own, one URL per line. Seed
URLs you type may use `http` and private addresses so you can test on a LAN; the built-in seeds and URLs
learned from other stations never can.

## Limits

A station without a public URL (behind NAT, no port forwarding) can reach out to others and appears on the map
through the cards it announces, but nobody can open it or fetch its preview from the station; its small preview travels inside the card
and its large preview goes to [relay stations](#preview-relays). Only stations with a public https URL are asked by others during gossip.
Behind a reverse proxy the announce rate limit sees the proxy address, so all announces share one limit.

## Threat model

- **Forged or altered cards**: a card is accepted only if the id matches the public key and the Ed25519 signature
  verifies. Nobody can speak for another station, but anyone can create a new identity: a card shows what a station
  claims, it is not an endorsement.
- **Abuse of your station as a request proxy (SSRF)**: URLs from other stations are fetched only if they are https,
  carry no credentials and resolve only to public addresses; the connection goes to the checked address (no second
  DNS lookup), redirects are not followed, replies are JSON only, capped at 512 KB, with an 8 second timeout.
  An announced `url` is never fetched at announce time.
- **Flooding**: announces are limited to 30 per minute per client address (the real client behind a local proxy; one shared bucket with a wider limit when the address is loopback or unknown), counted before the signature is checked; bodies to 16 KB, fields have length caps
  and the store is capped (500 cards, 30 days). NaN/Infinity are refused everywhere and the registry file is written at most every 30 s.
- **Hostile content in the map**: card text is shown with DOM text APIs only; links open only for https URLs with
  `rel="noopener noreferrer"`; preview images are accepted only as `data:image/jpeg` or `data:image/webp` or from the
  station's https URL.
- **Relay abuse**: a relay hosts images chosen by other stations. Mitigations: only a station whose card we know and whose
  key signs the upload (bound to the exact bytes and the time) can store, one image per station, every image is
  decoded and re-encoded here (no metadata, no active content, at most 1280 px), 100 stations / 25 MB / 24 h caps,
  rate limits before any work, and the owner can turn it off (it is on by default) and that deletes everything. Anyone can still create
  new identities, so a relay can briefly host an unwanted picture: it is gone within 24 h.
- **Key**: the private key lives in `data/community_key.pem` (mode 0600). Deleting it gives the station a new id.
- Requires the `cryptography` package; without it Community reports "not available" and the rest of Zenivo is unaffected.

## Basemap

The community map draws its own basemap: coastlines only, from Natural Earth land, lakes and rivers (public domain; 1:50m, the 1:10m set loads when zoomed in;
`src/web/geo/land.json`, coordinates rounded to 0.01 degrees), plus a 30-degree grid. It shows no borders and no place
names and loads no third-party map tiles, so it takes no position on disputed territories and names. Leaflet is used
only for panning, zooming and markers.

## Working without the seed stations

Seeds are only a starting point. Every station keeps what it learned in `data/community_peers.json`:

- the cards of the stations it knows (kept for 30 days after their last update), and
- up to 50 addresses that answered a gossip round, most recent first, origins only, at most 2 per registrable host suffix so one domain cannot fill the list. They expire after 180 days without an answer, and never replace the seeds.

Each round contacts the seeds plus a few random stations from both lists, so stations that have met once keep
exchanging cards when every seed is down, and a station that was switched off for longer than 30 days (up to 180) can still rejoin
through a remembered address. A brand-new station still needs one reachable address: a built-in seed or any station URL
typed into Settings. A station without a public URL cannot be contacted by others, so it depends on the stations it
can reach.

## What map visitors may do

Three switches in Settings > Community decide what other people get from the community map (all need sharing on):

| Setting | On (default) | Off |
|---|---|---|
| Share a preview image | A small image (320 px, or at most 8 KB inside the card without a public URL) | No image at all |
| Let visitors enlarge the preview | Clicking the image opens `/api/community/preview.jpg` (up to 1280 px, refreshed once a minute); needs a public URL | Only the small image, scaled up |
| Let the map link to this station's page | An "Open station" link to the public URL | No link (card field `open: false`) |

Turning the link off only removes it from the map: the public URL is still part of the card (other stations need it
to gossip), and whoever knows the address can open the station like any visitor. A station without a public URL can
never be opened or enlarged from outside; it only shows its small embedded image.
