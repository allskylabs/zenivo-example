# Third-party components

Zenivo itself is released under the [MIT licence](LICENSE). This page lists everything else that ships in the
repository, is installed with it, or is loaded at run time, with its licence and source. Each entry says where
it was checked. **"Licence not verified"** means the licence or terms could not be established from the files
in this repository or from the upstream source, and the owner must settle it before relying on it.

What leaves the machine at run time is a separate topic: see [docs/TRANSPARENCY.md](docs/TRANSPARENCY.md).

## 1. Python dependencies (installed by `pip`, not bundled)

Declared in [requirements.txt](requirements.txt) and [pyproject.toml](pyproject.toml). Licences read from the package
metadata of the versions that `pip install -r requirements.txt` resolved on 2026-11-14.

| Package | Licence | Source |
|---|---|---|
| numpy | BSD-3-Clause (the wheel also carries 0BSD, MIT, Zlib and CC0-1.0 parts) | https://numpy.org |
| Pillow | MIT-CMU (HPND) | https://python-pillow.org |
| pillow-avif-plugin | MIT | https://github.com/fdintino/pillow-avif-plugin |
| cryptography | Apache-2.0 OR BSD-3-Clause | https://github.com/pyca/cryptography |
| sgp4 | MIT | https://github.com/brandon-rhodes/python-sgp4 |
| urllib3 | MIT | https://github.com/urllib3/urllib3 |
| requests | Apache-2.0 | https://github.com/psf/requests |
| httpx | BSD-3-Clause | https://github.com/encode/httpx |
| fastapi | MIT | https://github.com/fastapi/fastapi |
| uvicorn | BSD-3-Clause | https://github.com/encode/uvicorn |
| websockets | BSD-3-Clause | https://github.com/python-websockets/websockets |
| opencv-python-headless | Apache-2.0 for OpenCV and the packaging; the wheel bundles FFmpeg and other libraries under their own licences (FFmpeg: LGPL-2.1+, see the licence files inside the wheel) | https://github.com/opencv/opencv-python |
| pytest (development only, `requirements-dev.txt`) | MIT | https://github.com/pytest-dev/pytest |
| picamera2 (optional, installed from `apt` on a Raspberry Pi, never from pip) | BSD-2-Clause | https://github.com/raspberrypi/picamera2 (not verified in this repository) |

Dependencies of these packages (starlette, pydantic, anyio, certifi and so on) are pulled in by `pip` under their own licences.

## 2. Data files in the repository

