# skyview

Source of the planetarium module used by the `/sky` page: WebGL star tiles and Milky Way, 2D overlay layers,
a 3D dome (three.js, loaded lazily), time controller, object inspector, English and Vietnamese labels.

## What ships, and what does not build yet

The **committed bundle in `src/web/sky/`** (`sky.js`, `core-*.js`, `dome3d-*.js`) is what Zenivo serves and runs.
You do not need Node or anything in this directory to run, test or deploy Zenivo.

This directory holds the TypeScript sources of that bundle, but they import three things that are **not part of
this repository**:

- `@astrophoto/sky-engine` and `@astrophoto/sky-data` (star-tile engine and data helpers),
- `astronomy-engine` and `three` (installed from npm in a sibling checkout).

`sky-engine` and `sky-data` live in the author's other repositories, which are **not published yet**. Until they
are, the bundle cannot be rebuilt from a plain clone of Zenivo; edits to the TypeScript here cannot be compiled
by outside contributors. Once those repositories are public, this README will point to them.

## How the build works when the dependencies are available

`vite.config.mjs` expects two sibling checkouts next to the Zenivo checkout (relative to this directory:
`../../../astrophoto/` with `node_modules` and `../../../hoan.uk/client/node_modules/` for `three`). It aliases
the packages to those checkouts, stubs `satellite.js` (TLE propagation is done by the backend, so the engine's
satellite helpers are never called) and writes the bundle into `src/web/sky/`.

```bash
cd tools/skyview
NODE_OPTIONS=--max-old-space-size=512 timeout -k 5 200 ../../../astrophoto/node_modules/.bin/vite build --config vite.config.mjs
NODE_OPTIONS=--max-old-space-size=512 timeout -k 5 150 ../../../astrophoto/node_modules/.bin/tsc -p .   # typecheck
```

The star, constellation and Milky Way data in `src/web/skydata/` was copied from the same sibling checkout
(`stars/`, `catalog/constellations.json`, `catalog/stars_named.json`, `catalog/milkyway.avif` and `.png`) plus
`catalog/messier.json` (110 Messier rows). `milkyway.avif` is the NASA SVS Deep Star Maps 2020 galactic map
(public domain) with the stars median-filtered out. See [THIRD_PARTY.md](../../THIRD_PARTY.md) for sources and licences.

## Module API (portable; only `allsky-bridge.ts` and `main.ts` are Zenivo specific)

```ts
import { createSky } from './index';
const sky = await createSky(containerEl, {
  observer: { latDeg, lonDeg, heightM? },
  dataBaseUrl: '/static/skydata/',   // trailing slash: stars/index.json, stars/o{n}/{pix}.stl, catalog/*.json, catalog/milkyway.avif (+ .png fallback)
  camera?: { az, alt, roll, fovH, fovV, label? },   // optional FOV frame
  lang?: 'en' | 'vi',                // default from navigator.language
  storagePrefix?: 'skyview.',        // localStorage keys (layers, lang)
  startMs?: number,                  // fixed start time; omit for live
});
sky.core.setObserver({ latDeg, lonDeg });  sky.core.setCamera(cam | null);
sky.core.setMarkers('aircraft' | 'satellites' | 'events', Marker[]);  // az/alt in degrees; optional vaz/valt (deg/ms) + t=performance.now() to extrapolate between updates
sky.core.setLayer(key, bool);  sky.core.setTime(ms);  sky.core.goNow();  sky.core.goTo(az, alt, fovDeg?);
sky.setMode('allsky' | 'eye' | '3d');   // 'eye' = Observer view: opaque ground, horizon, N/E/S/W, human FOV 30..110 deg; sky.destroy();
```

The container needs a size (the module fills it). Styles are injected, scoped under `.sv-root`, themed by
`--bg-card --border-color --text-primary --text-secondary --accent` with fallbacks.

Files: `engine-adapter.ts` (SkyCore: engine, time, view, picking, inspector), `layers.ts` (overlay layers, shared by
2D and dome through a projector), `ui.ts`, `dome3d.ts` (lazy chunk), `meteors.ts`, `astro.ts`, `allsky-bridge.ts`
(the only `/api/*` caller).
