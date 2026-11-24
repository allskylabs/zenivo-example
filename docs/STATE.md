# STATE: imx335-allsky (Zenivo)

Updated 2026-11-24 by worker wonderful-cannon. Release tag v1.3.0-beta.6 (main 7563a0e, pushed; no GitHub release, as for beta.5). Live on sky.hoan.uk and allsky.hoan.uk: 1.3.0b6, md5 checked on both hosts. Backups: mini-PC ~/zenivo-bak-*.tgz, Pi ~/zenivo-src-bak-*.tgz and zenivo-web-bak-*.tgz.

## Owner decisions
- **UI: full clean rebuild on the ds system, no more patching the old UI.** Mockups (23 pages) in docs/design/mockups/ and the owner's opnai account (permanent, never edit).
- **Icons first WITH short visible labels.** Never hide status, values, names or menu labels in a tooltip; long text goes behind an info icon. Text >= 12 px.
- **One function, one control per page** (dedupe rule, docs/design/ui-critique.md Update 10b): no second link/button for the same destination; the sidebar owns navigation, Settings list owns the rest.
- Deploys: static web = rsync to Pi + mini-PC, md5 check, no restart. Any Pi service restart: tell the PM first (502 for ~20-90 s).

## Done
- UI rebuild on ds for all pages: Live, Overhead, Sign-in, Archive + event viewer, Sky, Community, Admin, Logs, OBS dock, full.html, Flights/Satellites chrome, Settings.
- Sidebar: primary nav + one Settings item, Admin for admins only, collapse toggle + Ctrl+B, smooth expand/collapse animation, collapsed-rail tooltips, phone Menu sheet with the same grouping; Settings deep links (`/settings#community`) kept.
- Topbar: old-style labelled chips (status + fps, mode, UTC/Local clocks, Red Vision), visible captions restored.
- Event grid and viewer; Overhead card header (count chips, filter segment, sort).
- Object info card: anchored popover with arrow, Alt/Az/RA/Dec/Rise/Set labels, text actions, Esc closes, bottom sheet on mobile.
- Full-width desktop pages, By-day placeholders, compact toasts, hidden-attr fix (layers sheet uses data-open).
- icons.svg `?v=` fix: asset rewriter ate the `#fragment` of sprite URLs (src/api/main.py); needed a Pi service restart.
- Admin pane: version label, wrapping, autofill style, sign-out rows.
- Earlier: focus peaking, star-boost cloud grain fix, cloud cover day method + labelled set, events detector gates + owner labels (audits in docs/design/).

## Open
- **Skyview not rebuilt via vite:** deps `astrophoto` and `hoan.uk/client/node_modules` are missing on the laptop and the runners. The bundle was patched by hand and the source patched in tools/skyview/src/layers.ts:343; a real rebuild needs those deps.
- 390 px screenshots not saved: toasts, day tiles, object info card, Settings/Admin highlight in the Menu sheet (only docs/design/critique/nav-390-menu-sheet.jpg exists). The Menu sheet clips OBS dock/Settings below the fold (scroll), and the Menu tab's data-paths includes /settings.
- Owner phone review pending.
- Detector follow-ups: measure gates on real new events after a few days and tune from owner labels; add mislabelled cloud frames to the set; night moon exclusion not done.
- Legacy: some pages still load style.css + redesign*.css (ds_lint --report shows hex/px/z-index there); red mode on every page, a11y pass, loading/empty/error states not verified.

## Workers
None running. Max 3 at once.
