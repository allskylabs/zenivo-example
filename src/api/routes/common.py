"""
Shared router, helpers, caches and limits for the Zenivo Edge API route modules.
"""
from fastapi import APIRouter, HTTPException

from src.api.ttl_cache import TTLCache
from src.api.guest_view import guest_view, strip_keys
from src.api.service import AllSkyService
from src.auth.rounding import view_for

router = APIRouter()

# Shared short-lived caches for expensive public GETs (the raw result is cached; each caller's view is applied after)
_sat_cache = TTLCache(ttl_s=5.0, max_size=64)
_traffic_cache = TTLCache(ttl_s=15.0, max_size=64)
_dayobj_cache = TTLCache(ttl_s=15.0, max_size=64)
_frame_near_cache = TTLCache(ttl_s=10.0, max_size=128)
_sky_cache = TTLCache(ttl_s=60.0, max_size=128)
_acinfo_cache = TTLCache(ttl_s=5.0, max_size=256)
_dark_preview_cache = TTLCache(ttl_s=30.0, max_size=16)

GUEST_MAX_RANGE_S = 24 * 3600.0
GUEST_MAX_SAT_IDS = 50
GUEST_MAX_TRAIL_S = 600.0
_TS_MIN, _TS_MAX = 0.0, 4102444800.0  # 1970 .. 2100


def _check_ts(**values: float) -> None:
    """HTTP 400 for NaN/inf/out-of-range epoch timestamps (they used to raise a 500 deep in the log queries)."""
    import math
    for name, v in values.items():
        if not (math.isfinite(v) and _TS_MIN <= v <= _TS_MAX):
            raise HTTPException(status_code=400, detail=f"{name} is not a valid epoch timestamp")


def _check_range(request, t0: float, t1: float) -> None:
    from src.auth.middleware import is_admin
    _check_ts(t0=t0, t1=t1)
    if t1 < t0:
        raise HTTPException(status_code=400, detail="t1 must not be before t0")
    if request is not None and not is_admin(request) and t1 - t0 > GUEST_MAX_RANGE_S:
        raise HTTPException(status_code=400, detail="time range is limited to 24 hours")


def get_service() -> AllSkyService:
    """Dependency injector for AllSkyService instance."""
    return AllSkyService.get_instance()


def _view_for_request(request, service: AllSkyService):
    from src.auth.middleware import is_admin
    admin = is_admin(request)
    view = view_for(admin, service.public_precision)
    return view if admin else guest_view(view)


def public_view(request, service: AllSkyService, payload):
    """Payload as the caller may see it: exact for the admin, station coordinates rounded and ADS-B secrets removed
    for guests (src/auth/rounding.py). Server-side computations always keep the exact coordinates."""
    from src.auth.middleware import is_admin
    admin = is_admin(request)
    out = view_for(admin, service.public_precision)(payload)
    return out if admin else strip_keys(out)


# Live frames change every capture; without this Cloudflare adds a 4 h browser TTL and /live shows a stale frame
_NO_STORE = {"Cache-Control": "no-store, max-age=0"}
