# UI redesign plan (Phase 0 audit + plan)

Date 2026-11-23. Mockups: `docs/design/mockups/p01…p23` (+ `design.opnai.json`). Live: https://allsky.hoan.uk. No UI code was changed in this phase.

## Principles (owner design rules)

1. **ICONS FIRST, LESS TEXT.** Users recognise icons faster and prefer them. Every action, tab, toggle and setting row leads with a clear icon; text is only a short label, tooltip or `aria-label` where an icon alone is ambiguous. Cut helper sentences; long explanations go behind an info icon (opens a `ds-sheet`). Status is shown by icon + colour, not words. Tab bar, rail and sheets are icon-led.
2. **Check: no screen has a paragraph of text above the fold** (checked per page at 390x844 and 1440x900 in the acceptance step).
3. Finish and unify the partial redesign; no big-bang; plain CSS + existing JS.

**Icon set:** Lucide (ISC licence, permissive; 1 500+ icons, one 2 px stroke weight, active releases, plain SVG). Alternative Tabler Icons (MIT) only if a needed glyph is missing; never mix sets. Shipped as one inline SVG sprite `static/icons.svg` (`<use href="#i-name">`, `currentColor`, 24 px grid) plus a `.ds-icon` class; only the ~60 icons used (about 10 KB). Licence line goes in `static/icons.LICENSE` and the README. Replaces the unicode/emoji glyphs in the sidebar and ad hoc inline SVGs.

**Accessibility:** icon-only controls always carry `aria-label` (and `title` on desktop); hit area >= 44 px (`--tap`); icon never the only carrier of meaning for colour (shape differs: check / alert / x); focus ring on all.

**Icon list per page (starting set):** Live: eye/camera, cloud, moon, sun, plane, satellite, balloon (sondes), layers, info, expand, refresh. Archive: calendar, film/image, filter, download, trash, star. Settings: camera, image, map-pin, crosshair, sliders, plug, shield, search, chevron-right, info. Overhead: plane, satellite, balloon, compass, arrow-up-right. Sky: layers, clock, sun, moon, sparkles. Community: users, map, share, message. Admin: cpu, memory-stick, hard-drive, activity, power. Sign-in: user, key, log-in. Shared: tabs (radio/live, sky, community, archive, menu), bell, wifi/wifi-off, red-eye (Red Vision), x, check, alert-triangle.

## 0. Where the product is today (important context)

A **partial mobile redesign already shipped** (`css/redesign*.css`, 1 230 lines, `m-tabbar`, bottom Menu sheet, settings home list, cards). So the job is not "start over" but "finish and unify". Findings below are from the live site (mobile = 390 px iframe of the real pages, desktop = 1568 px) and a grep of `src/web`.

Inventory: 9 HTML pages (index 1 845 lines holds Live/Gallery/Settings/Calibration/Focus/Logs as tab sections; flights3d 2 600; satellites3d 1 577; full 1 357; obs_dock 1 003), 18 CSS parts imported in cascade order from `style.css` (3 900 lines), 15 JS files in `app/`, 1 000+ line pages with inline `<style>`.

## 1. The 10 worst problems (with evidence)

