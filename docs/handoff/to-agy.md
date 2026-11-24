# Handoff to Antigravity: imx335-allsky (Zenivo)

Written 2026-11-24 by the PM. Read docs/STATE.md first; this file is the self-contained brief.

## Goal
Zenivo = all-sky camera software (FastAPI backend + static vanilla-JS web UI on the "ds" design system). Hosts: mini-PC (IMX335, sky.hoan.uk) and Raspberry Pi (imx708, allsky.hoan.uk). Release 1.3.0-beta.6 is live on both. Keep polishing the UI to the owner's standard and finish the open items below. Owner talks Vietnamese; code, commits and docs are English.

## Repo and branch
- Repo: `/home/hoan/DATA/imx335-allsky`, branch `main` (released and pushed; tag v1.3.0-beta.6). Work on a branch, commit immediately after each verified change. Push/PR only when the owner asks.
- Web: `src/web` (pages in `src/web/pages/*.html` -> generated `index.html` etc. by `scripts/build_index.py` and `scripts/sync_nav.py`; CSS `src/web/css/ds-*.css`; JS `src/web/app/*.js`, `shell.js`, `split.js`). Gates: `python scripts/build_index.py --check`, `python scripts/sync_nav.py --check`, `python scripts/ds_lint.py` (hex colours, font < 12 px, radius, z-index), `pytest tests/unit`.
- Cache-busting `?v=` strings must be bumped when CSS/JS changes.

## Next tasks
1. Community stations dedupe (backend `src/api/routes` community + `src/community*`, UI `src/web/pages/30-settings.html`/community page): stale duplicate rows (same station seen under old ids: Jasper Arkansas, Hanoi Vietnam) and the station's own seed shown with "answered 253 h ago". Dedupe by station id/normalised URL, hide self, show "this station". Add a unit test.
2. 390 px review of all pages (Live, Sky, Overhead, Archive, Community, Flights 3D, Satellites 3D, Settings + subpages, Menu sheet) in real Chrome via an iframe or device emulation; save screenshots to `docs/design/critique/`; fix overflow/clipping; chat FAB + keyboard behaviour (`src/web/chat-widget.js`).
3. Cloud cover follow-ups (`src/.../cloud_cover.py`: `sky_state_cover` ~line 308, `service.py` `_smooth_day_cloud`): on the first clear day check the Pi imx708 white balance/blue-share test, add per-sensor calibration in config if needed; re-run the audit (`docs/design/cloud-audit.md`, fixtures in `tests/fixtures/`).
4. Events detector: after a few days of new events, measure gates (docs/design audits) and tune from the owner's labels; add night moon exclusion.
5. Legacy cleanup: pages still loading `style.css` + `redesign*.css`; run `ds_lint --report`, migrate to ds tokens; red-vision mode on every page; loading/empty/error states.

## Constraints (owner rules)
- No local Gradle or Cargo builds/tests (hooks block them); Node heavy work only with `NODE_OPTIONS=--max-old-space-size=512`, never headless WebGL loops on the laptop. Builds/tests that are heavy go to the `lan` CI runners (CT 236/237).
- WebGL (3D pages, MapLibre/three) is verified only in real Chrome (GPU), not in a headless/software-GL browser.
- AI images/music/video only through opnai (api.opnai.net/v1 or the Opnai connector); designs on opnai are permanent: never delete/rename/edit; keep PNG copies in `docs/design/`.
- UI rules: icons first WITH short visible labels (never hide key info in tooltips), text >= 12 px, 44 px touch targets, one function = one control per page, dark surface + light icons, no hex colours (ds tokens), mobile 390/360 px checked.
- Deploy (standing permission for the owner's own hosts): rsync from `main` HEAD only, back up first (`src.bak-<ts>`), md5 check, then restart only if Python changed. mini-PC: `ssh hoan@minipcv6.hoan.uk` (path `/home/hoan/zenivo`, service `zenivo`, port 8080, never reboot). Pi: `ssh -J hoan@minipcv6.hoan.uk pi@100.127.30.11` (path `/home/pi/zenivo`, port 8095, restart takes ~1.5 min). 192.168.3.24 is not the Pi. Verify `/api/status` version and md5 on both hosts afterwards.
- Never commit secrets (admin_setup_code, tokens). The admin password is set by the owner only.

## Known facts
- The skyview 3D bundle (`tools/skyview`) cannot be rebuilt with vite (deps missing); patch source and the built bundle consistently.
- `fix/reticle-first-paint` has 1 unmerged docs commit (Facebook group cover), leave unless the owner says.
