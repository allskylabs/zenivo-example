// SkyCore: wraps the astrophoto sky-engine (WebGL star tiles + Milky Way) with time, view, catalogues, picking and markers.
// It never calls a backend: hosts feed observer / camera / markers in (see allsky-bridge.ts).
import {
  SkyView, julianDate, project, panView, zoomView, autoProjection, clampFov, unproject, radecToVec, mat3MulVec,
  equatorialJ2000ToHorizontalMatrix, horizontalDir, solarSystem,
} from '@astrophoto/sky-engine';
import type { ViewState, SolarBody, Vec3, Mat3, GrayImage } from '@astrophoto/sky-engine';
import type { StarIndex } from '@astrophoto/sky-data';
import { drawLayers } from './layers';
import type { Proj } from './layers';
import { MeteorSim } from './meteors';
import { bodyDistanceAu, compassOf, constellationOf, CON_FACTS, DEG, eclipticLon, moonPhase, CALENDAR_POINTS, solarTermDates } from './astro';
import { LAYER_DEFAULTS, LAYER_KEYS } from './types';
import type { CameraFov, Hit, InspectorData, Lang, LayerKey, Layers, Marker, MarkerGroup, ObserverCfg, SkyConfig, ViewMode } from './types';

export interface Constellation { id: string; names: { en: string; i18n?: Record<string, string> }; labels: [number, number][]; lines: [number, number][][] }
export interface NamedStar { en: string; i18n?: Record<string, string>; ra: number; dec: number; mag: number; hip?: number }
export interface Messier { m: string; ra: number; dec: number; mag: number; type: string; name?: string }
export interface Snapshot { jd: number; date: Date; M: Mat3; bodies: SolarBody[]; sunAlt: number }

export class SkyCore {
  readonly gl: HTMLCanvasElement;
  readonly ov: HTMLCanvasElement;
  readonly cfg: SkyConfig;
  sky!: SkyView;
  observer: ObserverCfg;
  camera: CameraFov | null;
  layers: Layers;
  lang: Lang;
  mode: ViewMode = 'allsky';
  // time
  simMs: number; live: boolean; speed = 1;
  private lastReal = performance.now();
  // data
  constellations: Constellation[] = []; conVecs: Vec3[][][] = [];
  stars: NamedStar[] = []; starVecs: Vec3[] = [];
  messier: Messier[] = []; messierVecs: Vec3[] = [];
  milkyImg: GrayImage | null = null;
  meteors = new MeteorSim();
  private meteorDay = -1;
  // state
  markers: Record<MarkerGroup, Marker[]> = { aircraft: [], satellites: [], events: [] };
  hits: Hit[] = [];
  selected: string | null = null;
  snap!: Snapshot;
  dirty = true;
  /** extra renderer for the 3D dome: when set, the 2D engine is paused and this is ticked instead */
  external: ((dt: number, force: boolean) => void) | null = null;
  private listeners: Record<string, Set<() => void>> = { change: new Set(), select: new Set() };
  private bodiesAt = -1e15; private bodiesCache: SolarBody[] = [];
  private lastDraw = 0;
  private prefix: string;
  private raf = 0;

  constructor(gl: HTMLCanvasElement, ov: HTMLCanvasElement, cfg: SkyConfig) {
    this.gl = gl; this.ov = ov; this.cfg = cfg;
    this.observer = { heightM: 0, ...cfg.observer };
    this.camera = cfg.camera ?? null;
    this.prefix = cfg.storagePrefix ?? 'skyview.';
    this.simMs = cfg.startMs ?? Date.now(); this.live = cfg.startMs === undefined;
    this.layers = { ...LAYER_DEFAULTS };
    let lang: Lang = cfg.lang ?? ((navigator.language || 'en').toLowerCase().startsWith('vi') ? 'vi' : 'en');
    try {
      const s = JSON.parse(localStorage.getItem(this.prefix + 'layers') || '{}') as Record<string, boolean>;
      for (const k of LAYER_KEYS) if (typeof s[k] === 'boolean') this.layers[k] = s[k]!;
      const l = localStorage.getItem(this.prefix + 'lang'); if (l === 'en' || l === 'vi') lang = cfg.lang ?? l;
    } catch { /* storage unavailable */ }
    this.lang = lang;
    this.refreshSnapshot();
  }

