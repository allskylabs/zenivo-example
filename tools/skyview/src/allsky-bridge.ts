// The only file that talks to the AllSky backend: station, camera FOV, satellites, aircraft and detected sky events.
import type { SkyCore } from './engine-adapter';
import type { Marker } from './types';

type Raw = Record<string, unknown>;
const getJson = async <T>(url: string): Promise<T | null> => { try { const r = await fetch(url, { cache: 'no-store' }); return r.ok ? await r.json() as T : null; } catch { return null; } };
const num = (v: unknown): number | undefined => { const n = Number(v); return v !== null && v !== undefined && v !== '' && Number.isFinite(n) ? n : undefined; };

function track(prev: Marker[], raw: Raw[], build: (r: Raw, i: number) => Marker | null): Marker[] {
  const now = performance.now(); const old = new Map(prev.map(m => [m.id, m]));
  const out: Marker[] = [];
  raw.forEach((r, i) => {
    const m = build(r, i); if (!m || m.alt < 0) return;
    const p = old.get(m.id);
    if (p?.t !== undefined && now - p.t > 300) {
      let d = m.az - p.az; if (d > 180) d -= 360; else if (d < -180) d += 360;
      const vaz = d / (now - p.t), valt = (m.alt - p.alt) / (now - p.t);
      if (Math.hypot(vaz, valt) < 0.006) { m.vaz = vaz; m.valt = valt; }   // deg/ms; > 6 deg/s is a mismatch, not motion
    }
    m.t = now; out.push(m);
  });
  return out;
}

export function startAllskyBridge(core: SkyCore, api = ''): () => void {
  const pollSats = async (): Promise<void> => {
    const d = await getJson<{ station?: { lat?: number; lon?: number }; camera?: Record<string, number | null> | null; satellites?: Raw[] }>(`${api}/api/satellites?limit=60`);
    if (!d) return;
    if (d.station && num(d.station.lat) !== undefined && num(d.station.lon) !== undefined) core.setObserver({ latDeg: d.station.lat!, lonDeg: d.station.lon! });
    const c = d.camera;
    if (c && num(c.pointing_az_deg) !== undefined && num(c.pointing_alt_deg) !== undefined && (num(c.fov_horizontal_deg) || num(c.fov_vertical_deg))) {
      const fh = num(c.fov_horizontal_deg) ?? num(c.fov_vertical_deg)!, fv = num(c.fov_vertical_deg) ?? fh;
      core.setCamera({ az: c.pointing_az_deg!, alt: c.pointing_alt_deg!, roll: num(c.roll_deg) ?? 0, fovH: fh, fovV: fv, label: 'IMX335' });
    } else core.setCamera(null);
    core.setMarkers('satellites', track(core.markers.satellites, d.satellites ?? [], (r, i) => {
      const az = num(r.az_deg), alt = num(r.alt_deg); if (az === undefined || alt === undefined) return null;
      const vis = Boolean(r.visible), sun = Boolean(r.sunlit);
      return { id: String(r.norad ?? r.norad_id ?? r.name ?? i), label: String(r.name ?? '?'), az, alt, color: vis ? '#22d3ee' : sun ? '#f59e0b' : '#6b7280', shape: 'square', showLabel: vis || sun,
        rows: [['Status', vis ? 'visible now' : sun ? 'sunlit, too faint or low' : "in Earth's shadow"]] };
    }));
  };
  const pollAir = async (): Promise<void> => {
    const d = await getJson<{ aircraft?: Raw[] }>(`${api}/api/adsb/aircraft`);
    if (!d) return;
    core.setMarkers('aircraft', track(core.markers.aircraft, (d.aircraft ?? []).filter(a => a.above_horizon !== false), (r, i) => {
      const az = num(r.az_deg), alt = num(r.alt_deg); if (az === undefined || alt === undefined) return null;
      const name = String(r.callsign || r.icao || r.hex || '?').trim(); const am = num(r.altitude_m);
      return { id: String(r.icao ?? r.hex ?? r.callsign ?? i), label: name, az, alt, color: r.in_view ? '#fbbf24' : '#f472b6', shape: 'plane',
        rows: [['Altitude', am !== undefined ? `${Math.round(am)} m` : 'n/a'], ['In camera view', r.in_view ? 'yes' : 'no']] };
    }));
  };
  const pollEvents = async (): Promise<void> => {
    const since = Math.floor(Date.now() / 1000) - 2 * 3600;
    // full (non-brief) records: only those carry the sky_from / sky_to az-alt of a streak
    const d = await getJson<{ events?: Raw[] }>(`${api}/api/events?limit=80&since=${since}`);
    if (!d) return;
    const list: Marker[] = [];
    for (const e of d.events ?? []) {
      const f = e.sky_from as Raw | undefined, t = e.sky_to as Raw | undefined;
      const az = num(f?.az_deg), alt = num(f?.alt_deg); if (az === undefined || alt === undefined) continue;
      const type = String(e.type ?? 'event'); const flight = (e.nearest_flight as Raw | undefined)?.callsign;
      const ts = num(e.start_ts) ?? num(e.ts) ?? 0;
      list.push({ id: String(e.event_id ?? ts), label: type + (flight ? ' ' + String(flight) : ''), az, alt, az2: num(t?.az_deg), alt2: num(t?.alt_deg), ts, color: type === 'meteor' ? '#67e8f9' : type === 'plane' ? '#f472b6' : '#a3e635', shape: 'dot',
        rows: [['Type', type], ['Time', new Date(ts * 1000).toISOString().slice(11, 19) + ' UTC'], ...(flight ? [['Flight', String(flight)] as [string, string]] : [])] });
    }
    core.setMarkers('events', list);
  };
  // The camera picture shown inside the camera frame: the live snapshot when the sky time is "now", else the archived frame nearest that moment
  let imgKey = '', imgBusy = false, imgTimer = 0;
  const pollImage = async (): Promise<void> => {
    if (imgBusy || !core.camera || !core.layers.cameraImage || !core.layers.camera) return;
    imgBusy = true;
    try {
      let url: string | null = null, key = '';
      if (core.isNow) { url = `${api}/api/snapshot.jpg?ts=${Math.floor(Date.now() / 5000)}`; key = url; }
      else {
        const d = await getJson<{ url?: string; delta_s?: number }>(`${api}/api/frames/near?ts=${(core.simMs / 1000).toFixed(0)}`);
        if (d?.url && Math.abs(d.delta_s ?? 0) < 300) { url = api + d.url; key = url; }
      }
      if (key === imgKey) return;
      if (!url) { imgKey = ''; core.setCameraImage(null); return; }
      await new Promise<void>(res => { const im = new Image(); im.onload = () => { core.setCameraImage(im); imgKey = key; res(); }; im.onerror = () => res(); im.src = url!; });
    } finally { imgBusy = false; }
  };
  core.on('change', () => { window.clearTimeout(imgTimer); imgTimer = window.setTimeout(() => void pollImage(), 500); });
  const ids = [window.setInterval(() => { if (!document.hidden) void pollImage(); }, 5000), window.setInterval(() => { if (!document.hidden) void pollSats(); }, 5000), window.setInterval(() => { if (!document.hidden) void pollAir(); }, 2000), window.setInterval(() => { if (!document.hidden) void pollEvents(); }, 60000)];
  void pollSats(); void pollAir(); void pollEvents(); window.setTimeout(() => void pollImage(), 1500);
  return () => ids.forEach(clearInterval);
}
