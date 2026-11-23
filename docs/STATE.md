# STATE: imx335-allsky (Zenivo)

Updated 2026-11-23 by PM. main = 1b62a84 (local, nothing pushed). Release tag v1.3.0-beta.5 done; later fixes unreleased.

## Owner decisions (2026-11-23)
- **UI: full clean rebuild, no more patching the old UI.** Icons first, less text (see ~/.claude/CLAUDE.md "opnai design rules"). Mockups (23 pages) in docs/design/mockups/ and the owner's opnai account (permanent, never delete/edit). Plan: docs/design/ui-redesign-plan.md.
- Mockup features: build Admin /api/system tiles and Overlay-text switch; drop Airports layer and flight route v1; keep /sky planetarium restyled.
- Deploys: static web = rsync, no restart; any Pi service restart: worker messages PM first (a restart gives a 502 for ~20-90 s).

## Done today
- focus peaking + remembered checkbox (localStorage); release 1.3.0-beta.5.
- star-boost cloud grain fix (CLOUD_NOISE_SCALE 1.5), first-paint star-map overlay guards (05b-sky-reticle.js, full.html).
- cloud cover: absolute day method (b/r p90, sun exclusion, median 5), labelled set tests/fixtures/cloud_labels, audit docs/design/cloud-cover-audit.md.
- events: flash/meteor gates, SGP4 + ADS-B cross-check, fragment grouping, confidence, owner true/false labels (events/labels.jsonl), audit docs/design/events-detectors-audit.md.
- UI S0: ds-tokens/base/components, Lucide sprite, ds-demo.html, ds lint (1b62a84).
- Facebook: release comment posted on the Allsky Camera group post; new group "Zenivo Allsky" created by Worker-1 (cover not yet committed: docs/zenivo-group-cover.jpg).

## Done: UI rebuild on ds (2026-11-23)
Live, Overhead, Sign-in, Archive+event viewer, Sky, Community, Admin, Logs, OBS dock, full.html, 3D page chrome (Flights/Satellites), Settings 3a/3b, cleanup (112 dead legacy CSS rules pruned, redesign-data.css + ds-demo-phone.html deleted, ?v=20261202a). Overhead sits under Menu by design (5-tab shell); the Menu tab highlights.

## Remaining (unverified / open)
- Plan section 9: legacy index.html pages (Live/Archive/Settings/Calibration/Focus) still load style.css + redesign*.css/pages.css/phone.css (live rules); ds_lint --report still shows hex/px/z-index in those, flights3d.html scene colours (44 hex), sky_catalog.js (99 hex), app/*.js canvas hex. Default ds_lint = 0.
- Red mode on every page, Lighthouse-style a11y pass, loading/empty/error states per page, `data-ui` flag check: not verified.

## In progress
nothing. Next: detector follow-ups: measure gates on real new events after a few days; tune from owner labels. Cloud: watch a few days, add mislabelled frames to the set. Night moon handling not done.

## Workers
All workers idle. Max 3 at once.
