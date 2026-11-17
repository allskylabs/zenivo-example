"""
FastAPI Router Definition for IMX335 Edge Web API.
Implements all REST, streaming, and WebSocket endpoints conforming to F15-F17 and AC3.

The endpoints live in the domain modules below; each registers on the one shared `router` (src/api/routes/common.py).
"""

from src.api.routes.common import (
    router, get_service, public_view, _view_for_request, _check_ts, _check_range,
    _sat_cache, _traffic_cache, _dayobj_cache, _frame_near_cache, _dark_preview_cache,
    GUEST_MAX_RANGE_S, GUEST_MAX_SAT_IDS, GUEST_MAX_TRAIL_S, _TS_MIN, _TS_MAX, _NO_STORE,
)
# Importing the modules registers their routes (the order is the route registration order)
from src.api.routes import camera, calibration, station, live, focus, gallery, flights, system, sky, community  # noqa: F401

__all__ = [
    "router", "get_service", "public_view", "_view_for_request", "_check_ts", "_check_range",
    "_sat_cache", "_traffic_cache", "_dayobj_cache", "_frame_near_cache", "_dark_preview_cache",
    "GUEST_MAX_RANGE_S", "GUEST_MAX_SAT_IDS", "GUEST_MAX_TRAIL_S", "_TS_MIN", "_TS_MAX", "_NO_STORE",
]
