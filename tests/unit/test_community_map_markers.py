"""Community map: markers must stay glued to their lat/lon.

Regression (2026-11-24): `.cm-mk { position: relative }` overrode Leaflet's `.leaflet-marker-icon { position: absolute }`,
so each marker was laid out in normal flow and shifted by the heights of the markers before it (cluster "3" landed on
Sumatra, "2" in Mexico). Basemap and markers share ONE projection (Leaflet EPSG:3857, `map.latLngToContainerPoint`).
"""
import math
import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[2] / "src" / "web"


def merc(lat, lon, zoom):
    """Leaflet EPSG:3857 world pixel at `zoom` (256 px tiles)."""
    s = 256 * 2 ** zoom
    x = (lon + 180) / 360 * s
    y = (0.5 - math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) / (2 * math.pi)) * s
    return x, y


def test_projection_reference_points():
    # Hanoi sits north-west of the equator-line Sumatra point and east of Arkansas; Jasper is on the 93.2W meridian
    hx, hy = merc(21.03, 105.85, 2)
    sx, sy = merc(0, 101, 2)
    jx, jy = merc(36.0, -93.2, 2)
    assert hy < sy - 60 and hx > sx          # Hanoi well north of Sumatra
    assert abs(jx - (256 * 4) * (180 - 93.2) / 360) < 1e-6
    assert 395 < jy < 410                     # ~36N at zoom 2 (1024 px world)


def test_marker_css_does_not_leave_leaflet_positioning():
    css = (WEB / "css" / "community.css").read_text()
    for m in re.finditer(r"([^{}]*\.cm-mk[^{}]*)\{([^}]*)\}", css):
        sel, body = m.group(1).strip(), m.group(2)
        if "::" in sel:
            continue
        pos = re.search(r"position\s*:\s*(\w+)", body)
        assert not pos or pos.group(1) == "absolute", f"{sel!r} sets position:{pos.group(1)}: breaks Leaflet marker placement"
