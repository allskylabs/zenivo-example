⚠️ DEGRADED: single-context (token budget ~100k) — Assessment A and B were not run as separate sub-agents; one reviewer did both.

# Zenivo web UI — critique + audit (no code changed)

Live site https://allsky.hoan.uk/ (owner signed in), source `src/web`. Method: same-origin iframe at 390 px and 1568 px in Claude in Chrome, JS audit per page (text <12 px, targets <44 px, horizontal overflow, un-hidden `[hidden]`, unlabeled buttons), screenshots in `docs/design/critique/`, compared against `docs/design/mockups/`.

**Out of scope here (Worker-15 owns them):** topbar cluster (`.zt-cluster`, truncated "Hanoi, Vie…" at 390, clipped clock), Events card/viewer and its white native scrollbar.

**Coverage honesty.** Reviewed at 390: Live, Sky, Overhead, Archive, Community, Settings home + #image #detection #station #skymask #integrations #community #admin, Logs, OBS dock. Reviewed at 1568: Live, Sky, Overhead, Archive, Community, Settings, Flights 3D, Satellites 3D. **Not covered:** `/full`, `/focus`, `/calibration` page chrome, red mode, 390 view of the two 3D pages, and loading/error states (not reachable on a healthy station). Sign-in is not live-reviewable (owner is signed in; I did not sign out) — review it from `login.html` / `css/login.css` vs `p23-signin.png` only. Mockup comparison was read in full only for p05 and p19; other pages were judged against the owner rules (icons first, less text, 44 px, ≥12 px). `file:line` below were grepped for the main rules; where a line is "approx" the selector exists in that file but the exact rule should be located with grep.

Counts: **P1 = 6, P2 = 14, P3 = 8.**

## Top 10
1. P1 Settings home Focus row subtitle is garbage ("WhenHowMeasureScoreLensTempExp…").
2. P1 Focus history table overflows at 390 and clips columns/score.
3. P1 Archive toolbar clips the refresh icon at 390 (`.arc-tools`).
4. P1 Community desktop: map area blank, one 330 px card in a 1568 px void.
5. P1 Logs page is ~30 000 px tall at 390 with no paging/virtualisation.
6. P1 Satellites 3D empty state: text overflows its panel with a white native horizontal scrollbar.
7. P2 Touch targets <44 px: Live presets (36–40), OBS dock buttons (28–32), detection inputs (30), Community markers (26), `.page-back` (32).
8. P2 Text <12 px on Live lock chips, Overhead counters, Archive date header, Community timestamps.
9. P2 Settings #image: truncated select values/labels ("Mast…", "2304 × 12…"), duplicated "2304 × 1296".
10. P2 Settings row types inconsistent (heights, toggle+chevron mixes, 3-line subtext, nested cards).

