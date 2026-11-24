# Map controls (Flights 3D, Community)

Bug: MapLibre bottom-right controls were default dark-navy round buttons with black icons (black SVG background-image); attribution was a white bar overlapped by the Camera pill.

Fix (`css/scene3d.css`, `css/community.css`, `flights3d.html`):
- One ds control group per stack: `--c-surface-1` bg, `--c-border`, `--r-md`, 44px buttons, Lucide icons via mask + `currentColor` (always light on dark).
- States: hover `--c-surface-2` + #fff, active `--c-accent`, focus-visible 2px `--c-focus`, disabled .4.
- Attribution: ds pill, 12px, collapsed to an info chip (28px) bottom-right inside the card; expands only on click and then hides the camera pill. aria-labels on the globe button.
- Leaflet zoom (Community) restyled the same way; Leaflet's light `:hover`/`.leaflet-disabled` backgrounds overridden.
- Satellites 3D (three.js) and Overhead (canvas) have no map controls: nothing to change.

Checked in real Chrome (computed colours): dark icon rgb(230,237,247) on rgb(18,27,45) = 14.6:1; hover #fff on rgb(22,34,56); red mode rgb(255,107,107) on rgb(20,4,4) = 7.2:1. 390px (iframe): groups 44px wide buttons, chip collapsed, inside card. Screenshots: `map-controls-*.png|jpg`.
Not separately captured: 2000px, disabled state on MapLibre (same CSS rule as Leaflet).
