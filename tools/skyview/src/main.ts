// AllSky glue: mounts the Sky view into #sky-root, with the station from /api/satellites.
import { createSky } from './index';
import { startAllskyBridge } from './allsky-bridge';

const base = (window as unknown as { SKY_BASE?: string }).SKY_BASE ?? '/static/skydata/';
const q = new URLSearchParams(location.search);
const n = (k: string): number | undefined => { const v = q.get(k); const x = v === null || v === '' ? NaN : Number(v); return Number.isFinite(x) ? x : undefined; };

void (async () => {
  const host = document.getElementById('sky-root')!;
  try {
    const t = q.get('t');
    const sky = await createSky(host, {
      observer: { latDeg: n('lat') ?? 21.0285, lonDeg: n('lon') ?? 105.8542 }, dataBaseUrl: base,
      startMs: t && !Number.isNaN(Date.parse(t)) ? Date.parse(t) : undefined, storagePrefix: 'allsky.sky.',
    });
    startAllskyBridge(sky.core);
    if (n('az') !== undefined || n('alt') !== undefined || n('fov') !== undefined) sky.core.goTo(n('az') ?? 0, n('alt') ?? 89.9, n('fov') ?? 150);
    const m = q.get('mode'); if (m === '3d' || m === 'eye') void sky.setMode(m);
    (window as unknown as { __sky: unknown }).__sky = sky;
  } catch (e) {
    host.textContent = 'Sky view failed: ' + (e instanceof Error ? e.message : String(e)); console.error(e);
  }
})();