## P1 — broken / unusable
| # | Page | What | Why | Fix | Where |
|---|---|---|---|---|---|
| 1 | Settings home | Focus row subtitle reads `WhenHowMeasureScoreLensTempExp…` | JS takes the first `\n` line of a `<table>`'s textContent, so header cells concatenate. Misleading, looks broken. Mockup p05: "HFR 2.1 px · best 1.8". | Read the latest `tr.latest` row cells: `"<score> · <measure> · <date>"`, or best HFR from data, not `textContent`. | `src/web/settings_home.js:13` |
| 2 | Settings → Focus (#station) | Wide table (≈872 px) inside 390: columns Measure/Score clipped, rows have mixed strike-through ("near") + green "latest" with no legend; 12 px mono-ish text | Table is the whole page content; horizontal scroll hidden inside card; no hierarchy | Replace by card rows at ≤600 px: date (short, "23 Nov 23:10") + score big + icons for sweep/auto + measure; drop UTC offset; legend or icon for struck rows | `css/focus.css:85-92` (table nowrap), render in focus history JS |
| 3 | Archive 390 | `.arc-tools` / `#btn-build-timelapse` overflow (right edge r≈434–448): refresh icon half cut off | 5 icon buttons × 44 px + tabs exceed 358 px content | Collapse secondary tools into one "⋯" button at <480 px, or let `.arc-bar` wrap | `css/archive.css:6,13` |
| 4 | Community 1568 | Left map pane is empty dark (only a vertical line), station card 330 px wide centred in a huge void; mockup p19 shows map hero + stations list | Map tiles/canvas not rendering or sized 0; layout row at ≥768 leaves no map width | Give map `flex:1; min-height:60vh`, list a fixed 360 px column; show empty-map state with online/idle/offline legend | `css/community.css:3,73` |
| 5 | Logs 390 | Page ~30 089 px tall, chip rows overflow (r=432), no paging | Whole log rendered in DOM; unscannable | Page 50 rows + "Load more", sticky filter chips that wrap or scroll with a fade edge | `css/activity-log.css:10`, logs render JS |
| 6 | Satellites 3D 1568 | Empty state text "No satellite is above eye visible right now…" runs past panel, native white horizontal scrollbar | Unwrapped text in `overflow:auto` list | `white-space:normal`, icon-first empty state (satellite + "None visible now"), themed scrollbar | `css/scene3d.css` (sat list) |

## P2 — ugly / inconsistent
| # | Page | What | Why | Fix | Where |
|---|---|---|---|---|---|
| 7 | Live 390 | `.btn-preset` / `-gain` 36–40 px tall | Violates 44 px; `phone.css` explicitly lowers to 36 | Delete the override; keep `--tap` | `css/phone.css:9`, `css/redesign.css:375` (40px), `css/live.css:87,166` |
| 8 | Live | `#lock-exposure/#lock-gain/#lock-target`, `#live-events-status` <12 px; status chips show `--`/blank | Illegible; empty values look broken | min 12 px; show icon + "—" muted with tooltip, hide chip while no data | `css/live.css` (lock chips), `css/base.css:87` |
| 9 | Live 1568 | Content centred with wide empty gutters; right Overhead column source text tiny | Wasted width, weak hierarchy | Two-column grid (image 2fr / side 1fr) up to 1280 px max | `css/ds-tokens.css:76` (`--content-max`) + `css/live.css` |
| 10 | Overhead 390 | Chip row overflow (r=498) without scroll affordance; "0 in view · 0 heard" <12 px mono | Hidden filters; tiny text | `.ds-chips` fade mask on right; counter 12 px+ with icons only | `css/ds-components.css:68`, `css/overhead.css` |
| 11 | Overhead 1568 | Diagonal-arrow glyph on every row has no clear meaning (open? direction?); source text 10–11 px | Ambiguous icon, small text | Use a chevron or "locate" icon with tooltip; row sub-line 12 px | `css/overhead.css` (row) |
| 12 | Settings #image | Select values truncated ("Mast…", "2304 × 12…"), label wraps; "2304 × 1296" shown 4× (badge + 3 tiles) | Less text rule; truncation hides the value | Stack label above select at ≤480; drop duplicate badge; tiles show icon + one number | `css/redesign.css:346-348` |
| 13 | Settings (all) | Row types differ: toggle only / toggle + chevron / chevron / select; heights vary by subtitle; Integrations rows have 3-line subtext; card nested in card (Admin, Community) | Rhythm and scannability | One `.ds-row` anatomy: icon · title (+1-line sub) · single trailing control; info icon opens long text | `css/settings.css`, `css/settings-sub.css`, `css/redesign.css` |
| 14 | Settings #detection | Number inputs 50×30 px; no header icon unlike other subpages | Small target; inconsistent header | min 44 px height, 72 px width; same page-head component | `css/settings-sub.css`, `css/redesign-pages.css:7` |
| 15 | Settings #skymask | `input#sat-g-*` 54×42 chips, icon-only "+" buttons with no label/tooltip | Unlabeled actions | `aria-label` + tooltip, 44 px | `css/redesign.css:304-309` (`min-height:40px`) |
| 16 | Settings home | Section labels (CAMERA, STATION…) tiny uppercase; subtitles 11–12 px; mockup p05 uses 15–17 px sub and 13 px labels | Contrast/size vs mockup | Raise sub to `--fs-14`, label `--fs-12` with letter-spacing | `css/settings.css` (home rows) |
| 17 | Admin 390 | "Public l…" label truncated (should be "Public location"); metrics tiles uneven (5 tiles in 3-col grid, orphan row); 1594/1846 MB caption 10 px | Truncation, orphan tiles | 2-col grid or 3+2 centred; label gets full row | `css/settings-admin.css` |
| 18 | OBS dock 390 | Buttons 28–32 px tall; status row `--:--:--` dimmed to unreadable | Target size, empty-state contrast | 44 px buttons; muted-but-AA placeholders | `src/web/obs_dock.html` styles |
| 19 | Community 390 | Markers 26×26 / 40×40; `main.cm` overflows by ~22 px (r=397) | Target size, overflow | 44 px hit areas (pseudo-element padding), `min-width:0` on children | `css/community.css:3` |
| 20 | Sky 1568/390 | Orange sky-mask polygon is a heavy fill over the star field; bottom time bar and right icon stack dense and small | Competes with the sky, tiny controls | Fill ≤12 % alpha, 1 px outline; time bar 44 px controls, group by gap | `css/sky.css`, `css/sky-shell.css` |

## P3 — polish
| # | Page | What | Fix |
|---|---|---|---|
| 21 | Archive | Date header "Sun, Nov 22 · 31" 10 px mono (`.arc-day__head`) | 12 px, icon + count (`css/archive.css:37`) |
| 22 | Archive 1568 | Thumbnails of varying aspect leave ragged cards, blue plane-track overlay competes with content | Uniform 16:9 object-fit, thin 1 px track |
| 23 | Overhead/Live | Chart saturated blue, mono exposure row | Use token accent at 70 % |
| 24 | Maps | Attribution clipped/overlapping controls | Move to bottom-left collapsed "i" |
| 25 | Flights 3D | List rows 11 px; flag icons and LOCAL pills crowd the row | Drop LOCAL pill (colour dot), 12 px, 44 px rows |
| 26 | Satellites 3D | Header panel text-heavy ("Built-in; eye visible; …") | One icon + 2 numbers |
| 27 | Community 1568 | Station rows: 2 text lines + status icon fine, but timestamps 10 px | 12 px (`css/community.css`) |
| 28 | Logs | `a.page-back` 32×32 | 44 px hit area (`css/redesign-pages.css:7`) |

## Page notes vs mockups
- **Live (p01):** structure matches; the issues are sizes (#7, #8) and desktop width (#9).
- **Settings home (p05):** layout matches; the Focus subtitle regression (#1) and smaller type (#16) are the differences.
- **Community (p19):** mockup is map-first; live desktop loses the map (#4).
- **Sign-in (p23):** source only; check once with a signed-out profile.
- **Satellites 3D / Flights 3D (p17):** rendered in real Chrome GPU, scene looks right; chrome issues #6, #25, #26.

## Cross-page patterns (fix once in shared CSS)
1. **Tap target**: `phone.css:9` and `redesign.css:375,305` override `--tap` to 36–40. Remove; add one lint rule (`min-height:var(--tap)` on `button, .ds-chip, input, select`).
2. **Chip rows**: `.ds-chips` / `.arc-chips` / `.zchips` scroll silently. Add one `.ds-chips` mask-image fade and reuse it; delete the two local copies (`archive.css:44`, `redesign.css:172`).
3. **Type scale floor**: nothing under `--fs-12`. grep for `font-size: 1[01]px`, `0.6`–`0.7rem`, `font-size:0` hacks (`redesign-pages.css:74`) and map to tokens.
4. **Row anatomy**: one `.ds-row` (icon · title+1 line · trailing) for Settings, Community, Overhead, Flights lists; no nested cards.
5. **Empty/loading values**: a shared `.ds-empty` (icon + muted dash) replaces "--" and blank chips; and a shared long-text empty state that wraps.
6. **Page header**: one component for subpage header (back 44 px, icon, title) — Detection and Logs deviate.
7. **Scrollbars**: themed thin scrollbars on every `overflow:auto` panel (white native bars appear in Events, Satellites 3D list).
8. **Narrow-width tables**: any table at ≤600 px becomes card rows (Focus, Logs).
9. **Icons**: keep one set (current ds icons are consistent); the stray diagonal-arrow and unlabeled "+" need tooltips/`aria-label`.
10. **Desktop width**: use `--content-max` 1280 with two-column grids instead of a centred 400–900 px strip (Live, Community).

## Screenshots
`docs/design/critique/`: `d1568-{sky,overhead,archive,settings,community,flights3d,satellites3d}.jpg`, `m390-{settings-focus-table,settings-community,settings-admin,obs-dock}.jpg`.

Questions skipped: a worker does not ask the owner; sign-in, red mode and `/full /focus /calibration` left for a follow-up pass.

---

## Fix status (2026-11-23)

Commits on main: 609c22c (P1 batch), 0cd61ea (shared tier). Deployed to the Pi, md5 verified.

| Item | Status |
|---|---|
| P1 #1 Focus subtitle | done (`settings_home.js:13`) |
| P1 #2 Focus table at 390 | done (`css/focus.css`, columns hidden <600px, 44px button) |
| P1 #3 Archive toolbar wrap | done (`css/archive.css:6`) |
| P1 #4 Community collapse | done, verified live (`css/community.css:3`) |
| P1 #5 Logs collapse + paging | done, 30089px to 5838px (`activity.js`, `css/activity-log.css`) |
| P1 #6 Satellites empty state | done (`satellites3d.html:1234`, `css/scene3d.css`) |
| Pattern: 44px targets | done for presets, settings selects, page-back (`phone.css:9`, `redesign.css:305,375`, `redesign-pages.css:7`) |
| Pattern: 12px type floor | done across css/*.css; remaining 0px are intentional icon-only buttons (`.btn-lock`) |
| Pattern: chip-row overflow cue | done (fade mask, `ds-components.css:305`) |
| Pattern: themed scrollbars | done on Satellites list; other `overflow:auto` panels OPEN |
| Pattern: row anatomy, empty/loading values, unified header, table-to-card beyond Focus | OPEN |
| P2 #7-#20 | partly covered by type floor and 44px fixes; the rest OPEN (OBS dock button heights `obs_dock.html:21,45`, community marker hit area `community.js:116,313`, `.arc-day__head`) |
| P3 #21-#28 | OPEN |
| Extra pass (red mode, /full, /focus, /calibration, Sign-in, 390 3D pages) | OPEN; /gallery, /live, /overhead, /settings at 390 checked: no horizontal overflow (scrollWidth 375) |

### Update 2 (P2 batch + extra pass)

Commit: P2 batch on main (after 0cd61ea), deployed, md5 equal on repo, Pi and allsky.hoan.uk.

| Item | Status |
|---|---|
| #7 | done (44px) |
| #8 | partly: 12px floor done; hide-empty-chip logic OPEN (`css/live.css`, `css/base.css:87`) |
| #9 Live two-column | OPEN (`css/live.css`, `css/ds-tokens.css:76`) |
| #10 | done (chip fade, 12px) |
| #11 | partly: 12px done; arrow glyph replacement OPEN (`css/overhead.css`) |
| #12 | done at <=480px (select full width, `css/redesign-pages.css`); duplicate 2304x1296 badge OPEN |
| #13 row anatomy | OPEN |
| #14, #15 | done (44px inputs and chips; header icon for #detection OPEN) |
| #16 | done via 12px floor; sub 14px OPEN (`css/settings.css`) |
| #17 | OPEN (`css/settings-admin.css`: label truncation, tile grid) |
| #18 | done (OBS dock 44px go-live, zoom, sec-head); status placeholder contrast OPEN |
| #19 | done (44px marker hit area, `min-width:0`) |
| #20 sky mask fill | OPEN: drawn inside compiled bundle `sky/core-D2YY-WWc.js`, needs source repo change |
| #21, #27, #28 | done (12px, 44px back button) |
| #22 | done (cover thumbs) |
| #23-#26 | OPEN |

Extra pass at 390 (/full, /focus, /calibration, /flights3d, /satellites3d, /login, /community, /obs_dock, settings#detection): no horizontal overflow, no visible text under 12px. Red mode and a before/after pixel review of each page were NOT done.

### Update 3 (final)

Deployed, md5 equal on repo, Pi, allsky.hoan.uk.

| Item | Status |
|---|---|
| #9 Live wide layout | done (`css/live.css`, >=1400px: 1600px max, 420px panel); `critique/after-live-1568-red.jpg` |
| #8 empty live-events status | done (hidden when empty); lock chips keep "Auto" label |
| #17 Admin tiles | done (2-col at <=480px, `css/settings-admin.css`); label truncation OPEN: needs per-row markup check |
| #25 Flights 3D LOCAL pill | done (colour dot, `css/scene3d.css`); rows already 44px |
| Red mode | checked on Live at 1568: all surfaces and text stay in the red palette, no stray blue |
| #20 sky mask fill | OPEN with reason: the polygon is drawn inside the compiled sky engine (`src/web/sky/core-D2YY-WWc.js`); source is `tools/skyview/src` and needs a rebuild through its vite config, not edited by hand |
| #11 arrow glyph, #12 duplicate badge, #13 row anatomy, #14 header icon, #16 sub 14px, #18 placeholder contrast, #23 chart colour, #24 map attribution, #26 satellites header | OPEN with reason: each needs markup or JS changes across several pages; deferred to avoid regressions without per-page screenshot review |
| Before/after screenshots | only Live 1568 (red) saved; per-page 390 px screenshots were replaced by scripted overflow and 12px checks (see Update 2) |

## Update 4 - Live follow-ups (PM review at 1568 px)
| # | Defect | Status |
|---|---|---|
| L1 | Sky tile showed "Day" at night on first paint (local sun calc used default station position) | Done: tile is neutral (clock icon, dimmed, "--") until the server sun altitude arrives; local fallback only after 20 s. Live check: Night from the first sample, never Day. Before: Day/sun. |
| L2 | "Live from the public network" link default blue | Done: ds link (accent colour, underline, 44px target) with arrow-up-right icon, aria-label "(opens in a new tab)", no raw URL text. |
| L3 | Wifi tile icon only | Done: visible "Online"/"Offline" value plus title and aria-label. Other tiles show "--" only while data is loading. |
| L4 | `/#archive` stayed on Live | Done: hash aliases (archive, gallery, events, settings, calibration, logs, activity, focus) redirect to the page URL (Archive is `/gallery`). Verified `/#archive` lands on `/gallery`, `tab-gallery`. |

## Update 5: dialog/sheet scrollbars and sizing (Community station dialog)
- Global themed thin scrollbar in `ds-tokens.css` (`scrollbar-width: thin`, ds colour tokens, webkit 8px, no arrows). Covers every scroll area on every ds page (sheets, chat, archive viewer, admin, scene3d lists), so the remaining `overflow:auto` boxes inherit it; none bypasses it.
- `.ds-sheet__panel`: dvh-bounded, `scrollbar-gutter: stable`, scrolls only the body; sticky grab + header (X stays fixed).
- Community station dialog: image box follows the image aspect (cover, max 36dvh, no bands), tighter rows. Fits without scrolling at 1568x757 and a true 388 px iframe (scrollHeight == clientHeight).
- Screenshots: `docs/design/critique/community-dialog-after-1568.jpg`, `community-dialog-after-390.jpg`.
