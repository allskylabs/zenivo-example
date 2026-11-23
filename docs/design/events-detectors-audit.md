# Events and detectors audit (Phase 1, audit only)

Date 2026-11-23. Data: Pi store, last 7 days (2026-11-15..22), 332 events via `GET /api/events`; 40 sampled and labelled by eye from thumbnails (one reviewer, so precision numbers are estimates, not ground truth). No algorithm changed, nothing deployed.

## 1. Code map (12 lines)

1. `SkyEventDetector` (src/analytics/sky_events.py, ~1000 lines): works on a 640 px grey copy; two-frame difference, threshold = max(8 DN, 4 sigma of robust noise); Hough streaks, collinear segments merged.
2. Meteor: single-frame solid streak. Gates in `_meteor_ok` (:188): no cloudy sky, not on a tracked plane path, no repeat spot (3 % of width within 3 h), elongation >= 5; bright fuzzy streak (peak >= 90 DN) is re-typed plane.
3. Plane/satellite: dot tracks (net move >= 10-12 px, straightness 0.6-0.75), rejected as star drift (:763, duration > 1200 s or speed < 0.5 px/s), common-drift cloud test, faint/short/texture tests.
4. Type decision (:793): `plane` if blinking or peak >= 60 DN, else `satellite`. Brightness heuristic only, no orbit check.
5. Flash (:363): sky mean jumps x1.6 + 8 DN over most of the sky. No exposure/AE or lamp test.
6. Sun gating: night below -2 deg; by day only plane + flash (`EVENT_DAY_TYPES`), plus birds/insects/rain tracking in day_objects.
7. Flight naming: `_match_flight` (src/api/traffic_mixin.py:682) matches own ADS-B to plane events after detection; it does not change type.
8. Storage: `_record_sky_event` (src/api/events_mixin.py:132) writes `events/<day>/events.jsonl` + `<id>.jpg` + `<id>_sky.jpg`; cap 1000/day; pruned at 90 days. Fields: type, start/end, label, line, frames, length_px, peak_dn, flight. No confidence, no score, no owner label.
9. API: `/api/events`, `/api/events/meteors`, `/api/events/img/...`, `/api/events/config` (per-type switches).
10. Viewer: src/web/app/08b-events-live.js (modal event detail, prev/next, zoom/pan), Gallery & Events page (tabs Products / By day / Events, type chips), Recent-events card in index.html, Overhead page.
11. Satellites exist separately for the overlay (src/analytics/satellites.py, SGP4) but are never used to classify events.
12. Legacy src/analytics/meteor.py is tests only.

## 2. Real data, 7 days

| Type | Count | Est. precision | Typical false-positive causes |
|---|---|---|---|
| plane | 310 (122 with ADS-B flight) | ~75 % real aircraft, but count inflated by fragments | one flight split into several events (THA565 x7, SPQ883 x4); 61 events start < 120 s after a previous plane event; 88 have <= 3 frames; isolated dots near building lights |
| satellite | 11 | unknown, guess 30-50 % | sparse faint dot chains typed by brightness only; look like distant aircraft; nothing verifies against SGP4 |
| meteor | 7 | ~15-30 % (1 true, 4 aircraft, 2 unsure) | aircraft landing lights/strobes (double parallel lines, dashed streaks, thick bars), thin faint line that may be a satellite |
| flash | 4 | ~0 % | all four within 40 s on 11-21 20:05: saturated purple/white frames, exposure/IR or lamp change, not lightning |

Counts per day: planes 41/115/7/28/26/59/34 (11-16..22), 115 on 11-17 is a fragment storm; meteors 1/2/0/2/1/1 over the week.
Labels of the 7 meteors: TRUE 1 (11-17 02:29 thin tapered streak), aircraft 4 (11-21 18:08, 11-19 18:49, 11-17 22:09, 11-16 23:07), UNSURE 2 (11-19 17:52, 11-20 00:40, the latter near a street lamp).
Missed events: not quantified; there is no raw-frame archive to search, so recall is unknown (gap, see proposal 4).

Top causes: (1) no cross-check against ADS-B/SGP4 for meteor and satellite types, (2) no track continuity merge so aircraft fragment, (3) flash has no exposure/lamp test, (4) type from brightness only, (5) no confidence and no label feedback loop to measure any of this.

## 3. Ideas worth borrowing from allskyhub (own words, ideas only)

