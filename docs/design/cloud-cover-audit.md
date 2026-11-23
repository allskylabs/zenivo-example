# Cloud cover audit (phase 1, no algorithm change)

Trigger: owner says cloud-% detection is poor day and night. Data: `cloud_history.jsonl` from the Pi (11014 lines, 11-16..11-23) plus 13 real frames viewed at 640x360.

## 1. How it works today
- `src/analytics/cloud_cover.py` runs on a copy downscaled to `analysis_max_width`, internally `WORK_WIDTH=160`. Sampled every 300 s (`src/api/service.py` `_sky_stats`), cached between; **no temporal smoothing**, one frame = one history row `{t,cc,m,conf,stars,sun,clar,shift}` in `data/logs/cloud_history.jsonl` (buckets are averaged only for charts, `cloud_history.py`).
- Features: log(B/R) (colour, day and night) and log luminance (night only, `sun_elev <= -6`).
- Method is **relative / structure-based**: per frame fit a quadratic "clear-sky envelope" to the bluer (or darker) side with iterative 1-sigma refit (`_envelope_fit`, l.75-90), after subtracting a learned per-pixel template (learned only at `sun < -12`, cloud < 0.35). Pixel is cloud if `score > 1` with margin = max(fixed 0.03/0.04/ln1.2, 3 sigma noise) (`cloud_map` l.213-269). Cloud % = flagged / valid sky pixels.
- Night extra: if a plate-solve `star_ref` exists, `star_clarity.py` counts catalogue stars (mag<=3.4, alt>=8) with a peak hit (>4.5 sigma, >6 DN), searching +-4 % pointing shift; `cc = max(cc, 1 - clarity/0.75)` (l.340-349). Star count itself is display only.
- Mask: station sky polygons, ROI, lens circle, stamp box, eroded 3x3 (`sky_work_mask` l.183-210). Saturated px (>=248) dilated and dropped; frame "unknown" if sky_level<20, valid<50 px or sky_frac<0.3; conf x0.5 if saturated>5 %, x0.6 in twilight.
- **No sun exclusion beyond saturation, no moon handling, no absolute sky colour** (auto-WB hides it, documented weakness). Status: clear<25, scattered<70, else overcast.

## 2. Frames: system vs my estimate
Pi local time (UTC+7). Frames are 1296x730 AVIF. My % is a visual estimate (oktas/8).

| Frame | Condition | System % | My % | Error |
|---|---|---|---|---|
| 1121_060121 | dawn, uniform grey overcast | 4.1 (conf .31) | ~95 | **-91** |
| 1121_120422 | day, broken/overcast, sun glare | 48.3 | ~88 | -40 |
| 1121_130020 | day, partly cloudy (blue gaps) | 63.3 | ~60 | +3 |
| 1121_140107 | day, thin uniform overcast | 2.3 | ~95 | **-93** |
| 1122_070103 | morning, altocumulus layer | 29.6 | ~95 | **-65** |
| 1122_120129 | day, hazy, cloud+glare top right | 10.4 | ~30 | -20 |
| 1122_160431 | late day, clear/haze | 30.2 | ~5 | +25 |
| 1122_180017 | twilight sun -11, clear | 0.1 (conf .36) | ~3 | ok |
| 1122_200102 | night, clear/hazy | 1.3 | ~5 | ok |
| 1122_210230 | night, thick cloud NE half | 33.3 | ~65 | -32 |
| 1122_220325 | night, broken cloud | 78.7 | ~75 | ok |
| 1121_220313 | night, mostly clear, few stars | 10.3 | ~15 | ok |
| 1121_230414 | night, uniform veil, 0 stars | 84.0 | ~80 | ok |

Missing: no stored frames at 03:00-05:00 and none with moon found in the sample, so **moon and deep-twilight cases are not covered**; history shows many 85-100 % readings at 00:00-05:00 with clarity 0.0-0.1 that still need a frame check (clouds or moon/haze). Day values flip within an hour (11-21: 48 -> 63 -> 2 at 12:04/13:00/14:01), consistent with noise, not weather.