  // ------------------------------------------------------------ events
  on(ev: 'change' | 'select', fn: () => void): void { this.listeners[ev]!.add(fn); }
  private emit(ev: 'change' | 'select'): void { this.listeners[ev]!.forEach(f => f()); }
  t(en: string, vi: string): string { return this.lang === 'vi' ? vi : en; }
  nameOf(n: { en: string; i18n?: Record<string, string> }): string { return (this.lang === 'vi' && n.i18n?.vi) || n.en; }

  // ------------------------------------------------------------ init
  async init(): Promise<void> {
    const base = this.cfg.dataBaseUrl;
    const index = await (await fetch(base + 'stars/index.json')).json() as StarIndex;
    this.sky = new SkyView(this.gl, index, async (order, pix) => {
      const r = await fetch(`${base}stars/o${order}/${pix}.stl`);
      if (r.status === 404) return null;
      if (!r.ok) throw new Error(`tile ${order}/${pix}: HTTP ${r.status}`);
      return r.arrayBuffer();
    });
    this.sky.observer.latDeg = this.observer.latDeg; this.sky.observer.lonDeg = this.observer.lonDeg;
    this.allSkyView();
    const j = async <T>(u: string): Promise<T | null> => { try { const r = await fetch(base + u); return r.ok ? await r.json() as T : null; } catch { return null; } };
    void j<{ constellations: Constellation[] }>('catalog/constellations.json').then(c => {
      if (!c) return;
      this.constellations = c.constellations;
      this.conVecs = c.constellations.map(k => k.lines.map(l => l.map(p => radecToVec({ raRad: p[0] * DEG, decRad: p[1] * DEG }))));
      this.dirty = true;
    });
    void j<NamedStar[]>('catalog/stars_named.json').then(s => {
      if (!s) return;
      this.stars = s.sort((a, b) => a.mag - b.mag);
      this.starVecs = this.stars.map(p => radecToVec({ raRad: p.ra * DEG, decRad: p.dec * DEG }));
      this.dirty = true;
    });
    void j<Messier[]>('catalog/messier.json').then(s => {
      if (!s) return;
      this.messier = s; this.messierVecs = s.map(p => radecToVec({ raRad: p.ra * DEG, decRad: p.dec * DEG })); this.dirty = true;
    });
    void this.loadMilky(base).then(img => { this.milkyImg = img; this.applyMilky(); this.dirty = true; this.emit('change'); });
    this.applyMilky();
    this.attachPointer();
    new ResizeObserver(() => { this.dirty = true; }).observe(this.gl);
  }

  private async loadMilky(base: string): Promise<GrayImage | null> {
    try {
      const r = await fetch(base + 'catalog/milkyway.png');
      if (!r.ok) return null;
      const bmp = await createImageBitmap(await r.blob(), { colorSpaceConversion: 'none', premultiplyAlpha: 'none' });
      const c = document.createElement('canvas'); c.width = bmp.width; c.height = bmp.height;
      const x = c.getContext('2d'); if (!x) return null;
      x.drawImage(bmp, 0, 0);
      const { width, height } = bmp; const px = x.getImageData(0, 0, width, height).data;
      const data = new Uint8Array(width * height);
      for (let i = 0; i < data.length; i++) data[i] = px[4 * i]!;
      return { width, height, data };
    } catch { return null; }
  }
  private applyMilky(): void { this.sky?.setMilkyWay(this.layers.milkyWay ? this.milkyImg : null); }

  // ------------------------------------------------------------ state setters
  setLayer(k: LayerKey, on: boolean): void {
    this.layers[k] = on;
    if (k === 'milkyWay') this.applyMilky();
    if (k === 'stars') this.gl.style.visibility = on || this.mode === '3d' ? 'visible' : 'hidden';
    try { localStorage.setItem(this.prefix + 'layers', JSON.stringify(this.layers)); } catch { /* ignore */ }
    this.dirty = true; this.emit('change');
  }
  setLang(l: Lang): void { this.lang = l; try { localStorage.setItem(this.prefix + 'lang', l); } catch { /* ignore */ } this.dirty = true; this.emit('change'); }
  setObserver(o: ObserverCfg): void {
    if (o.latDeg === this.observer.latDeg && o.lonDeg === this.observer.lonDeg) return;
    this.observer = { heightM: 0, ...o };
    if (this.sky) { this.sky.observer.latDeg = o.latDeg; this.sky.observer.lonDeg = o.lonDeg; }
    this.bodiesAt = -1e15; this.dirty = true; this.emit('change');
  }
  setCamera(c: CameraFov | null): void { this.camera = c; this.dirty = true; this.emit('change'); }
  setMarkers(g: MarkerGroup, list: Marker[]): void { this.markers[g] = list; this.dirty = true; }
  setMode(m: ViewMode): void { this.mode = m; this.dirty = true; this.emit('change'); }