- Decide a meteor one frame late: a real meteor shows in exactly one frame, anything that continues into the next frame or came from the previous one is an aircraft or satellite.
- Guard rails: minimum length and elongation, spots that recur (hot pixel, bright star), and many candidates in one frame (cloud edge, wind) cancel the frame.
- Each event stores a confidence, sky direction (az/alt from the plate solve), track endpoints and the fit error, and only trusted when the clock is trusted.
- Shower matching: the path should point back to a radiant and must not cross it; fall back to date only when orientation is unknown.
- Cross-checks that auto-label false positives: aircraft nearby from ADS-B; a satellite whose predicted path, in sunlight and above 5 deg, passes within ~1 deg of both track ends (gives name and NORAD id); lightning data for flashes; lamp-coloured flashes that repeat at the same spot are a light source.
- Owner true/false label overrides automatic label, giving a labelled set to tune thresholds.
- Long phenomena as one episode with a stable id updated in place; retry/backoff for external lookups.

## 4. Event viewer UX audit

Seen on https://allsky.hoan.uk/gallery (Events tab) and the detail modal, desktop 1568 px.

1. Type tabs are mixed with a three-way view switch (Products / By day / Events) and chips; two nested filter rows, unclear which is which. Fix: one segmented control for view, one chip row with counts per type.
2. No counts on chips; "Showing the newest 100 of 500" gives no way to page, filter by date or load more. Fix: counts, date range, infinite scroll.
3. Every card is titled "Aircraft" with only time and duration; 36 near-identical tiles per day, no flight number, no confidence. Fix: show flight/callsign when matched, a quality badge, group fragments of one flight.
4. Unread blue dot on every card with no explanation and no way to clear it. Fix: tooltip + "mark all seen", or remove.
5. Cards are tiny crops with no scale; sparse dot tracks look like noise. Fix: draw the track polyline over the thumbnail, larger tile on hover.
6. Today empty block ("0 detected") takes a full card; day cards are not collapsible. Fix: collapse empty days, sticky day headers.
7. Detail modal: close-up has large black letterbox bars wasting half the image; whole-sky pane crops the bottom horizon. Fix: fit crop to its aspect, show the box fully.
8. Detail modal gives no way to judge or correct: no "real / not real" action, no reason for the type (why aircraft vs satellite), only Delete (admin). Fix: true / false-positive / "it was X" buttons feeding the label store; show detection reasons.
Also: fact tiles use mono small caps with weak contrast; Delete is the only coloured action; no keyboard hint in the UI although arrows work.

Unpolished feel (vs docs/design/ui-redesign.html mockup): cards lack consistent spacing rhythm (tile gap, title/time baselines), same-weight text everywhere so hierarchy is flat, the mockup's tap-for-info pattern and plain chips are only half implemented (emoji pills remain), many near-identical dark blues with low contrast between card and page, no empty/loading skeletons (the page is blank until data arrives, seen on first load).

## 5. Proposal, ranked

| # | Improvement | Gain | Effort | Files | Risk |
|---|---|---|---|---|---|
| 1 | Cross-check types after detection: ADS-B nearby or SGP4 pass over the track window relabel meteor/satellite to plane/satellite(name), unknown stays; merge plane fragments by track continuity/flight id | removes most meteor FP (4 of 7) and the plane count inflation (~20-40 %), gives real satellite precision | M | src/analytics/sky_events.py, src/api/traffic_mixin.py, src/analytics/satellites.py, events_mixin.py | medium: needs good orientation/time; keep original type in a field to allow rollback |
| 2 | Meteor gate vs aircraft: reject dashed/double-line/thick bars, require single-frame + not continuing; decide one frame late | precision 15-30 % to a usable level | S-M | sky_events.py `_meteor_ok`, `_pending_event` | low-medium: may drop real faint meteors, measure on labelled set |
| 3 | Flash gate: ignore when exposure/gain changed, IR-cut/lamp-colour or repeat at same spot; require brightness not saturated whole frame | removes 100 % of the flashes seen | S | sky_events.py :363 | low |
| 4 | Confidence + az/alt + track fields stored per event, and owner true/false label buttons writing a labelled set (jsonl) | enables measurement and tuning; needed for all later work | M | events_mixin.py, API.md, 08b-events-live.js | low |
| 5 | Viewer redesign: grouped by flight, counts, filters, track overlay, better detail modal, per mockup | clearer UX, fewer tiles | L | src/web/app/08b-events-live.js, css/events.css, gallery | low: pure UI, deployable via rsync |

Order of work: 3, 2, 1, 4 (tests on labelled set), then 5.
