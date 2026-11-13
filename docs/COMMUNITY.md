# Community

Community is an opt-in feature: Zenivo stations share a small signed **station card** with each other and the
`/community` page shows the known stations on a map. There is no central server. It is **off by default**: while
it is off nothing is sent, no key is created and no background thread runs.

Turn it on in Settings, Community.

## What is shared

A card is one JSON object of at most 16 KB:

| Field | Meaning |
|-------|---------|
| `id`, `pubkey` | Station id (first 16 bytes of the SHA-256 of the public key, hex) and the Ed25519 public key |
| `name` | Station name (60 characters at most; defaults to the station location name) |
| `lat`, `lon` | Location rounded to a grid: about 1 km (0.01 deg), about 10 km (0.1 deg, default) or about 100 km (1 deg). Your exact position is never sent |
| `url` | Public https address of the station, or null |
| `camera`, `software` | Sensor name and `Zenivo <version>` |
| `updated` | Unix time the card was signed |
| `thumb` | Optional preview of at most 8 KB, only for stations without a public URL and only when preview sharing is on |
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
are dropped and at most 2000 are kept. Known stations are stored in `data/community_peers.json`, settings in
`data/community.json`.

## Seeds

Seeds are the first stations a new station asks. Add your own, one URL per line, in Settings, Community. Seed
URLs you type may use `http` and private addresses so you can test on a LAN; URLs learned from other stations
never can.

## Limits

A station without a public URL (behind NAT, no port forwarding) can reach out to others and appears on the map
through the cards it announces, but nobody can open it or fetch its preview from the URL; its preview travels inside the card
instead. Only stations with a public https URL are asked by others during gossip.
Behind a reverse proxy the announce rate limit sees the proxy address, so all announces share one limit.

## Threat model

- **Forged or altered cards**: a card is accepted only if the id matches the public key and the Ed25519 signature
  verifies. Nobody can speak for another station, but anyone can create a new identity: a card shows what a station
  claims, it is not an endorsement.
- **Abuse of your station as a request proxy (SSRF)**: URLs from other stations are fetched only if they are https,
  carry no credentials and resolve only to public addresses; the connection goes to the checked address (no second
  DNS lookup), redirects are not followed, replies are JSON only, capped at 512 KB, with an 8 second timeout.
  An announced `url` is never fetched at announce time.
- **Flooding**: announces are limited to 30 per minute per client address, bodies to 16 KB, fields have length caps
  and the store is capped (2000 cards, 30 days).
- **Hostile content in the map**: card text is shown with DOM text APIs only; links open only for https URLs with
  `rel="noopener noreferrer"`; preview images are accepted only as `data:image/jpeg` or `data:image/webp` or from the
  station's https URL.
- **Key**: the private key lives in `data/community_key.pem` (mode 0600). Deleting it gives the station a new id.
- Requires the `cryptography` package; without it Community reports "not available" and the rest of Zenivo is unaffected.

## Basemap

The community map draws its own basemap: coastlines only, from Natural Earth 1:50m land polygons (public domain,
`src/web/geo/land.json`, coordinates rounded to 0.01 degrees), plus a 30-degree grid. It shows no borders and no place
names and loads no third-party map tiles, so it takes no position on disputed territories and names. Leaflet is used
only for panning, zooming and markers.

## Working without the seed stations

Seeds are only a starting point. Every station keeps what it learned in `data/community_peers.json`:

- the cards of the stations it knows (kept for 30 days after their last update), and
- up to 50 addresses that answered a gossip round, most recent first. These do not expire.

Each round contacts the seeds plus a few random stations from both lists, so stations that have met once keep
exchanging cards when every seed is down, and a station that was switched off for longer than 30 days can still rejoin
through a remembered address. A brand-new station still needs one reachable address: a built-in seed or any station URL
typed into Settings. A station without a public URL cannot be contacted by others, so it depends on the stations it
can reach.