  // time
  setTime(ms: number): void { this.simMs = ms; this.live = false; this.dirty = true; this.emit('change'); }
  shiftTime(ms: number): void { this.setTime(this.simMs + ms); }
  goNow(): void { this.live = true; this.speed = 1; this.simMs = Date.now(); this.dirty = true; this.emit('change'); }
  setSpeed(s: number): void { this.speed = s; if (this.live && s !== 1) this.live = false; this.emit('change'); }
  /** true when the shown time is within 30 s of the wall clock (live host data is meaningful) */
  get isNow(): boolean { return Math.abs(this.simMs - Date.now()) < 30000; }

  // ------------------------------------------------------------ view
  private setFov(v: ViewState, fovRad: number): ViewState { const k = autoProjection(fovRad); return { ...v, fovRad: clampFov(k, fovRad), projection: k }; }
  allSkyView(): void { this.sky.view = this.setFov({ ...this.sky.view, azRad: 0, altRad: 89.9 * DEG, rollRad: 0 }, 150 * DEG); this.dirty = true; }
  goTo(azDeg: number, altDeg: number, fovDeg?: number, rollDeg = 0): void {
    this.sky.view = { ...this.sky.view, azRad: azDeg * DEG, altRad: Math.max(-89.9, Math.min(89.9, altDeg)) * DEG, rollRad: rollDeg * DEG };
    if (fovDeg) this.sky.view = this.setFov(this.sky.view, fovDeg * DEG);
    this.dirty = true;
  }
  cameraView(): void {
    const c = this.camera; if (!c) return;
    const w = Math.max(1, this.gl.clientWidth), h = Math.max(1, this.gl.clientHeight);
    // fov spans the shorter canvas side: make both frame dimensions fit
    const needV = (h <= w ? c.fovV : c.fovV * w / h) * 1.25, needH = (h <= w ? c.fovH * h / w : c.fovH) * 1.25;
    this.goTo(c.az, c.alt, Math.max(8, Math.min(170, Math.max(needV, needH))));
  }
  get fovDeg(): number { return this.sky ? this.sky.view.fovRad / DEG : 150; }

  // ------------------------------------------------------------ snapshot (positions of the Sun/Moon/planets)
  refreshSnapshot(): Snapshot {
    const date = new Date(this.simMs), jd = julianDate(date);
    const M = equatorialJ2000ToHorizontalMatrix(this.observer as never, jd);
    if (Math.abs(this.simMs - this.bodiesAt) > 4000) { this.bodiesCache = solarSystem(jd, this.observer as never); this.bodiesAt = this.simMs; }
    const sun = this.bodiesCache[0];
    const sunAlt = sun ? Math.asin(mat3MulVec(M, radecToVec({ raRad: sun.raDeg * DEG, decRad: sun.decDeg * DEG }))[2]) / DEG : -90;
    this.snap = { jd, date, M, bodies: this.bodiesCache, sunAlt };
    return this.snap;
  }
  azAltOfEq(raDeg: number, decDeg: number): [number, number] {
    const d = mat3MulVec(this.snap.M, radecToVec({ raRad: raDeg * DEG, decRad: decDeg * DEG }));
    return [((Math.atan2(-d[1], d[0]) / DEG) + 360) % 360, Math.asin(Math.max(-1, Math.min(1, d[2]))) / DEG];
  }