| Path | What it is | Licence / terms | Source and how it was produced |
|---|---|---|---|
| `src/web/geo/land.json`, `land10.json`, `lakes.json`, `lakes10.json`, `rivers.json`, `rivers10.json` | Coastlines, lakes and rivers for the neutral community map (1:50m and 1:10m sets) | Public domain (Natural Earth terms of use: "in the public domain", https://www.naturalearthdata.com/about/terms-of-use/, read 2026-11-14) | Natural Earth, https://www.naturalearthdata.com. The files are simplified GeoJSON derived from the Natural Earth land, lakes and rivers layers (commits `37a33fa`, `14c30b3`, `src/web/community.js`, `docs/COMMUNITY.md`). The conversion script is not in the repository. The page shows the attribution "Natural Earth (public domain)" |
| `src/web/skydata/stars/` (index and `o0` to `o4` tiles, about 12 MB) | Star tiles for the `/sky` page | Header of `stars/index.json` names the source: Hipparcos (ESA 1997), CDS catalogue I/239. ESA and CDS terms ask for acknowledgement; **licence not verified** (no SPDX licence is stated) | ESA, "The Hipparcos and Tycho Catalogues" (1997), via CDS Strasbourg, https://cdsarc.cds.unistra.fr/viz-bin/cat/I/239. Tiled by the author's `sky-data` tooling (not published yet) |
| `src/web/skydata/catalog/stars_named.json` | Bright named stars with Hipparcos numbers, positions, magnitudes, B-V, English and Vietnamese names | Positions and magnitudes come from Hipparcos (see above); the names and translations were collected by the author; **licence not verified** | Copied from the author's `astrophoto` repository (not published yet) |
| `src/web/skydata/catalog/constellations.json` | Stick figures, boundaries and names of the 88 constellations, with French and Vietnamese names | **Licence not verified**: the original source of the figures and boundaries is not recorded in the repository (the IAU boundaries are public data, the line figures are a common convention) | Copied from the author's `astrophoto` repository (not published yet) |
| `src/web/skydata/catalog/messier.json` | 110 Messier objects | **Licence not verified**: extracted from a `dso.json` of the author's `astrophoto` repository whose own source is not recorded | See above |
| `src/web/skydata/catalog/milkyway.avif`, `milkyway.png` | Milky Way texture | Public domain (NASA); stated in `tools/skyview/README.md` | NASA Scientific Visualization Studio, Deep Star Maps 2020 galactic map, https://svs.gsfc.nasa.gov/, with the stars median-filtered out by the author |
| `src/web/sky_catalog.js` | 88 constellation stick figures, bright navigation stars (magnitude 3.8 or brighter) and Messier objects, as a JavaScript object, used by the live-view overlay | **Licence not verified**: its header names no source | Origin not recorded |
| `src/web/brand/*`, `docs/design/ui-redesign.html` | Zenivo logo, icons and a design note | Part of Zenivo, MIT | Created for this project (`docs/BRAND.md`) |
| `tests/fixtures/strobe_overcast/*.png` | Three sample frames of one all-sky camera used by a test | Real frames of one station, committed by the author (MIT like the rest of the repository) | The author's station (see `tests/unit/test_sky_events.py`) |

## 3. Bundled JavaScript: the sky viewer (`src/web/sky/`)

`sky.js`, `core-D2YY-WWc.js` and `dome3d-wWkzaZu3.js` are a minified bundle built from the TypeScript in
[tools/skyview](tools/skyview). The bundle is committed and is what Zenivo serves. **The sources it is built from need
two sibling repositories of the author that are not published yet**, so the bundle cannot be rebuilt from this
repository alone (see [tools/skyview/README.md](tools/skyview/README.md)).

| Content of the bundle | Licence | Source |
|---|---|---|
| Zenivo's own viewer code (`tools/skyview/src`) | MIT (Zenivo) | this repository |
| `@astrophoto/sky-engine`, `@astrophoto/sky-data` (WebGL star tiles, projections, inspector) | Author's own code in unpublished repositories; **licence not verified** until they are published and licensed | not published yet |
| three.js (lazy `dome3d` chunk, 3D dome; the revision is not recorded in the bundle header) | MIT | https://github.com/mrdoob/three.js |
| astronomy-engine (Sun, Moon, planets, sidereal time; imported by `tools/skyview/src/astro.ts`) | MIT | https://github.com/cosinekitty/astronomy |

`satellite.js` is stubbed out of the build (`tools/skyview/stubs/satellite.js`); it is not in the bundle.
The `/sky` page defaults to a fixed observer position when no `lat`/`lon` query parameters are given (a constant in
`tools/skyview/src/main.ts`).

## 4. Browser libraries loaded from a CDN

The pages load these from the hosts below; the pinned versions and the SRI hashes are in the HTML files named in
[docs/TRANSPARENCY.md](docs/TRANSPARENCY.md) section 2. Licences were read from each package's `package.json` on the CDN on 2026-11-14.

| Library | Used by | Licence | Source |
|---|---|---|---|
| three.js 0.160.0 (`three.module.js`, `BufferGeometryUtils.js`, `OrbitControls.js`) | `/flights3d`, `/satellites3d` | MIT | https://cdn.jsdelivr.net/npm/three@0.160.0/, https://github.com/mrdoob/three.js |
| three.js r160 example textures (Earth and Moon images) | `/satellites3d` | **Licence not verified** for the individual images (the three.js repository is MIT; the example textures carry their own notes) | https://github.com/mrdoob/three.js/tree/r160/examples/textures/planets |
| MapLibre GL JS 5.24.0 | `/flights3d` | BSD-3-Clause | https://github.com/maplibre/maplibre-gl-js |
| Leaflet 1.9.4 | `/community` | BSD-2-Clause | https://github.com/Leaflet/Leaflet, loaded from `unpkg.com` |

## 5. Fonts (Google Fonts)

Loaded from `fonts.googleapis.com` and `fonts.gstatic.com`; not bundled.

| Family | Weights | Licence | Source |
|---|---|---|---|
| Inter | 400, 500, 600, 700 | SIL Open Font License 1.1 (Google Fonts metadata, `license: "OFL"`) | https://github.com/rsms/inter, https://fonts.google.com/specimen/Inter |
| JetBrains Mono | 400, 600 | SIL Open Font License 1.1 (Google Fonts metadata, `license: "OFL"`) | https://github.com/JetBrains/JetBrainsMono, https://fonts.google.com/specimen/JetBrains+Mono |

## 6. Data and imagery fetched at run time

None of this is in the repository. Whether and when each request is made is in [docs/TRANSPARENCY.md](docs/TRANSPARENCY.md);
this table is only about the terms of the data. Using a service is the station owner's responsibility under that service's terms.

| Service | Data | Terms (one line) |
|---|---|---|
| CelesTrak (`celestrak.org`) | Satellite orbital elements (TLE, GP data) for the station and visual groups | Free public data from T. S. Kelso; no formal licence text checked: **licence not verified**, keep requests rare (Zenivo asks at most once per 24 h) |
| SondeHub (`api.v2.sondehub.org`) | Radiosonde (weather balloon) positions | Community open-data API; **licence not verified**, terms are the SondeHub project's |
| OurAirports (`davidmegginson.github.io/ourairports-data`) | Airports and runways (CSV) | Stated by the project as public domain; **not re-verified here** |
| hexdb.io | Aircraft type and registration by ICAO hex, route by callsign | Free API without a key (site text); **licence not verified** |
| OpenFlights (`raw.githubusercontent.com/jpatokal/openflights`, `airlines.dat`) | Airline names and codes | Open Database License (ODbL) with the Database Contents License, per openflights.org/data.php (read 2026-11-14) |
| Flightradar24 (`data-cloud.flightradar24.com`, `data-live.flightradar24.com`, `www.flightradar24.com`) | Aircraft positions and flight details | Web endpoints, not an official public API. The feed is off by default. Enabling it is the station owner's responsibility under Flightradar24's terms; a subscription token is the supported way for account holders |
| Esri World Imagery (`server.arcgisonline.com`) | Satellite base map on `/flights3d` | Esri's terms and attribution apply; **licence not verified** |
| AWS Terrain Tiles (`s3.amazonaws.com/elevation-tiles-prod`) | Elevation tiles on `/flights3d` | Open data with required attributions of the underlying sources; **licence not verified** |
| Google Map Tiles (`tile.googleapis.com`) | Optional 2D tiles, only with your own Google Maps key | Google Maps Platform terms apply to your key |
| flagcdn.com, pics.avs.io | Country flags, airline logos | Third-party image services; **licence not verified** |
| ipwho.is, ipapi.co | Coordinates of your public IP, only when the admin presses "Locate via GPS / IP" | Third-party services under their own terms; **licence not verified** |
| Community seeds and other stations | Signed station cards | Project data, see [docs/COMMUNITY.md](docs/COMMUNITY.md) |
