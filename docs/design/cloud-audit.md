# Cloud detection audit (2026-11-24)

Owner report: cloud detection is "badly wrong". This audit measures how wrong, finds the causes, and records the fix.

## 1. What the owner sees, and the mask question
Value path: `analyze_cloud_cover` (src/analytics/cloud_cover.py) -> `AllSkyService._sky_stats` (src/api/service.py) -> `stats["cloud_cover_pct"]` -> status_mixin.py:177-178 -> Live telemetry `telemClouds` (src/web/app/06-telemetry.js:165-168), 07-cloud.js, 08b-events-live.js; timelapse overlay "Clouds: N%" (src/pipeline/timelapse.py:69-70); archive/day tiles (products_mixin.py:254-262, events_mixin.py:133,220); history in data/logs/cloud_history.jsonl.

Worker-17 (f018f1c/30d4540) restricted counting to the single saved sky mask. That is correct and is **not** the cause: errors come from the colour and star logic, not the mask. The same frames were wrong with any mask.

## 2. Method
70 frames from the mini-PC, 2026-11-22 20:17 to 2026-11-24 12:51 local (every ~35 min, day, twilight, night, moon up, rain/dew), 960x720, stored in tests/fixtures/cloud_audit/ with my visual labels (labels.csv: clear <25, partly 25-70, overcast >=70) and the value the deployed service had recorded. Metric: class agreement % and MAE in percent points. `python3 scripts/cloud_audit_eval.py [-v]` reproduces it; tests/unit/test_cloud_audit.py pins it.

| Group | n | Deployed agree / MAE | New agree / MAE |
|---|---|---|---|
| all | 70 | 38.6% / 45.4 | **88.6% / 10.6** |
| day | 30 | 20.0 / 55.1 | 90.0 / 10.7 |
| twilight | 5 | 0.0 / 75.5 | 80.0 / 14.3 |
| night, moon down | 2 | 0.0 / 82.0 | 100 / 7.0 |
| night, moon up | 33 | 63.6 / 29.7 | 87.9 / 10.1 |

Independent check, older 46-frame labelled set (tests/fixtures/cloud_labels, different white balance): MAE over all frames 30.7 (legacy) -> 18.9 (new); overcast frames reading <50%: 13 -> 0; day MAE unchanged 13.7. The audit labels were tuned in-sample, so this set is the out-of-sample guard.

## 3. Failure modes and causes
a. **Moonlit night read as overcast** (frames 2-7, 11-17 deployed 55-100% on clear sky): star clarity was used as "cloud = 1 - clarity" and moon glare hides stars. Now clarity only caps cloud from above (>=0.12 -> max 20%); stars alone never raise cloud. cloud_cover.py (`STAR_CLEAR_CLARITY`, clarity branch).
b. **Day / dusk overcast read as clear** (frames 33-37, 62-70, deployed 3-25%): pale blue-grey overcast passes the old absolute grey test. Now blue-pixel share (B/R>1.35) combined with white-balance-independent colour spread (p90-p10 of B/R). `sky_state_cover`.
c. **Night city-glow overcast inconsistent** (frames 8-10, 39-57): replaced by median B/R and luminance structure. `sky_state_cover` night branch.
d. **Rain / dew on the dome** (48-61) is overcast-like and is labelled so; not separable from cloud with one image.
e. **Held values**: 92, 88, 41.9, 15.2, 3.5 repeated for hours: 5-min cache, 5-sample day-only median, and `last_cc` fallback (service.py `_sky_stats`). Now median smoothing for day/twilight/night with history reset on method change, status hysteresis +/-5 points. `_smooth_day_cloud`.
f. Frame 0 of the day was black (no value).

## 4. Method (simple, explainable)
Inside the sky mask at 160 px: day (sun>=6) cloud = min(blue-share ramp, colour-spread ramp); twilight (-12..6) uses a stricter blue share; night (<-12) = max(median B/R ramp, luminance-structure ramp); legacy envelope kept as a max for day/twilight. Stars: report, and cap cloud when clearly seen. Confidence is reported by the analyzer. Thresholds are in cloud_cover.py constants.

## 5. Limits
- Absolute B/R thresholds depend on white balance; the older set has neutral balance and needed the spread cue. The Pi (imx708) was checked, see section 7: no per-sensor calibration needed, but no clear Pi sky exists in the set.
- Known misses, both accepted limits: frame 10 (thin moonlit overcast reads 21 vs 95: the cloud is lit by the moon and shows stars/structure like clear sky; telling them apart needs a star solution at night, which the offline set lacks, so the live star-clarity cap covers it); frame 18 (dawn, flat colour reads 79 vs 20: a clear pre-dawn sky has no blue share or spread to measure; only a time-of-day sky model would fix it, not worth the complexity).
- No truly clear day in the audit set; clear-day calibration comes from the older set.

