"""Packed overlay protocol for the live WebSocket (aircraft, satellites, radiosondes).

One text message per change set instead of a full JSON document per tick:

    {"type": "ov", "l": <layer>, "t": <server time>, "f": 1,            # f = full snapshot (first push after subscribe)
     "m": {<meta keys that changed>},                                   # layer-wide values (totals, source, ...)
     "u": [[<row>], ...],                                               # rows that are new or changed, fields as in FIELDS
     "x": [<id>, ...]}                                                  # ids that left

A row is a plain array in the order of FIELDS[layer]; the first field is the id. Positions on the frame are integers:
fx, fy = frame_xy * 10000 (null when the object is outside the frame), vx, vy = frame_v * 10000 per second.
fl is a bit set: 1 = in view, 2 = above horizon, 4 = visible (satellites: sunlit under a dark sky).
A row is sent again only when one of its values changed, so a geostationary satellite costs nothing after the snapshot.
The page (05c-aircraft-overlay.js, OVERLAY_FIELDS) rebuilds the same objects the REST endpoints return.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

FIELDS: Dict[str, Tuple[str, ...]] = {
    "aircraft": ("icao", "callsign", "altitude_m", "speed_kmh", "heading_deg", "climb_m_min", "az_deg", "alt_deg",
                 "distance_km", "fx", "fy", "vx", "vy", "fl"),
    "satellites": ("norad", "name", "height_km", "az_deg", "alt_deg", "fx", "fy", "fl"),
    "sondes": ("serial", "type", "altitude_m", "climb_m_s", "az_deg", "alt_deg", "distance_km", "fx", "fy", "vx", "vy", "fl"),
}
LIST_KEY = {"aircraft": "aircraft", "satellites": "satellites", "sondes": "sondes"}
META = {"aircraft": ("enabled", "connected", "source_in_use", "public_provider", "total", "in_view"),
        "satellites": ("sun_alt_deg", "disabled"),
        "sondes": ("enabled", "total", "in_view", "error")}


def _q(v: Any) -> Optional[int]:
    return None if v is None else int(round(float(v) * 10000))


def _row(layer: str, o: Dict[str, Any]) -> List[Any]:
    xy, v = o.get("frame_xy") or (None, None), o.get("frame_v") or (None, None)
    extra = {"fx": _q(xy[0]), "fy": _q(xy[1]), "vx": _q(v[0]), "vy": _q(v[1]),
             "fl": (1 if o.get("in_view") else 0) | (2 if o.get("above_horizon") else 0) | (4 if o.get("visible") else 0)}
    return [extra[f] if f in extra else o.get(f) for f in FIELDS[layer]]


def rows(layer: str, payload: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[Any, List[Any]]]:
    """(meta, {id: row}) of a REST-shaped payload. Satellites keep only those inside the frame: the others are never drawn."""
    items = payload.get(LIST_KEY[layer]) or []
    if layer == "satellites":
        items = [s for s in items if s.get("in_view") and s.get("frame_xy")]
    meta = {k: payload[k] for k in META[layer] if payload.get(k) not in (None, "", False)}
    out: Dict[Any, List[Any]] = {}
    for o in items:
        r = _row(layer, o)
        if r[0] is not None:
            out[r[0]] = r
    return meta, out


def delta(layer: str, payload: Dict[str, Any], prev: Optional[Tuple[Dict[str, Any], Dict[Any, List[Any]]]]
          ) -> Tuple[Optional[Dict[str, Any]], Tuple[Dict[str, Any], Dict[Any, List[Any]]]]:
    """(message or None when nothing changed, new state). prev=None gives a full snapshot."""
    meta, cur = rows(layer, payload)
    msg: Dict[str, Any] = {"type": "ov", "l": layer, "t": round(float(payload.get("t") or 0), 2)}
    if prev is None:
        msg.update({"f": 1, "m": meta, "u": list(cur.values())})
        return msg, (meta, cur)
    pmeta, prows = prev
    m = {k: v for k, v in meta.items() if pmeta.get(k) != v}
    m.update({k: None for k in pmeta if k not in meta})
    u = [r for i, r in cur.items() if prows.get(i) != r]
    x = [i for i in prows if i not in cur]
    if not (m or u or x):
        return None, (meta, cur)
    if m:
        msg["m"] = m
    if u:
        msg["u"] = u
    if x:
        msg["x"] = x
    return msg, (meta, cur)