  // ------------------------------------------------------------ interaction (2D / phone)
  private pointers = new Map<number, [number, number]>();
  private pinch = 0; private downAt = 0; private moved = 0; private downPos: [number, number] = [0, 0];
  /** when set (phone finder with live sensors) one-finger drag does not pan */
  panEnabled = true;
  private rel(e: { clientX: number; clientY: number }): [number, number] { const r = this.gl.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; }
  private zoomAround(f: number, a: [number, number]): void {
    let nv = zoomView(this.sky.view, f, a);
    const kind = autoProjection(nv.fovRad);
    if (kind !== nv.projection) { const d = unproject(a[0], a[1], nv); nv = { ...nv, projection: kind }; const p = project(d, nv); if (p) nv = panView(nv, [p.x, p.y], a); }
    this.sky.view = nv; this.dirty = true;
  }
  private attachPointer(): void {
    const el = this.ov.parentElement ?? this.gl;
    el.addEventListener('pointerdown', e => {
      if (this.mode === '3d' || (e.target as HTMLElement).closest('.sv-ui')) return;
      el.setPointerCapture(e.pointerId); this.pointers.set(e.pointerId, this.rel(e));
      if (this.pointers.size === 1) { this.downAt = performance.now(); this.moved = 0; this.downPos = this.rel(e); }
      if (this.pointers.size === 2) { const [a, b] = [...this.pointers.values()]; this.pinch = Math.hypot(a![0] - b![0], a![1] - b![1]); this.moved = 99; }
    });
    el.addEventListener('pointermove', e => {
      const prev = this.pointers.get(e.pointerId); if (!prev) return;
      const cur = this.rel(e);
      if (this.pointers.size === 1) {
        this.moved = Math.max(this.moved, Math.hypot(cur[0] - this.downPos[0], cur[1] - this.downPos[1]));
        if (this.panEnabled) { this.sky.view = panView(this.sky.view, prev, cur); this.dirty = true; }
      } else if (this.pointers.size === 2) {
        this.pointers.set(e.pointerId, cur);
        const [a, b] = [...this.pointers.values()]; const d = Math.hypot(a![0] - b![0], a![1] - b![1]);
        if (this.pinch > 0 && d > 0) this.zoomAround(d / this.pinch, [(a![0] + b![0]) / 2, (a![1] + b![1]) / 2]);
        this.pinch = d;
      }
      this.pointers.set(e.pointerId, cur);
    });
    const up = (e: PointerEvent): void => {
      const was = this.pointers.size; this.pointers.delete(e.pointerId); this.pinch = 0;
      if (e.type === 'pointerup' && was === 1 && this.moved < 6 && performance.now() - this.downAt < 600) this.select(this.pick(this.rel(e)));
    };
    el.addEventListener('pointerup', up); el.addEventListener('pointercancel', up);
    el.addEventListener('wheel', e => { if (this.mode === '3d' || (e.target as HTMLElement).closest('.sv-ui')) return; e.preventDefault(); this.zoomAround(Math.exp(-e.deltaY * 0.0015), this.rel(e)); }, { passive: false });
  }
  pick(p: [number, number]): Hit | null {
    let best: Hit | null = null, bs = 1e9;
    for (const h of this.hits) { const d = Math.hypot(h.x - p[0], h.y - p[1]); if (d > h.r) continue; const s = d - h.prio * 6; if (s < bs) { bs = s; best = h; } }
    return best;
  }
  select(h: Hit | null): void { this.selected = h ? h.kind + ':' + h.id : null; this.selectedHit = h; this.dirty = true; this.emit('select'); }
  selectedHit: Hit | null = null;