1. **Two navigation systems, no shared map.** Desktop: 14-item left sidebar with unicode/emoji glyphs (◉ ✨ 🌐 ✈ …) in `index.html:65-89`; mobile: 5 SVG tabs. Overhead, Flights 3D, Satellites 3D, Logs have no tab and light up "Menu" or nothing (seen on /overhead and /flights3d). Sidebar clips "Settings" below the fold at 757 px high.
2. **Top bar differs on every page.** Live: Online + AUTO + UTC + Local + Red Vision switch. Sky/Overhead/Community: only UTC + a "Red Vision" text link. Gallery/Settings: yet another mix. Mobile gets a title bar only on some pages (Community, Flights 3D yes; Gallery, Overhead, Settings no).
3. **Floating chat button covers content on every page.** Mobile Live (hides the distance/bearing column of the flight list), Sky ("Sun & planets"), Flights 3D (attribution + Aircraft card), Settings, Archive. It is fixed bottom-right with no safe area logic (`css/chat.css`, z-index 1000).
4. **Touch targets and type below minimum.** On mobile Live 30 of 85 interactive elements are under 44 px, 9 under 32 px; Overhead 21 of 39 under 44 px (`live-events-more` 21–30 px high, chips). Sizes in use: 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 22, 24, 26, 28, 34, 54 px, plus rem variants (0.75/0.78/0.8/0.8125/0.82/0.85/0.95 rem). 10–11 px text appears on 20+ elements. `--font-size-min: 12px` exists but is not enforced.
5. **Token system is declared but not used.** `tokens.css` has 7 brand colours, 3 radii, 5 spacing steps; but `--brand-*` (7) and `--space-6` are never referenced, `--space-1…4` are almost unused (px values: 734 in `redesign.css`, 388 in `redesign-pages.css`), and **no type scale tokens exist**. Hard-coded colours: flights3d 124 hex, satellites3d 45, full.html 33 (and 0 `var()`), index 23, sky 15, overhead 14, community 14. Radii in use: 2,3,4,6,8,10,12,14,16,20,999 px (11 values) against 3 tokens. Undefined variables referenced: `--bg-elevated`.
6. **Component forks.** `btn`, `btn-sm`, `btn-outline`, `btn-primary`, `btn-preset`, `btn-range`, `btn-lock`, `btn-live`, `btn-textlink`… (10+ button classes); toggles exist as `.switch`, `.toggle-row`, native checkbox lists (Sky layers) and the "Red Vision" pill; chips as `chip-preset`, `.pill`, `.tag`, `.badge-*`, filter chips on Gallery and Overhead; cards as `.card`, `.m-tile`, `.cm-*`, `.adm-*`. The 3D pages ship their own controls (square white zoom buttons on Flights 3D), unrelated to the app's style.
7. **Desktop = stretched mobile or empty canvas.** Overhead desktop: one 380 px card in a 1 400 px canvas plus a stray "Loading…" bar; Gallery/Events renders a blank page for seconds; Settings is a single column of long-paragraph cards (e.g. Image Output intro text, 3 lines at 12 px) with no section navigation (sub-nav exists only on mobile as `mobile-subnav`); Community leaves a right "Stations" panel empty until data arrives.
8. **Density and information design.** Sky view panel on desktop is a 25-row checkbox list in 12 px text; on mobile the time bar, "Sun & planets" and layer card stack and overlap. Live shows "CLOUD 100% / Sky Day" tiles while the exposure card, cloud card, histogram, telemetry are four more stacked collapsibles with different headers. Settings subtitle bug: "When-lowMeasureScoreLensTempEx…" (concatenated text, `settings_home.js` focus summary).
9. **Empty/loading/error states are ad hoc.** "Loading…" text bar (Overhead), blank grid (Gallery Events), "No aircraft heard right now" in dimmed 12 px, "No sky events." inside a card, map with empty side panel (Community). No skeleton, no retry, no offline banner; only one `toast` implementation (`toast-stack`) and it is not used by 3D pages.
10. **Theme and accessibility gaps.** Night/red mode works through `[data-theme="red"]` on tokens, but pages with hard-coded colours (3D pages, full.html, charts, overlay canvases) do not follow it; only 3 `prefers-reduced-motion` blocks, no `prefers-color-scheme`; `aria-` attributes sparse outside index (5 `aria-` on 1 845 lines of index excluding nav); only 5 dialog roles for ~10 sheets/modals; 9 breakpoints (600/640/700/760/768/1023/1280…) with no shared names; z-index scale ad hoc (1,2,3,4,60,70,80,90,1000).

## 2. (a) Design-system foundation

Plain CSS custom properties + small vanilla JS helpers. No framework (the app is server-rendered static HTML + IIFE scripts; a framework would force a build step and rewrite 15 JS files for no gain).

**Files (new, loaded by every page, in this order):** `css/ds-tokens.css` (merges and replaces `tokens.css`, keep the old name as an alias file so existing `?v=` links keep working), `css/ds-base.css` (reset, type, focus ring, safe-area), `css/ds-components.css`, `js/ds.js` (sheet, toast, switch helpers).

**Tokens**
- Colour roles (keep current values, they match mockup `#0b1220`): `--bg`, `--surface-1/2/3`, `--border`, `--divider`, `--text`, `--text-2`, `--text-3`, `--accent`, `--accent-bg`, `--ok`, `--warn`, `--danger`, `--info`, `--violet`. Brand colours stay as source palette. Add `--overlay-scrim`, `--shadow-1/2`.
- Night/red mode: one override block per role (exists for ~16 vars; extend to all roles above and **forbid hex in components**: a lint check, section 6). Add a `data-theme="red"` variant of map and sky canvas palettes read from CSS variables via `getComputedStyle`, so 3D/canvas pages follow it.
- Icons: one Lucide sprite (see Principles); every component below takes an icon slot first, label second.
- Type scale (7 steps, px, with line-height): `--fs-11` caption, `--fs-12` meta, `--fs-14` body, `--fs-16` row title, `--fs-18` section title, `--fs-22` page title, `--fs-32` hero number. Rule: nothing under 12 px except uppercase 11 px labels. Fonts: Inter (text), JetBrains Mono (numbers); drop Space Grotesk (not bundled, falls back).
- Spacing: 4-based `--s-1…--s-8` (4,8,12,16,20,24,32,48). Radius: `--r-sm 8`, `--r-md 12`, `--r-lg 16`, `--r-xl 20`, `--r-pill`. Touch: `--tap: 44px` min height for every control.
- Z-index named layers: `--z-nav 40`, `--z-fab 50`, `--z-sheet 60`, `--z-toast 70`, `--z-modal 80`. Breakpoints documented once: phone ≤ 640, tablet 641–1023, desktop ≥ 1024.

