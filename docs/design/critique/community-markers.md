# Community map markers (2026-11-24)

**Cause:** `src/web/css/community.css:76` `.cm-mk { position: relative; }` (added with the 44px hit area) beat Leaflet's
`.leaflet-marker-icon { position: absolute }`. Markers fell into normal flow inside the marker pane, each shifted down by the
heights of the markers before it (+26 px for the 2nd, +58 px for the cluster after it): "3" landed on Sumatra, "2" in Mexico, UK on Ireland.
Not a projection or resize bug: Leaflet 1.9.4 (EPSG:3857, `map.latLngToContainerPoint`) is the single projection for the canvas basemap and markers.
Measured in Chrome: marker centre vs `latLngToContainerPoint` was +0/+58 px before, 0/0 after, also after hiding the list (L key; canvas 1582x858 = 1.2x card).

**Fix:** `.leaflet-marker-icon.cm-mk { position: absolute }`, css `?v=20261204s`. Guard: `tests/unit/test_community_map_markers.py` (reference mercator points + CSS must not override marker positioning).
Verified live: Hanoi cluster over N Vietnam (1225,475 = projection), Arkansas cluster at Jasper (93,378), UK dot on England. Screenshot: `community-markers-after.jpg`.
Flights 3D / Overhead / Satellites 3D use no Leaflet (own canvas/three), so the CSS cause does not apply.