  // ------------------------------------------------------------ inspector
  inspect(h: Hit): InspectorData {
    const d = h.data; const L = this.t.bind(this);
    const fmt = (v: number, n = 1): string => (v >= 0 ? '+' : '') + v.toFixed(n);
    const rows: [string, string][] = []; let title = String(d.name ?? h.id), kind = h.kind, az = 0, alt = 0, note: string | undefined;
    const eq = (ra: number, dec: number): void => {
      [az, alt] = this.azAltOfEq(ra, dec);
      rows.push([L('Altitude', 'Độ cao'), fmt(alt) + '°'], [L('Azimuth', 'Phương vị'), `${az.toFixed(1)}° ${compassOf(az)}`], [L('Right ascension', 'Xích kinh'), ra.toFixed(2) + '°'], [L('Declination', 'Xích vĩ'), fmt(dec, 2) + '°']);
    };
    const con = (ra: number, dec: number): void => { const c = constellationOf(ra, dec); if (c) rows.push([L('Constellation', 'Chòm sao'), c.name]); };
    if (h.kind === 'star') {
      const s = d.star as NamedStar; title = this.nameOf(s); kind = L('Star', 'Ngôi sao'); eq(s.ra, s.dec);
      rows.push([L('Magnitude (V)', 'Cấp sao biểu kiến'), fmt(s.mag, 2)]); con(s.ra, s.dec); rows.push([L('Ecliptic longitude (λ)', 'Kinh độ hoàng đạo (λ)'), eclipticLon(s.ra, s.dec).toFixed(2) + '°']);
    } else if (h.kind === 'body') {
      const b = this.snap.bodies.find(x => x.id === d.id)!; title = this.lang === 'vi' ? b.names.i18n.vi ?? b.names.en : b.names.en;
      kind = b.id === 'sun' ? L('Sun', 'Mặt Trời') : b.id === 'moon' ? L('Moon', 'Mặt Trăng') : L('Planet', 'Hành tinh'); eq(b.raDeg, b.decDeg);
      rows.push([L('Magnitude (V)', 'Cấp sao biểu kiến'), fmt(b.mag, 2)]);
      if (b.id !== 'sun') { const f = Math.round(b.illuminatedFraction * 100); const ph = b.id === 'moon' ? moonPhase(this.snap.date) : null; rows.push([L('Illumination', 'Chiếu sáng'), `${f}%` + (ph ? ' ' + (this.lang === 'vi' ? ph.vi : ph.en) : '')]); }
      const au = bodyDistanceAu(b.id, this.snap.date, this.observer); if (au !== null) rows.push([L('Distance', 'Khoảng cách'), au.toFixed(b.id === 'moon' ? 5 : 3) + ' AU']);
      con(b.raDeg, b.decDeg); rows.push([L('Ecliptic longitude (λ)', 'Kinh độ hoàng đạo (λ)'), eclipticLon(b.raDeg, b.decDeg).toFixed(2) + '°']);
    } else if (h.kind === 'dso') {
      const m = d.m as Messier; title = m.m + (m.name ? ' ' + m.name : ''); kind = L('Deep-sky object', 'Thiên thể sâu (Messier)'); eq(m.ra, m.dec);
      rows.push([L('Magnitude (V)', 'Cấp sao biểu kiến'), fmt(m.mag, 1)], [L('Type', 'Loại'), m.type]); con(m.ra, m.dec);
    } else if (h.kind === 'con') {
      const c = d.con as Constellation; title = this.nameOf(c.names); kind = L('IAU constellation', 'Chòm sao IAU'); eq(c.labels[0]![0], c.labels[0]![1]);
      rows.push([L('Constellation', 'Chòm sao'), c.id]);
      const f = CON_FACTS[c.id]; if (f) { rows.push([L('Brightest star', 'Sao sáng nhất'), f.star], [L('Best season', 'Mùa ngắm đẹp nhất'), this.lang === 'vi' ? f.seasonVi : f.seasonEn]); note = this.lang === 'vi' ? f.vi : f.en; }
    } else if (h.kind === 'cal') {
      const p = CALENDAR_POINTS.find(x => x.id === d.id)!; title = this.lang === 'vi' ? p.vi : p.en; kind = L('Astronomical marker', 'Điểm lịch thiên văn'); eq(p.raDeg, p.decDeg);
      rows.push([L('Ecliptic longitude (λ)', 'Kinh độ hoàng đạo (λ)'), p.lon + '°']);
      const dt = solarTermDates(this.snap.date.getUTCFullYear())[p.id]; if (dt) rows.push([L('Approx. date', 'Thời điểm'), dt.toISOString().slice(0, 16).replace('T', ' ') + ' UTC']);
    } else if (h.kind === 'pole') {
      const north = d.id === 'ncp'; title = north ? L('North celestial pole', 'Cực bắc thiên cầu') : L('South celestial pole', 'Cực nam thiên cầu'); kind = L('Earth polar axis', 'Trục quay Trái Đất'); eq(0, north ? 90 : -90);
      note = L('The Earth rotation axis points here; its altitude equals the observer latitude.', 'Trục quay Trái Đất chỉ về đây; độ cao bằng vĩ độ người quan sát.');
    } else if (h.kind === 'marker') {
      const m = d.marker as Marker; title = m.label; kind = L(m.shape === 'plane' ? 'Aircraft' : m.shape === 'square' ? 'Satellite' : 'Event', m.shape === 'plane' ? 'Máy bay' : m.shape === 'square' ? 'Vệ tinh' : 'Sự kiện');
      const [a, l] = this.markerPos(m); az = a; alt = l;
      rows.push([L('Altitude', 'Độ cao'), fmt(alt) + '°'], [L('Azimuth', 'Phương vị'), `${az.toFixed(1)}° ${compassOf(az)}`], ...(m.rows ?? []));
    }
    return { title, kind, above: alt > 0, rows, note, az, alt };
  }
  markerPos(m: Marker): [number, number] {
    const dt = m.t !== undefined ? Math.min(10000, performance.now() - m.t) : 0;
    return [(m.az + (m.vaz ?? 0) * dt + 360) % 360, m.alt + (m.valt ?? 0) * dt];
  }
  /** Current az/alt of the selected object (for the phone-finder guidance), or null */
  selectedAzAlt(): [number, number] | null {
    const h = this.selectedHit; if (!h) return null;
    const d = h.data;
    if (h.kind === 'star') { const s = d.star as NamedStar; return this.azAltOfEq(s.ra, s.dec); }
    if (h.kind === 'body') { const b = this.snap.bodies.find(x => x.id === d.id); return b ? this.azAltOfEq(b.raDeg, b.decDeg) : null; }
    if (h.kind === 'dso') { const m = d.m as Messier; return this.azAltOfEq(m.ra, m.dec); }
    if (h.kind === 'con') { const c = d.con as Constellation; return this.azAltOfEq(c.labels[0]![0], c.labels[0]![1]); }
    if (h.kind === 'cal') { const p = CALENDAR_POINTS.find(x => x.id === d.id)!; return this.azAltOfEq(p.raDeg, p.decDeg); }
    if (h.kind === 'pole') return this.azAltOfEq(0, d.id === 'ncp' ? 90 : -90);
    if (h.kind === 'marker') return this.markerPos(d.marker as Marker);
    return null;
  }

