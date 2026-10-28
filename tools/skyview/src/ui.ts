// DOM UI for the sky module: view tabs, layer toggles, time controller, object inspector, language toggle.
// Plain DOM + one injected stylesheet (all rules scoped under .sv-root; colours via --sv-* with fallbacks to the host's CSS variables).
import type { SkyCore } from './engine-adapter';
import { solarTermDates, CALENDAR_POINTS } from './astro';
import { LAYER_GROUPS, LAYER_KEYS, LAYER_LABELS } from './types';
import type { ViewMode } from './types';

const CSS = `
.sv-root{position:relative;width:100%;height:100%;overflow:hidden;background:#02030a;touch-action:none;--sv-card:var(--bg-card,#11151f);--sv-border:var(--border-color,#2a3142);--sv-fg:var(--text-primary,#e6e9f2);--sv-dim:var(--text-secondary,#8b93a7);--sv-accent:var(--accent,#60a5fa);font-size:13px;color:var(--sv-fg)}
.sv-root canvas,.sv-root video{position:absolute;inset:0;width:100%;height:100%;display:block}
.sv-ov{pointer-events:none}
.sv-video{object-fit:cover;background:#000}
.sv-ui{position:absolute;z-index:5;background:color-mix(in srgb,var(--sv-card) 92%,transparent);border:1px solid var(--sv-border);border-radius:8px;backdrop-filter:blur(6px);font-family:inherit}
.sv-root button,.sv-root select,.sv-root input{font:inherit;font-size:12px;color:var(--sv-fg)}
.sv-root button{background:color-mix(in srgb,var(--sv-card) 70%,#fff 6%);border:1px solid var(--sv-border);border-radius:6px;padding:4px 8px;min-height:32px;cursor:pointer}
.sv-root button:hover,.sv-root button.on{border-color:var(--sv-accent);color:var(--sv-accent)}
.sv-root select,.sv-root input[type=datetime-local]{background:var(--sv-card);border:1px solid var(--sv-border);border-radius:6px;padding:4px;min-height:32px}
.sv-left{top:10px;left:10px;max-width:min(300px,calc(100% - 20px));max-height:calc(100% - 150px);overflow:auto;padding:8px 10px;display:flex;flex-direction:column;gap:6px}
.sv-row{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.sv-row h1{font-size:14px;font-weight:600;margin:0}
.sv-grow{flex:1}
.sv-layers{display:grid;grid-template-columns:1fr;gap:1px 10px;padding:2px 0 4px}
.sv-layers label{display:flex;gap:6px;align-items:center;font-size:12px;padding:2px 0;cursor:pointer}
.sv-sections{display:flex;flex-direction:column;gap:2px}
.sv-group{border-top:1px solid rgba(148,163,184,.18);padding-top:3px}
.sv-group summary{cursor:pointer;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:#94a3b8;padding:3px 0;list-style:none}
.sv-group summary::before{content:'▸ '}.sv-group[open] summary::before{content:'▾ '}
.sv-left.sv-collapsed .sv-body{display:none}
.sv-time{left:10px;right:10px;bottom:10px;padding:8px 10px;display:flex;flex-direction:column;gap:6px;max-width:760px}
.sv-time input[type=range]{flex:1;min-width:120px;accent-color:var(--sv-accent)}
.sv-time .sv-more{display:none;flex-direction:column;gap:6px}
.sv-time.sv-open .sv-more{display:flex}
.sv-dim{color:var(--sv-dim);font-size:12px}
.sv-insp{top:10px;right:10px;width:min(290px,calc(100% - 20px));padding:10px 12px;display:none;max-height:calc(100% - 160px);overflow:auto}
.sv-insp.show{display:block}
.sv-insp h3{margin:0;font-size:15px;font-weight:600;display:flex;justify-content:space-between;gap:8px}
.sv-insp dl{display:grid;grid-template-columns:auto auto;gap:2px 12px;margin:6px 0 0;font-size:12px}
.sv-insp dt{color:var(--sv-dim)}.sv-insp dd{margin:0;text-align:right;font-family:ui-monospace,monospace}
.sv-insp p{margin:6px 0 0;font-size:12px;color:var(--sv-dim)}
.sv-status{top:auto;bottom:auto;left:50%;transform:translateX(-50%);top:8px;padding:3px 8px;font-size:11px;color:var(--sv-dim);pointer-events:none;white-space:nowrap}
.sv-guide{left:50%;transform:translateX(-50%);top:44px;padding:4px 10px;display:none;font-size:13px;pointer-events:none}
.sv-guide.show{display:block}
@media(max-width:700px){.sv-left{max-width:calc(100% - 20px);right:10px}.sv-status{display:none}.sv-insp{top:auto;bottom:118px;max-height:35%}}
`;

export interface UiHooks { setMode(m: ViewMode): void | Promise<void>; modes: ViewMode[] }
export interface Ui { root: HTMLElement; guide: HTMLElement; setModeActive(m: ViewMode): void; status: HTMLElement }

