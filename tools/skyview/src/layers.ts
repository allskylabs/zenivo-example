// 2D overlay layers drawn on top of the WebGL sky (All-sky) or of the three.js dome (via a different projector).
import { horizontalDir, mat3MulVec, radecToVec, raLine, decCircle, eclipticLine } from '@astrophoto/sky-engine';
import type { Vec3 } from '@astrophoto/sky-engine';
import type { SkyCore } from './engine-adapter';
import { CALENDAR_POINTS, DEG, OBLIQUITY } from './astro';
import type { Marker } from './types';

export interface Proj {
  w: number; h: number; fovDeg: number;
  /** hide everything below the horizon (opaque ground) */
  clipBelow: boolean;
  /** horizontal-frame unit vector -> pixels, null when not visible */
  project(d: Vec3): { x: number; y: number } | null;
  pxPerRad: number;
  /** the 3D dome draws its own sky/haze */
  noBackdrop?: boolean;
}
type P = { x: number; y: number };

const FONT = '"JetBrains Mono", ui-monospace, monospace';
const SEASON_COL = ['#86efac', '#fde047', '#fdba74', '#93c5fd'];

export function drawLayers(core: SkyCore, ctx: CanvasRenderingContext2D, proj: Proj): void {
  const { snap, layers: L } = core;
  const M = snap.M;
  const hits = core.hits = [] as typeof core.hits;
  const { w, h } = proj;
  const on = (p: P | null, m = 30): p is P => p !== null && p.x > -m && p.y > -m && p.x < w + m && p.y < h + m;
  ctx.lineJoin = 'round'; ctx.lineCap = 'round';
  const text = (s: string, x: number, y: number, color: string, align: CanvasTextAlign = 'left', size = 12, bold = false): void => {
    ctx.font = `${bold ? '600 ' : ''}${size}px ${FONT}`; ctx.textAlign = align; ctx.textBaseline = 'middle';
    ctx.lineWidth = 3; ctx.strokeStyle = 'rgba(0,0,10,0.75)'; ctx.strokeText(s, x, y); ctx.fillStyle = color; ctx.fillText(s, x, y);
  };
  const eqv = (ra: number, dec: number): Vec3 => mat3MulVec(M, radecToVec({ raRad: ra * DEG, decRad: dec * DEG }));
  /** polyline of vectors (horizontal frame unless eq=true), split at invisible points / long jumps */
  const poly = (pts: Vec3[], eq: boolean, clip = proj.clipBelow): void => {
    let open = false, lx = 0, ly = 0;
    ctx.beginPath();
    for (const q of pts) {
      const d = eq ? mat3MulVec(M, q) : q;
      const p = clip && d[2] < -0.01 ? null : proj.project(d);
      if (!p) { open = false; continue; }
      if (open && Math.hypot(p.x - lx, p.y - ly) > 1.2 * Math.max(w, h)) open = false;
      if (open) ctx.lineTo(p.x, p.y); else { ctx.moveTo(p.x, p.y); open = true; }
      lx = p.x; ly = p.y;
    }
    ctx.stroke();
  };
  const fov = proj.fovDeg;

  // backdrop: daylight / twilight tint
  if (L.daylightAtmosphere && !proj.noBackdrop && snap.sunAlt > -18) {
    const s = snap.sunAlt;
    const [r, g, b, a] = s > 0 ? [90, 150, 220, Math.min(0.88, 0.55 + s / 40)] : s > -6 ? [200, 120, 80, 0.45 + 0.1 * (s / 6)] : s > -12 ? [40, 50, 110, 0.32 * (s + 12) / 6 + 0.1] : [15, 20, 60, 0.2 * (s + 18) / 6];
    ctx.fillStyle = `rgba(${r},${g},${b},${a})`; ctx.fillRect(0, 0, w, h);
  }

  // grids
  if (L.altAzGrid) {
    ctx.strokeStyle = 'rgba(160,190,255,0.16)'; ctx.lineWidth = 1;
    for (const alt of [15, 30, 45, 60, 75]) poly(Array.from({ length: 121 }, (_, i) => horizontalDir(i * 3 * DEG, alt * DEG)), false);
    for (let az = 0; az < 360; az += 30) poly(Array.from({ length: 31 }, (_, i) => horizontalDir(az * DEG, i * 3 * DEG)), false);
  }
  if (L.raGrid) { ctx.strokeStyle = 'rgba(244,114,182,0.22)'; ctx.lineWidth = 1; for (let ra = 0; ra < 360; ra += 30) poly(raLine(ra, 3), true); }
  if (L.decGrid) { ctx.strokeStyle = 'rgba(94,234,212,0.22)'; ctx.lineWidth = 1; for (const d of [-60, -30, 30, 60]) poly(decCircle(d, 3), true); }
  if (L.equator) { ctx.strokeStyle = 'rgba(94,234,212,0.7)'; ctx.lineWidth = 1.5; poly(decCircle(0, 2), true); }
  if (L.ecliptic) {
    const e = eclipticLine(2); const n = e.length - 1;
    for (let q = 0; q < 4; q++) { ctx.strokeStyle = SEASON_COL[q]!; ctx.globalAlpha = 0.7; ctx.lineWidth = 1.5; poly(e.slice(Math.floor(q * n / 4), Math.ceil((q + 1) * n / 4) + 1), true); }
    ctx.globalAlpha = 1;
  }
  if (L.meridian) {
    ctx.strokeStyle = 'rgba(253,224,71,0.45)'; ctx.lineWidth = 1.2;
    poly([...Array.from({ length: 61 }, (_, i) => horizontalDir(0, i * 3 * DEG)), ...Array.from({ length: 61 }, (_, i) => horizontalDir(180 * DEG, (180 - i * 3) * DEG))], false);
  }
  // horizon + compass
  ctx.strokeStyle = 'rgba(52,211,153,0.75)'; ctx.lineWidth = 1.5;
  poly(Array.from({ length: 121 }, (_, i) => horizontalDir(i * 3 * DEG, 0)), false, false);
  if (L.cardinalDirections) {
    ['N', 'E', 'S', 'W'].forEach((c, i) => { const p = proj.project(horizontalDir(i * 90 * DEG, 0.5 * DEG)); if (on(p, 0)) text(c, p.x, p.y - 11, '#6ee7b7', 'center', 14, true); });
  }
  // constellations
  if (L.constellationLines && core.conVecs.length) { ctx.strokeStyle = 'rgba(110,160,255,0.5)'; ctx.lineWidth = 1; for (const c of core.conVecs) for (const l of c) poly(l, true); }
  if ((L.constellationLabels || L.constellationLines) && core.constellations.length && fov > 12) {
    for (const c of core.constellations) for (const lb of c.labels) {
      const d = eqv(lb[0], lb[1]); if (proj.clipBelow && d[2] < 0.02) continue;
      const p = proj.project(d); if (!on(p, -10)) continue;
      if (L.constellationLabels) text(core.nameOf(c.names).toUpperCase(), p.x, p.y, 'rgba(150,185,255,0.8)', 'center', 11);
      hits.push({ x: p.x, y: p.y, r: 34, kind: 'con', id: c.id, prio: 0, data: { con: c } });
    }
  }
  // named stars
  if (core.starVecs.length && (L.starLabels || L.stars)) {
    const lim = fov > 110 ? 1.6 : fov > 70 ? 2.3 : fov > 35 ? 3.2 : 5;
    for (let i = 0; i < core.stars.length; i++) {
      const s = core.stars[i]!; if (s.mag > lim) break;
      const d = mat3MulVec(M, core.starVecs[i]!); if (proj.clipBelow && d[2] < 0.02) continue;
      const p = proj.project(d); if (!on(p, -4)) continue;
      if (L.starLabels) text(core.nameOf(s), p.x + 7, p.y - 6, 'rgba(255,240,200,0.9)', 'left', 11);
      hits.push({ x: p.x, y: p.y, r: 14, kind: 'star', id: String(s.hip ?? s.en), prio: 2, data: { star: s } });
    }
  }
  // Messier
  if (L.deepSkyObjects && core.messier.length) {
    const lim = fov > 100 ? 6 : fov > 50 ? 8 : 12;
    core.messier.forEach((m, i) => {
      if (m.mag > lim) return;
      const d = mat3MulVec(M, core.messierVecs[i]!); if (proj.clipBelow && d[2] < 0.02) return;
      const p = proj.project(d); if (!on(p, -4)) return;
      ctx.strokeStyle = '#c4b5fd'; ctx.lineWidth = 1.2; ctx.beginPath(); ctx.arc(p.x, p.y, 5, 0, 7); ctx.stroke();
      if (fov < 110) text(m.m, p.x + 8, p.y + 6, '#c4b5fd', 'left', 10);
      hits.push({ x: p.x, y: p.y, r: 14, kind: 'dso', id: m.m, prio: 2, data: { m } });
    });
  }
  // equinox / solstice / cross-quarter points
  if (L.calendarPoints) {
    for (const c of CALENDAR_POINTS) {
      const d = eqv(c.raDeg, c.decDeg); if (proj.clipBelow && d[2] < 0) continue;
      const p = proj.project(d); if (!on(p, -4)) continue;
      ctx.fillStyle = c.major ? '#fde047' : '#fdba74'; ctx.beginPath(); ctx.moveTo(p.x, p.y - 6); ctx.lineTo(p.x + 5, p.y); ctx.lineTo(p.x, p.y + 6); ctx.lineTo(p.x - 5, p.y); ctx.closePath(); ctx.fill();
      text(core.lang === 'vi' ? c.vi : c.en, p.x + 8, p.y, '#fde68a', 'left', 11);
      hits.push({ x: p.x, y: p.y, r: 14, kind: 'cal', id: c.id, prio: 1, data: { id: c.id } });
    }
  }
  // celestial poles / Earth polar axis
  if (L.polarAxis) {
    for (const [id, dec, en, vi] of [['ncp', 90, 'N celestial pole', 'Cực bắc thiên cầu'], ['scp', -90, 'S celestial pole', 'Cực nam thiên cầu']] as [string, number, string, string][]) {
      const d = eqv(0, dec); if (proj.clipBelow && d[2] < 0) continue;
      const p = proj.project(d); if (!on(p, -4)) continue;
      ctx.strokeStyle = '#f472b6'; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(p.x, p.y, 6, 0, 7); ctx.moveTo(p.x - 10, p.y); ctx.lineTo(p.x + 10, p.y); ctx.moveTo(p.x, p.y - 10); ctx.lineTo(p.x, p.y + 10); ctx.stroke();
      text(core.lang === 'vi' ? vi : en, p.x + 12, p.y, '#f9a8d4', 'left', 11);
      hits.push({ x: p.x, y: p.y, r: 16, kind: 'pole', id, prio: 1, data: { id } });
    }
  }
  // camera FOV
  if (core.camera && L.camera) drawCamera(core, ctx, proj, text, on, poly);

  // Sun, Moon, planets
  for (const b of snap.bodies) {
    const sm = b.id === 'sun' || b.id === 'moon';
    if (sm ? !L.moon : !L.planets) continue;
    const d = eqv(b.raDeg, b.decDeg); if (d[2] < -0.02 && proj.clipBelow) continue;
    const p = proj.project(d); if (!on(p, 10)) continue;
    const r = Math.max(sm ? 6 : 3.5, Math.min(40, (b.angularDiameterArcsec / 2 / 3600) * DEG * proj.pxPerRad));
    ctx.beginPath(); ctx.arc(p.x, p.y, r, 0, 7);
    ctx.fillStyle = b.id === 'sun' ? '#ffd24a' : b.id === 'moon' ? `rgba(230,232,238,${0.35 + 0.65 * b.illuminatedFraction})` : '#fde68a'; ctx.fill();
    if (b.id === 'moon') { ctx.strokeStyle = 'rgba(230,232,238,0.7)'; ctx.lineWidth = 1; ctx.stroke(); }
    text(core.lang === 'vi' ? b.names.i18n.vi ?? b.names.en : b.names.en, p.x + r + 5, p.y, b.id === 'sun' ? '#ffd24a' : '#fde68a', 'left', 12, true);
    hits.push({ x: p.x, y: p.y, r: Math.max(18, r + 6), kind: 'body', id: b.id, prio: 3, data: { id: b.id } });
  }

  // meteors + radiants of the active showers
  if (L.meteors) {
    for (const s of core.meteors.segments()) {
      const a = proj.project(mat3MulVec(M, s.a)), b = proj.project(mat3MulVec(M, s.b));
      if (!a || !b || mat3MulVec(M, s.b)[2] < 0 && proj.clipBelow) continue;
      const g = ctx.createLinearGradient(a.x, a.y, b.x, b.y);
      const c = s.rgb.map(v => Math.round((v * 0.8 + 0.2) * 255)).join(',');
      g.addColorStop(0, `rgba(${c},0)`); g.addColorStop(1, `rgba(${c},${s.alpha})`);
      ctx.strokeStyle = g; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
    }
    if (fov < 170) for (const r of core.meteors.radiants) {
      const d = mat3MulVec(M, r.v); if (d[2] < 0) continue;
      const p = proj.project(d); if (!on(p, -4)) continue;
      ctx.strokeStyle = 'rgba(103,232,249,0.9)'; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(p.x, p.y, 8, 0, 7); ctx.stroke();
      text(r.name, p.x + 11, p.y, '#67e8f9', 'left', 11, true);
    }
  }

  // host markers
  const now = core.isNow;
  const draw = (m: Marker, group: string): void => {
    const [az, alt] = core.markerPos(m); if (alt < 0 && proj.clipBelow) return;
    const p = proj.project(horizontalDir(az * DEG, alt * DEG)); if (!on(p, 6)) return;
    ctx.fillStyle = m.color; ctx.strokeStyle = m.color;
    if (m.shape === 'plane') { ctx.beginPath(); ctx.moveTo(p.x, p.y - 5); ctx.lineTo(p.x + 4, p.y + 4); ctx.lineTo(p.x - 4, p.y + 4); ctx.closePath(); ctx.fill(); }
    else if (m.shape === 'square') ctx.fillRect(p.x - 2.5, p.y - 2.5, 5, 5);
    else { ctx.beginPath(); ctx.arc(p.x, p.y, 3, 0, 7); ctx.fill(); }
    if (m.showLabel !== false) text(m.label, p.x + 7, p.y - 5, m.color, 'left', 11);
    hits.push({ x: p.x, y: p.y, r: 18, kind: 'marker', id: group + ':' + m.id, prio: 3, data: { marker: m } });
  };
  if (L.satellites && now) for (const m of core.markers.satellites) draw(m, 's');
  if (L.aircraft && now) for (const m of core.markers.aircraft) draw(m, 'a');
  if (L.events) {
    const t = core.simMs / 1000;
    for (const m of core.markers.events) {
      const age = t - (m.ts ?? 0); if (age < 0 || age > 1800) continue;
      const a = proj.project(horizontalDir(m.az * DEG, m.alt * DEG));
      const b = m.az2 !== undefined ? proj.project(horizontalDir(m.az2 * DEG, (m.alt2 ?? 0) * DEG)) : null;
      ctx.globalAlpha = 1 - age / 1800 * 0.75; ctx.strokeStyle = m.color; ctx.lineWidth = 2.5;
      if (a && b) { ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke(); }
      ctx.globalAlpha = 1;
      if (a && on(a, 6)) { if (!b) { ctx.fillStyle = m.color; ctx.beginPath(); ctx.arc(a.x, a.y, 3, 0, 7); ctx.fill(); } text(m.label, a.x + 7, a.y - 5, m.color, 'left', 11); hits.push({ x: a.x, y: a.y, r: 20, kind: 'marker', id: 'e:' + m.id, prio: 3, data: { marker: m } }); }
    }
  }

  // selection ring
  const sel = core.selected ? hits.find(x => x.kind + ':' + x.id === core.selected) : undefined;
  if (sel) { ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(sel.x, sel.y, 12, 0, 7); ctx.stroke(); core.selectedHit = sel; }
  void OBLIQUITY;
}

