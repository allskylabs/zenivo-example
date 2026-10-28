# skyview

Planetarium module: WebGL star tiles + Milky Way (astrophoto sky-engine), 2D overlay layers, 3D dome (three.js, lazy), time controller, object inspector, en/vi.

Build (needs sibling checkouts `../../../astrophoto` and `../../../hoan.uk/client` with node_modules; output is committed in `src/web/sky/`):
`cd tools/skyview && NODE_OPTIONS=--max-old-space-size=512 timeout -k 5 200 ../../../astrophoto/node_modules/.bin/vite build --config vite.config.mjs`
Typecheck: `NODE_OPTIONS=--max-old-space-size=512 timeout -k 5 150 ../../../astrophoto/node_modules/.bin/tsc -p .`
Data in `src/web/skydata/` is copied from astrophoto/data/build (stars/, catalog/{constellations,stars_named}.json, milkyway.png) plus catalog/messier.json (110 Messier rows extracted from dso.json).

## Module API (portable; only `allsky-bridge.ts` and `main.ts` are AllSky specific)
```ts
import { createSky } from './index';
const sky = await createSky(containerEl, {
  observer: { latDeg, lonDeg, heightM? },
  dataBaseUrl: '/static/skydata/',   // trailing slash: stars/index.json, stars/o{n}/{pix}.stl, catalog/*.json, catalog/milkyway.png
  camera?: { az, alt, roll, fovH, fovV, label? },   // optional FOV frame
  lang?: 'en' | 'vi',                // default from navigator.language
  storagePrefix?: 'skyview.',        // localStorage keys (layers, lang)
  startMs?: number,                  // fixed start time; omit for live
});
sky.core.setObserver({ latDeg, lonDeg });  sky.core.setCamera(cam | null);
sky.core.setMarkers('aircraft' | 'satellites' | 'events', Marker[]);  // az/alt in degrees; optional vaz/valt (deg/ms) + t=performance.now() to extrapolate between updates
sky.core.setLayer(key, bool);  sky.core.setTime(ms);  sky.core.goNow();  sky.core.goTo(az, alt, fovDeg?);
sky.setMode('allsky' | '3d');  sky.destroy();
```
The container needs a size (the module fills it). Styles are injected, scoped under `.sv-root`, themed by `--bg-card --border-color --text-primary --text-secondary --accent` with fallbacks.

Files: `engine-adapter.ts` (SkyCore: engine, time, view, picking, inspector), `layers.ts` (overlay layers, shared by 2D and dome through a projector), `ui.ts`, `dome3d.ts` (lazy chunk), `meteors.ts`, `astro.ts`, `allsky-bridge.ts` (the only `/api/*` caller).
`vite.config.mjs` stubs `satellite.js` (TLE propagation is done by the backend) and aliases `astronomy-engine` and `three` to the sibling checkouts.