## 6. Per-frame table
| # | local time | sun° | moon° | label | deployed | new | ok |
|---|---|---|---|---|---|---|---|
| 1 | 11-22T20:17 | -42 | 69 | clear 20 | 0 | 0 | ✓ |
| 2 | 11-22T20:53 | -50 | 77 | clear 20 | 55 | 0 | ✓ |
| 3 | 11-22T21:28 | -58 | 84 | clear 20 | 92 | 0 | ✓ |
| 4 | 11-22T22:03 | -66 | 84 | clear 20 | 95 | 0 | ✓ |
| 5 | 11-22T22:38 | -75 | 78 | partly 25 | 91 | 0 | ✗ |
| 6 | 11-22T23:13 | -83 | 70 | clear 15 | 100 | 0 | ✓ |
| 7 | 11-22T23:49 | -89 | 62 | partly 30 | 96 | 0 | ✗ |
| 8 | 11-23T00:24 | -81 | 54 | overcast 95 | 84 | 100 | ✓ |
| 9 | 11-23T00:59 | -72 | 46 | overcast 95 | 88 | 100 | ✓ |
| 10 | 11-23T01:34 | -64 | 38 | overcast 95 | 92 | 21 | ✗ |
| 11 | 11-23T02:10 | -56 | 31 | clear 5 | 92 | 0 | ✓ |
| 12 | 11-23T02:45 | -48 | 23 | partly 30 | 92 | 0 | ✗ |
| 13 | 11-23T03:20 | -40 | 15 | clear 10 | 92 | 0 | ✓ |
| 14 | 11-23T03:55 | -32 | 8 | clear 10 | 92 | 0 | ✓ |
| 15 | 11-23T04:31 | -24 | -0 | clear 10 | 92 | 14 | ✓ |
| 16 | 11-23T05:06 | -16 | -7 | clear 10 | 92 | 0 | ✓ |
| 17 | 11-23T05:41 | -8 | -14 | clear 10 | 92 | 7 | ✓ |
| 18 | 11-23T06:16 | -0 | -21 | clear 20 | 92 | 79 | ✗ |
| 19 | 11-23T06:51 | 7 | -28 | partly 35 | 92 | 51 | ✓ |
| 20 | 11-23T07:26 | 15 | -34 | overcast 80 | 44 | 85 | ✓ |
| 21 | 11-23T08:02 | 22 | -39 | overcast 75 | 44 | 65 | ✗ |
| 22 | 11-23T08:37 | 28 | -44 | overcast 70 | 28 | 100 | ✓ |
| 23 | 11-23T09:12 | 34 | -47 | partly 60 | 46 | 44 | ✓ |
| 24 | 11-23T09:47 | 40 | -49 | overcast 75 | 20 | 92 | ✓ |
| 25 | 11-23T10:23 | 44 | -49 | partly 60 | 49 | 47 | ✓ |
| 26 | 11-23T10:58 | 47 | -48 | overcast 90 | 42 | 100 | ✓ |
| 27 | 11-23T11:34 | 48 | -45 | overcast 80 | 42 | 85 | ✓ |
| 28 | 11-23T12:09 | 48 | -41 | partly 60 | 42 | 44 | ✓ |
| 29 | 11-23T12:44 | 46 | -36 | partly 55 | 42 | 59 | ✓ |
| 30 | 11-23T13:20 | 42 | -30 | partly 25 | 42 | 39 | ✓ |
| 31 | 11-23T13:55 | 38 | -24 | partly 45 | 42 | 60 | ✓ |
| 32 | 11-23T14:30 | 32 | -17 | overcast 80 | 42 | 100 | ✓ |
| 33 | 11-23T15:06 | 26 | -10 | overcast 95 | 15 | 100 | ✓ |
| 34 | 11-23T15:41 | 19 | -3 | overcast 95 | 15 | 89 | ✓ |
| 35 | 11-23T16:17 | 12 | 5 | overcast 95 | 15 | 68 | ✗ |
| 36 | 11-23T16:52 | 4 | 12 | overcast 95 | 15 | 67 | ✗ |
| 37 | 11-23T17:27 | -3 | 20 | overcast 95 | 15 | 100 | ✓ |
| 38 | 11-23T18:02 | -11 | 28 | overcast 95 | 15 | 100 | ✓ |
| 39 | 11-23T18:38 | -19 | 35 | overcast 95 | 52 | 100 | ✓ |
| 40 | 11-23T19:13 | -27 | 43 | overcast 95 | 100 | 100 | ✓ |
| 41 | 11-23T19:48 | -35 | 51 | overcast 95 | 100 | 100 | ✓ |
| 42 | 11-23T20:24 | -43 | 59 | overcast 95 | 44 | 100 | ✓ |
| 43 | 11-23T20:59 | -51 | 67 | overcast 95 | 100 | 100 | ✓ |
| 44 | 11-23T21:34 | -60 | 75 | overcast 95 | 92 | 100 | ✓ |
| 45 | 11-23T22:10 | -68 | 83 | overcast 95 | 92 | 100 | ✓ |
| 46 | 11-23T22:45 | -76 | 89 | overcast 95 | 92 | 100 | ✓ |
| 47 | 11-23T23:20 | -84 | 82 | overcast 95 | 92 | 100 | ✓ |
| 48 | 11-23T23:55 | -87 | 74 | overcast 100 | 92 | 100 | ✓ |
| 49 | 11-24T00:31 | -79 | 66 | overcast 100 | 97 | 100 | ✓ |
| 50 | 11-24T01:06 | -71 | 58 | overcast 100 | 88 | 100 | ✓ |
| 51 | 11-24T01:42 | -63 | 50 | overcast 100 | 88 | 100 | ✓ |
| 52 | 11-24T02:17 | -54 | 42 | overcast 100 | 88 | 100 | ✓ |
| 53 | 11-24T02:52 | -46 | 35 | overcast 100 | 88 | 100 | ✓ |
| 54 | 11-24T03:28 | -38 | 27 | overcast 100 | 88 | 100 | ✓ |
| 55 | 11-24T04:03 | -30 | 19 | overcast 100 | 88 | 100 | ✓ |
| 56 | 11-24T04:39 | -22 | 12 | overcast 100 | 88 | 100 | ✓ |
| 57 | 11-24T05:14 | -14 | 5 | overcast 100 | 88 | 100 | ✓ |
| 58 | 11-24T05:49 | -6 | -3 | overcast 95 | 31 | 95 | ✓ |
| 59 | 11-24T06:25 | 2 | -9 | overcast 95 | 54 | 90 | ✓ |
| 60 | 11-24T07:00 | 9 | -16 | overcast 90 | 54 | 93 | ✓ |
| 61 | 11-24T07:35 | 16 | -22 | overcast 90 | 54 | 100 | ✓ |
| 62 | 11-24T08:10 | 23 | -28 | overcast 95 | 26 | 100 | ✓ |
| 63 | 11-24T08:45 | 30 | -34 | overcast 95 | 5 | 100 | ✓ |
| 64 | 11-24T09:20 | 35 | -38 | overcast 95 | 4 | 100 | ✓ |
| 65 | 11-24T09:55 | 41 | -42 | overcast 95 | 4 | 100 | ✓ |
| 66 | 11-24T10:31 | 45 | -44 | overcast 95 | 4 | 100 | ✓ |
| 67 | 11-24T11:06 | 47 | -45 | overcast 95 | 4 | 100 | ✓ |
| 68 | 11-24T11:41 | 48 | -44 | overcast 95 | 4 | 100 | ✓ |
| 69 | 11-24T12:16 | 48 | -42 | overcast 95 | 4 | 100 | ✓ |
| 70 | 11-24T12:51 | 45 | -39 | overcast 95 | 4 | 100 | ✓ |

## 7. Pi (imx708) check, 2026-11-24
Pi is a 66x41 deg rectilinear camera (no circle, rectangular sky mask), neutral/teal white balance (median B/R 1.00-1.04). 33 frames (18 hourly 11-23/11-24 + 15 from 11-19..11-22: dawn, day, dusk, night), labelled by eye, frozen in `tests/fixtures/cloud_audit_pi`, test `test_pi_imx708_frames_agree`.
- Result: 31/33 class agreement (93.9 %). Overcast day frames read 100, night overcast 68-100. Blue-gap frame (11-21 13:00, label 60) fires the cues (blue_day 0.13, spread 0.48) and reads 62.5.
- Misses: 11-19 23:00 (label 90, reads 68, dark night overcast, no star solution offline) and 11-22 15:00 (hazy teal with a warm sun-side glow, label 85, reads 50: the warm-to-cool gradient raises the spread cue).
- No clear Pi frame exists: the old Pi history shows low cc on 11-19 but the frames are fully overcast (the old method under-read), and frames before 11-17 are not kept. The blue-share/spread thresholds are therefore verified only for overcast and partly-cloudy there. Decision: no per-sensor calibration added (nothing to fit; a config knob without data would be guesswork). Revisit when the first clear Pi day appears.