function drawCamera(core: SkyCore, ctx: CanvasRenderingContext2D, proj: Proj, text: (s: string, x: number, y: number, c: string, a?: CanvasTextAlign, s2?: number, b?: boolean) => void, on: (p: P | null, m?: number) => boolean, poly: (pts: Vec3[], eq: boolean, clip?: boolean) => void): void {
  const c = core.camera!;
  const f = horizontalDir(c.az * DEG, c.alt * DEG);
  const r0: Vec3 = [-Math.sin(c.az * DEG), -Math.cos(c.az * DEG), 0];
  const u0: Vec3 = [r0[1] * f[2] - r0[2] * f[1], r0[2] * f[0] - r0[0] * f[2], r0[0] * f[1] - r0[1] * f[0]];
  const cr = Math.cos(c.roll * DEG), sr = Math.sin(c.roll * DEG);
  const r: Vec3 = [r0[0] * cr + u0[0] * sr, r0[1] * cr + u0[1] * sr, r0[2] * cr + u0[2] * sr];
  const u: Vec3 = [u0[0] * cr - r0[0] * sr, u0[1] * cr - r0[1] * sr, u0[2] * cr - r0[2] * sr];
  const hx = Math.tan(Math.min(85, c.fovH / 2) * DEG), hy = Math.tan(Math.min(85, c.fovV / 2) * DEG);
  const at = (x: number, y: number): Vec3 => [f[0] + x * hx * r[0] + y * hy * u[0], f[1] + x * hx * r[1] + y * hy * u[1], f[2] + x * hx * r[2] + y * hy * u[2]];
  const N = 24, pts: Vec3[] = [];
  for (let i = 0; i <= N; i++) pts.push(at(-1 + 2 * i / N, 1));
  for (let i = 0; i <= N; i++) pts.push(at(1, 1 - 2 * i / N));
  for (let i = 0; i <= N; i++) pts.push(at(1 - 2 * i / N, -1));
  for (let i = 0; i <= N; i++) pts.push(at(-1, -1 + 2 * i / N));
  ctx.strokeStyle = '#fb923c'; ctx.lineWidth = 2; poly(pts, false, false);
  const pc = proj.project(f);
  if (on(pc, 0) && pc) { ctx.lineWidth = 1.5; ctx.beginPath(); ctx.moveTo(pc.x - 8, pc.y); ctx.lineTo(pc.x + 8, pc.y); ctx.moveTo(pc.x, pc.y - 8); ctx.lineTo(pc.x, pc.y + 8); ctx.stroke(); }
  const pl = proj.project(at(-1, 1)) ?? pc;
  if (pl && on(pl, 0)) text(c.label ?? 'IMX335', pl.x + 6, pl.y + 12, '#fdba74', 'left', 12, true);
}