## 3. Root causes (biggest errors first)
1. **Relative method cannot see uniform overcast** (`cloud_cover.py` `_envelope_fit` l.75-90, `cloud_map` l.213-269). The "clear" envelope is fitted to the same frame; when the whole sky is cloud the fit follows the cloud, score stays <1 and cc ~ 0. Errors of -65..-93 on frames 060121, 140107, 070103. The module's own docstring admits this. Confidence 0.3 only softens it (l.351-359) but the value is still stored and plotted.
2. **Colour feature is washed out by auto-WB and haze**; margins 0.03 (day) are fixed (l.40-53), so thin/low-contrast cloud (frames 120422, 120129) falls under the threshold, while sky gradients/haze towards the horizon (160431, warm horizon) exceed it (+25).
3. **No sun handling**: saturation mask only. Glare/halo near the sun (120422, 120129) sits next to cloud and distorts the envelope fit; the bright halo is neither excluded nor modelled.
4. **Night star check works only with a star_ref and is merged by max()** (l.340-349), so any stale/mispointed solution or moon glow gives clarity ~0 -> 100 % (suspect for the 00:00-05:00 100 % runs). No moon-aware expectation (faint stars vanish in moonlight without clouds).
5. **No temporal smoothing/persistence**: single-frame outputs every 300 s, thresholds on noisy 160 px maps; hence the day flicker.

## 4. Proposed method
### Day (sun > -6): absolute sky/cloud classification
- Work at 160-320 px. Per pixel: r=R/(R+G+B), b=B/(R+G+B) and brightness; **cloud = low blue-red ratio (B/R below ~1.15) or high luminance with low saturation**; clear sky = high B/R, saturated. Use a fixed, once-calibrated threshold on WB-locked frames (store the WB gains or use the ISP colour gains from telemetry to undo auto-WB), the way Long/Slingo "red-blue ratio" methods work, plus a sky-reference sample from clear frames to calibrate per-station.
- **Sun exclusion**: compute sun azimuth/elevation (already have `sun`), project to the frame, mask a disc ~8-10 degrees plus the saturated region; count cloud only outside, report coverage over unmasked area.
- Keep the current relative score only as a secondary check for scattered cloud; take `max` only when the absolute test is reliable (not twilight).
- Add smoothing: median of last 3-5 samples, and hysteresis already in `_cloud_event`.
- CPU: numpy/cv2 on 320x180 = well under 5 ms/frame on the Pi (current code is similar); run every 60-300 s, negligible.
- Validate: label ~60 frames by eye (clear, scattered, overcast, haze, sun-near), compare error and confusion; target MAE < 10 % and no cloudy frame under 50 %.

### Night (sun < -12): star visibility vs expectation, moon-aware
- Keep `star_clarity` but expected set = catalogue stars brighter than the **limiting magnitude for current sky background** (moon altitude/phase and angular distance from the moon via ephemeris; limiting mag from measured sky level), not fixed mag 3.4. Count seen/expected only on stars in clear-of-moon sectors.
- Compute clarity per sky tile (e.g. 4x4) to give a spatial cloud map instead of one global number, then combine with the luminance/colour map by `cc = tile-based union` not global max.
- If the plate solve is stale (age > N h or no match found) do not use clarity; fall back to luminance/colour and lower confidence, never output 100 %.
- CPU: the peak map already runs; tile counting is a bincount, <10 ms.
- Validate: nights with and without moon (check frames at 00:00-05:00 flagged 100 %), compare with the 5 min star-count series, and with any external reference (weather API cloud cover for the station, a free sanity check, not ground truth).

### Both
Offline harness: reuse `cloud_backfill.py` on labelled frames, produce table + confusion matrix before changing the live code. Phase 2 = implement behind a flag, compare old vs new on the labelled set.