**Shared components (10)**: `.ds-tabbar` (bottom, 5 tabs, safe-area aware, also becomes the left rail ≥ 1024), `.ds-sheet` (bottom sheet mobile, side/centred dialog desktop, focus trap, Esc), `.ds-card`, `.ds-row` (list row: icon, title, subtitle, value, chevron; 56 px), `.ds-switch`, `.ds-btn` (primary / secondary / ghost / danger, sm / md, 44 px hit area), `.ds-chip` (filter and status variants), `.ds-toast` (queue, replaces `toast-stack`), `.ds-empty` (icon, title, hint, optional action; plus `.ds-skeleton`), `.ds-topbar` (title, status dot, UTC/local, Red Vision, one markup everywhere). Also decide where the chat button lives: move into Menu sheet on phones; on desktop keep the FAB but offset by `--tabbar-h` and hide while a sheet or full-screen map is open.

## 3. (b) Page-by-page migration order (one worker task each, disjoint files)

Shared first, sequentially (one worker, then a review): 
- **S0 (M)** tokens + base + components + `ds.js` + a hidden `/static/ds-demo.html` showing each component in default/night mode. Files: `ds-*.css`, `js/ds.js`, `tokens.css` alias. 
- **S1 (M)** shell: shared top bar, tab bar, desktop rail, sheet (Menu/Layers), chat FAB relocation. Files: `shell.js`, `css/sky-shell.css`, `css/redesign.css` header parts, the nav blocks of each HTML (one-line include each).
- **S2 (S)** the lint script + CI check (hex/px/font-size ban in page CSS).

Then pages, each a single worker, in this order (value × risk), sizes S ≤ 0.5 d, M ≈ 1 d, L ≈ 2 d:
1. **Live /live (L)** `index.html` Live section, `app/04-live-view.js`, `08d-live-ui.js`, `css/viewport.css`, `redesign-live.css`, `telemetry.css`. Biggest traffic. Mockups p01, p02, p03, p04, p17 info card. Includes the tap-for-info card and Overhead-now tabs.
2. **Archive /gallery + Event detail (M)** `gallery.css`, `events.css`, `app/08*-gallery*`, mockups p13, p15. Worker-4's detector/event-viewer audit must be merged first to avoid overlap.
3. **Settings (L, split in 2 tasks)** 3a home + Image/Station/Detection (p05, p09, p11, p12), 3b Sky mask, Integrations, Focus, Master dark (p06, p07, p14, p16). Files: `settings.css`, `focus.css`, `settings_home.js`, `settings_layout.js`, `app/02`, `03`, `08c`. Desktop gets a two-pane layout (section list left).
4. **Overhead /overhead (S)** `overhead.html`, `overhead.js` (p10).
5. **Sky /sky (M)** `sky.html`, `sky_lists.js`, `app/05-*` (p08). Layers sheet shared with Live.
6. **Community + Sharing (M)** `community.html`, `community.js`, `app/09` (p18, p19).
7. **Admin + Activity log (S)** `app/10-admin.js`, `activity-cloud.css`, `activity.js` (p20, p21).
8. **Sign-in (S)** `login.html`, `login.js` (p23). 
9. **OBS dock (M)** `obs_dock.html` (p22), uses desktop layout only.
10. **Flights 3D / Satellites 3D (L each)**: last, only the chrome (top bar, cards, controls, theming from tokens); the three.js scenes untouched. `flights3d.html` 124 hex colours is the long tail. Test in Claude in Chrome (WebGL), never headless.
11. **full.html fullscreen viewer (S)** move 33 hex to tokens.

Order of workers: S0 → S1 → (Live, Overhead, Sign-in in parallel, disjoint) → (Archive, Sky) → Settings 3a → 3b → Community, Admin → OBS → 3D pages. Max 3 workers at once.

## 4. (c) Keeping the live site working

