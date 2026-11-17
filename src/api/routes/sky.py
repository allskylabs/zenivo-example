"""
Rise/set times, altitude and Moon phase of sky objects for the tap card and the Sky page lists.
"""
import time
from typing import Optional

from fastapi import HTTPException, Query, Request, Depends

from src.analytics import riseset
from src.api.service import AllSkyService
from src.api.routes.common import router, get_service, _sky_cache
from src.auth.rounding import snap


def _station(request: Request, service: AllSkyService):
    """Station latitude/longitude the times are computed for. Guests get the publicly rounded position: sunrise to the
    minute would otherwise tell the exact longitude."""
    from src.auth.middleware import is_admin
    lat, lon = float(service.auto_exposure.lat), float(service.auto_exposure.lon)
    if not is_admin(request):
        p = service.public_precision()
        lat, lon = snap(lat, p), snap(lon, p)
    return lat, lon


@router.get("/api/sky/riseset")
def get_sky_riseset(request: Request,
                    body: Optional[str] = Query(default=None, pattern=r"^[A-Za-z]{3,10}$", description="sun, moon, venus, mars, jupiter or saturn"),
                    ra: Optional[float] = Query(default=None, ge=0.0, le=360.0, description="J2000 right ascension, degrees"),
                    dec: Optional[float] = Query(default=None, ge=-90.0, le=90.0, description="J2000 declination, degrees"),
                    service: AllSkyService = Depends(get_service)):
    """Altitude/azimuth now and next rise / set (epoch seconds; state up, down, always_up or never_rises) of one body or of
    a fixed point (ra and dec)."""
    lat, lon = _station(request, service)
    now = time.time()

    def compute():
        if body:
            r = riseset.body_info(body, lat, lon, now)
            if r is None:
                raise HTTPException(status_code=404, detail="unknown body")
            return r
        if ra is None or dec is None:
            raise HTTPException(status_code=400, detail="give body, or ra and dec")
        return riseset.fixed_info(ra, dec, lat, lon, now)
    key = ("one", (body or "").lower(), None if ra is None else round(ra, 2), None if dec is None else round(dec, 2), lat, lon, int(now // 60))
    return _sky_cache.get_or_compute(key, compute)


@router.get("/api/sky/bodies")
def get_sky_bodies(request: Request, service: AllSkyService = Depends(get_service)):
    """Sun, Moon (with phase) and the naked-eye planets: altitude/azimuth now, next rise and set."""
    lat, lon = _station(request, service)
    now = time.time()
    return _sky_cache.get_or_compute(("all", lat, lon, int(now // 60)), lambda: {"t": round(now), "bodies": riseset.all_bodies(lat, lon, now)})
