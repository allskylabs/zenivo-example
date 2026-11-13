"""Guest view of a payload: the station's own coordinates snapped to the public precision grid and the ADS-B
credentials/receiver address removed. The server keeps computing with exact coordinates; only what leaves is changed."""
from __future__ import annotations

from typing import Any, Callable

PRECISIONS = (0.0, 0.01, 0.1, 1.0)  # 0 = exact
DEFAULT_PRECISION = 0.1
_ADSB_PRIVATE = ("host", "fr24_email", "fr24_password", "fr24_token")


def snap(value: float, precision_deg: float) -> float:
    """Round a coordinate to the grid (0.1 deg is about 10 km); precision 0 keeps it exact."""
    if not precision_deg or precision_deg <= 0:
        return round(float(value), 5)
    return round(round(float(value) / precision_deg) * precision_deg, 4)


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _pair(d: dict, lat_key: str, lon_key: str, precision: float) -> None:
    if _num(d.get(lat_key)) and _num(d.get(lon_key)):
        d[lat_key], d[lon_key] = snap(d[lat_key], precision), snap(d[lon_key], precision)
        if precision > 0:
            d["location_rounded"] = True


def guest_view(obj: Any, precision: float) -> Any:
    """Deep copy of a JSON-like payload for non-admins. Rounds {latitude_deg, longitude_deg} pairs (the station) and the
    {lat, lon} pair of a `station` object; strips private ADS-B receiver/account fields from `adsb` objects."""
    if isinstance(obj, list):
        return [guest_view(v, precision) for v in obj]
    if not isinstance(obj, dict):
        return obj
    out = {}
    for k, v in obj.items():
        if k == "adsb" and isinstance(v, dict):
            v = {ak: av for ak, av in v.items() if ak not in _ADSB_PRIVATE}
        out[k] = guest_view(v, precision)
    _pair(out, "latitude_deg", "longitude_deg", precision)
    st = out.get("station")
    if isinstance(st, dict):
        _pair(st, "lat", "lon", precision)
    return out


def view_for(is_admin: bool, precision_fn: Callable[[], float]) -> Callable[[Any], Any]:
    """Payload filter for a connection: identity for the admin, guest_view for everybody else."""
    if is_admin:
        return lambda payload: payload
    return lambda payload: guest_view(payload, precision_fn())