- **No big-bang.** Every page keeps its URL and its API. Server already sends `Cache-Control: no-store` for HTML (`api/main.py:142`) and CSS/JS use `?v=` strings; bump them in the same commit as the change. 
- **Per-page switch:** each migrated page loads `ds-*.css` always (additive, class-prefixed `ds-`, cannot restyle old markup) and activates its new layout through `<body data-ui="2">`. Rollback = flip the attribute. Optional `?ui=1` query / `localStorage.ui` override for the owner to compare. Remove the old branch one release after the page is accepted. 
- New CSS goes into new files; do not edit old `redesign*.css` except to delete rules the page no longer uses (otherwise cascade-order bugs: 18 files in order).
- Deploy the existing way (rsync after commit), check on the real phone via the 3-tab device checklist (Live, Settings, Archive) before moving to the next page.

## 5. (d) Mockup features the product does not have yet

Decisions recommended (owner confirms):
| Mockup feature | Backend today | Recommendation |
|---|---|---|
| Admin CPU / RAM / disk tiles (p20) | `telemetry` has CPU temp; no RAM/disk API verified | **Build**, small: add `/api/system` (psutil), S. Needed for a credible Admin. |
| "Overlay text" switch (p03) | text (time, exposure) always drawn in the live frame | **Build**: one flag in overlay config, S. |
| "Airports" layer switch (p03) | no airport data | **Drop** from Layers sheet v1 (needs a dataset and licence check); revisit later. |
| Flight route (origin → destination) in tap card (p04) | ADS-B gives callsign/alt/speed only | **Drop** v1; show callsign, altitude, speed, distance, bearing. Route needs an external API (cost, privacy). |
| Rise/set times in tap card (p04) | computable for Sun/Moon/planets/satellite passes from existing sky data (`sky_catalog.js`, skydata) | **Build** for sky objects, S–M; not for aircraft. |
| Sky page content (p08) | `/sky` exists with the layers and time control | **Keep** the existing planetarium, restyle only; mockup content was a guess. |
| Sondes tab in "Overhead now" (p01) | already present (Flights 14 / Sondes 1) | Done. |
| Tap-for-info replaces tap-to-zoom (p04) | zoom/pan on tap today | **Build** in Live task; double-tap and pinch stay for zoom. |
| Menu sheet with status line + sign-in (p02) | exists (`m-sheet`, `m-st`) | Reuse, move to `ds-sheet`. |

## 6. (e) Desktop layout

Mockups are mobile only. Proposal, same components, three tiers: ≤ 640 phone (bottom tab bar, one column, sheets); 641–1023 tablet (tab bar, two-column grids, sheets become centred dialogs); ≥ 1024 desktop (left rail with the same 5 items + grouped secondary items, 12-column content max 1280 px, right context panel for Live (Overhead now, Recent events) and a two-pane Settings and Archive (list + detail)). Live image keeps full width up to its aspect ratio, never upscaled blurry. One markup, CSS only (container queries are fine in current Chrome/Safari). The rail replaces the current 14-item sidebar and fixes problems 1 and 2.

## 7. (f) Risks

- **Cascade explosion**: 18 CSS files, `!important` and later files override earlier ones; mitigate with prefixed new classes and `data-ui` gate, delete old rules page by page.
- **index.html is a 1 845-line monolith** hosting six pages; parallel workers will conflict. Mitigation: S1 first splits per-section markers; Live, Archive, Settings tasks are sequential on that file, or first extract each section into its own HTML include (M, recommended before Phase 1).
- **WebGL/3D pages** can only be verified in real Chrome with GPU; keep them last and cheap (chrome only).
- **Night/red mode regressions** on canvas drawing code (hard-coded colours in `app/05*`, `07-cloud.js`, 3D pages): needs variable-driven palettes and a manual red-mode screenshot pass per page.
- **Pi performance/data**: no extra JS payload (target ≤ +15 KB gzip), no web-font additions, avoid backdrop-filter on the live viewport.
- **Overlap with Worker-4** (event viewer, detector); sequence Archive after its merge.
- **Scope creep from mockup features** (section 5): ship the visual system first, features as separate tasks.
- **Testing cost**: follow the global rule, one E2E per page in CI on the `lan` runners, a lint for tokens, manual device check; no repeated local browser loops.
- **Mockups**: they live permanently in the owner's personal opnai account (read-only for us: never delete, rename or edit). `docs/design/mockups/` is a repo copy for reference.

## 8. Per-page deliverable checklist (added)

Every page task must deliver: icon-led controls from the sprite (no emoji/unicode glyphs), `aria-label` on icon-only buttons, 44 px targets, helper text moved behind an info icon, status by icon+colour, and a screenshot pair (390 and 1440, default and red mode) showing no paragraph above the fold.