export function buildUi(core: SkyCore, root: HTMLElement, hooks: UiHooks): Ui {
  if (!document.getElementById('sv-style')) { const st = document.createElement('style'); st.id = 'sv-style'; st.textContent = CSS; document.head.append(st); }
  const texts: [HTMLElement, string, string][] = [];
  const el = <K extends keyof HTMLElementTagNameMap>(tag: K, cls?: string, en?: string, vi?: string, parent?: HTMLElement): HTMLElementTagNameMap[K] => {
    const e = document.createElement(tag); if (cls) e.className = cls;
    if (en !== undefined) { e.textContent = en; texts.push([e, en, vi ?? en]); }
    parent?.append(e); return e;
  };
  const retext = (): void => { for (const [e, en, vi] of texts) e.textContent = core.lang === 'vi' ? vi : en; };

  // ---- left panel
  const left = el('div', 'sv-ui sv-left', undefined, undefined, root);
  const head = el('div', 'sv-row', undefined, undefined, left);
  el('h1', '', 'Sky view', 'Bầu trời', head);
  el('span', 'sv-grow', undefined, undefined, head);
  const langBtn = el('button', '', undefined, undefined, head); langBtn.title = 'EN / VI';
  const colBtn = el('button', '', '▾', '▾', head);
  const body = el('div', 'sv-body', undefined, undefined, left); body.style.cssText = 'display:flex;flex-direction:column;gap:6px';
  body.className = 'sv-body';
  const tabs = el('div', 'sv-row', undefined, undefined, body);
  const tabBtns = new Map<ViewMode, HTMLButtonElement>();
  const TABS: [ViewMode, string, string][] = [['eye', 'Inside view', 'Nhìn từ trong'], ['3d', 'Outside view', 'Nhìn từ ngoài']];
  for (const [m, en, vi] of TABS) { if (!hooks.modes.includes(m)) continue; const b = el('button', '', en, vi, tabs); b.addEventListener('click', () => void hooks.setMode(m)); tabBtns.set(m, b); }
  const checks = new Map<string, HTMLInputElement>();
  const onlyRows: { row: HTMLElement; only: ViewMode }[] = [];
  const sections = el('div', 'sv-sections', undefined, undefined, body);
  for (const g of LAYER_GROUPS) {
    const det = el('details', 'sv-group', undefined, undefined, sections); det.open = g.open;
    el('summary', '', g.title[0], g.title[1], det);
    const lay = el('div', 'sv-layers', undefined, undefined, det);
    for (const { k, only } of g.keys) {
      const l = document.createElement('label'); lay.append(l);
      const cb = document.createElement('input'); cb.type = 'checkbox'; cb.checked = core.layers[k];
      cb.addEventListener('change', () => core.setLayer(k, cb.checked));
      const sp = document.createElement('span'); l.append(cb, sp); texts.push([sp, LAYER_LABELS[k][0], LAYER_LABELS[k][1]]);
      checks.set(k, cb); if (only) onlyRows.push({ row: l, only });
    }
  }
  const collapsed = (): void => { left.classList.toggle('sv-collapsed'); };
  colBtn.addEventListener('click', collapsed);
  if (window.matchMedia('(max-width: 700px)').matches) left.classList.add('sv-collapsed');
  langBtn.addEventListener('click', () => core.setLang(core.lang === 'vi' ? 'en' : 'vi'));

  // ---- status line
  const status = el('div', 'sv-ui sv-status', undefined, undefined, root);
  const guide = el('div', 'sv-ui sv-guide', undefined, undefined, root);

  // ---- time controller
  const tc = el('div', 'sv-ui sv-time', undefined, undefined, root);
  const r1 = el('div', 'sv-row', undefined, undefined, tc);
  const nowBtn = el('button', '', 'Now', 'Hiện tại', r1); nowBtn.addEventListener('click', () => core.goNow());
  const playBtn = el('button', '', undefined, undefined, r1);
  playBtn.addEventListener('click', () => { if (core.speed === 0) core.setSpeed(1); else core.setSpeed(0); });
  const spd = el('select', '', undefined, undefined, r1); for (const s of [1, 10, 60, 300, 1000]) { const o = document.createElement('option'); o.value = String(s); o.textContent = s + '×'; spd.append(o); }
  spd.addEventListener('change', () => core.setSpeed(Number(spd.value)));
  const dt = el('input', '', undefined, undefined, r1); dt.type = 'datetime-local';
  dt.addEventListener('change', () => { const v = Date.parse(dt.value); if (!Number.isNaN(v)) core.setTime(v); });
  const tstat = el('span', 'sv-dim', undefined, undefined, r1);
  el('span', 'sv-grow', undefined, undefined, r1);
  const moreBtn = el('button', '', 'Time ▾', 'Thời gian ▾', r1); moreBtn.addEventListener('click', () => tc.classList.toggle('sv-open'));
  const more = el('div', 'sv-more', undefined, undefined, tc);
  const slider = (en: string, vi: string, min: number, max: number, onInput: (v: number) => void): { range: HTMLInputElement; val: HTMLElement } => {
    const row = el('div', 'sv-row', undefined, undefined, more); const lb = el('span', 'sv-dim', en, vi, row); lb.style.minWidth = '88px';
    const range = el('input', '', undefined, undefined, row); range.type = 'range'; range.min = String(min); range.max = String(max);
    const val = el('span', 'sv-dim', undefined, undefined, row); val.style.minWidth = '56px';
    range.addEventListener('input', () => onInput(Number(range.value))); return { range, val };
  };
  const tod = slider('Time of day', 'Giờ trong ngày', 0, 1439, v => { const d = new Date(core.simMs); d.setHours(Math.floor(v / 60), v % 60, 0, 0); core.setTime(d.getTime()); });
  const doy = slider('Day of year', 'Ngày trong năm', 1, 366, v => { const d = new Date(core.simMs); const y0 = new Date(d.getFullYear(), 0, 1, d.getHours(), d.getMinutes()); y0.setDate(v); core.setTime(y0.getTime()); });
  const steps = el('div', 'sv-row', undefined, undefined, more);
  for (const [ms, lb] of [[-86400000, '−1 d'], [-3600000, '−1 h'], [-600000, '−10 m'], [-60000, '−1 m'], [60000, '+1 m'], [600000, '+10 m'], [3600000, '+1 h'], [86400000, '+1 d']] as [number, string][]) {
    const b = el('button', '', lb, lb, steps); b.addEventListener('click', () => core.shiftTime(ms));
  }
  const seasons = el('div', 'sv-row', undefined, undefined, more);
  for (const p of CALENDAR_POINTS) {
    const b = el('button', '', p.en, p.vi, seasons); b.style.opacity = p.major ? '1' : '0.8';
    b.addEventListener('click', () => { const d = solarTermDates(new Date(core.simMs).getUTCFullYear())[p.id]; if (d) core.setTime(d.getTime()); });
  }

  // ---- inspector
  const insp = el('div', 'sv-ui sv-insp', undefined, undefined, root);
  const renderInspector = (): void => {
    const h = core.selectedHit;
    if (!core.selected || !h) { insp.classList.remove('show'); return; }
    const d = core.inspect(h);
    insp.replaceChildren();
    const t = document.createElement('h3'); const name = document.createElement('span'); name.textContent = d.title;
    const x = document.createElement('button'); x.textContent = '×'; x.style.minHeight = '24px'; x.addEventListener('click', () => core.select(null));
    t.append(name, x); insp.append(t);
    const k = document.createElement('div'); k.className = 'sv-dim'; k.textContent = d.kind + ' · ' + (d.above ? core.t('above horizon', 'trên chân trời') : core.t('below horizon', 'dưới chân trời')); insp.append(k);
    const dl = document.createElement('dl');
    for (const [a, b] of d.rows) { const dt2 = document.createElement('dt'); dt2.textContent = a; const dd = document.createElement('dd'); dd.textContent = b; dl.append(dt2, dd); }
    insp.append(dl);
    if (d.note) { const p = document.createElement('p'); p.textContent = d.note; insp.append(p); }
    if (core.mode !== '3d') { const g = document.createElement('button'); g.textContent = core.t('Centre on it', 'Hướng tới'); g.style.marginTop = '8px'; g.addEventListener('click', () => core.goTo(d.az, d.alt)); insp.append(g); }
    insp.classList.add('show');
  };
  core.on('select', renderInspector);
  setInterval(() => { if (core.selected) renderInspector(); }, 1000);

  // ---- refresh on state change
  const pad = (n: number): string => String(n).padStart(2, '0');
  const sync = (): void => {
    retext();
    langBtn.textContent = core.lang === 'vi' ? 'VI' : 'EN';
    for (const k of LAYER_KEYS) checks.get(k)!.checked = core.layers[k];
    playBtn.textContent = core.speed === 0 ? '▶' : '❚❚';
    const d = new Date(core.simMs);
    if (document.activeElement !== dt) dt.value = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
    tod.range.value = String(d.getHours() * 60 + d.getMinutes()); tod.val.textContent = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    const y0 = new Date(d.getFullYear(), 0, 1); const n = Math.round((new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime() - y0.getTime()) / 86400000) + 1;
    doy.range.value = String(n); doy.val.textContent = 'd' + n;
    spd.value = String(core.speed === 0 ? spd.value : core.speed);
    tstat.textContent = d.toISOString().slice(0, 16).replace('T', ' ') + ' UTC' + (core.live ? ' · live' : !core.isNow ? ' · ' + core.t('aircraft/satellites hidden', 'ẩn máy bay/vệ tinh') : '');
    status.textContent = `fov ${core.mode === '3d' ? '' : core.fovDeg.toFixed(core.fovDeg < 10 ? 1 : 0) + '°'}  lat ${core.observer.latDeg.toFixed(2)} lon ${core.observer.lonDeg.toFixed(2)}`;
  };
  core.on('change', sync); sync();
  setInterval(sync, 1000);
  return {
    root, guide, status,
    setModeActive: m => {
      for (const [k, b] of tabBtns) b.classList.toggle('on', k === m);
      for (const o of onlyRows) o.row.style.display = o.only === m ? '' : 'none';
      retext();
    },
  };
}