  // ------------------------------------------------------------ frame loop
  /** Projector of the 2D engine view for the overlay layers */
  proj2d(): Proj {
    const v = this.sky.view;
    return {
      w: this.gl.clientWidth, h: this.gl.clientHeight, fovDeg: v.fovRad / DEG, clipBelow: true, noBackdrop: false,
      project: d => project(d, v), pxPerRad: (Math.min(v.widthPx, v.heightPx) / 2) / (v.projection === 'stereographic' ? 2 * Math.tan(v.fovRad / 4) : Math.tan(v.fovRad / 2)),
    };
  }
  start(): void {
    const tick = (): void => {
      const now = performance.now(), dt = (now - this.lastReal) / 1000; this.lastReal = now;
      if (this.live) this.simMs = Date.now(); else if (this.speed !== 0) { this.simMs += dt * 1000 * this.speed; if (this.speed > 1) this.dirty = true; }
      this.refreshSnapshot();
      const day = Math.floor(this.simMs / 86400000);
      if (day !== this.meteorDay) { this.meteorDay = day; this.meteors.setDate(this.snap.date); }
      if (this.layers.meteors) this.meteors.tick(dt, this.live ? 1 : Math.abs(this.speed), this.snap.M); else this.meteors.clear();
      const animating = this.layers.meteors && this.meteors.segments().length > 0;
      if (this.external) this.external(dt, this.dirty);
      else if (this.sky) {
        const full = this.dirty || now - this.lastDraw > 300;
        if (full || animating) {
          if (full) this.sky.jd = this.snap.jd, this.sky.frame();
          this.drawOverlay();
          if (full) { this.lastDraw = now; this.dirty = false; }
        }
      }
      this.raf = requestAnimationFrame(tick);
    };
    this.raf = requestAnimationFrame(tick);
  }
  stop(): void { cancelAnimationFrame(this.raf); }
  drawOverlay(): void {
    const ctx = this.ov.getContext('2d')!; const dpr = window.devicePixelRatio || 1;
    const w = this.gl.clientWidth, h = this.gl.clientHeight;
    if (this.ov.width !== Math.round(w * dpr) || this.ov.height !== Math.round(h * dpr)) { this.ov.width = Math.round(w * dpr); this.ov.height = Math.round(h * dpr); }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, w, h);
    drawLayers(this, ctx, this.proj2d());
  }
}
export { horizontalDir };
