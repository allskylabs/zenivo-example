"""
Gallery, timelapse, sky events and stored frame endpoints.
"""

from typing import Optional
from fastapi import HTTPException, Path, Query, Depends

from src.api.schemas import (
    EventConfigRequest, EventLabelRequest,
)
from src.api.service import AllSkyService
from src.storage import frame_codec
from src.api.routes.common import (router, get_service, _check_ts, _frame_near_cache)


# ----------------------------------------------------------------------
# Gallery & Events Endpoints (AC3.7)
# ----------------------------------------------------------------------

@router.get("/api/gallery")
def get_gallery_items(service: AllSkyService = Depends(get_service)):
    """
    List captured astronomical products: diurnal keograms, nocturnal startrails, timelapses.
    """
    return service.get_gallery()


@router.post("/api/gallery/generate")
@router.post("/api/timelapse/generate")
def generate_gallery_products(
    fps: float = Query(default=12.0, ge=1.0, le=60.0),
    service: AllSkyService = Depends(get_service),
):
    """
    Compile/refresh today's H.264 Timelapse (.mp4), Meridian Keogram (.jpg), and Startrails (.jpg)
    from archived frames on disk.
    """
    try:
        res = service.generate_observational_products(fps=fps, ensure_min_frames=True)
        res["gallery"] = service.get_gallery()
        return res
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to generate timelapse/products: {exc}")



@router.post("/api/timelapse/build")
def build_range_timelapse(
    date_from: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_to: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    fps: float = Query(default=24.0, ge=1.0, le=60.0),
    width: int = Query(default=0, ge=0, le=1920),
    overlay: bool = Query(default=False),
    service: AllSkyService = Depends(get_service),
):
    """Build a custom timelapse (custom.mp4) from archived frames of an observing-day range."""
    try:
        return service.build_range_timelapse(date_from, date_to, fps=fps, width=width, overlay=overlay)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to build timelapse: {exc}")


@router.get("/api/events")
@router.get("/api/events/meteors")
def get_meteor_events(
    limit: int = Query(default=20, ge=1, le=3000),
    type: Optional[str] = Query(default=None, pattern="^(meteor|plane|satellite|clouds|flash)$"),
    since: Optional[float] = Query(default=None, description="epoch seconds, events starting at or after"),
    until: Optional[float] = Query(default=None, description="epoch seconds, events starting at or before"),
    brief: bool = Query(default=False, description="only what a timeline needs"),
    service: AllSkyService = Depends(get_service),
):
    """
    List detected sky events (meteors, aircraft, satellites, cloud changes, flashes), newest first.
    """
    if limit > 100 and not brief:
        # Full event records are capped at 100; only the compact timeline form may ask for more
        raise HTTPException(status_code=422, detail="limit must be at most 100 unless brief=true")
    return service.get_events(limit=limit, event_type=type, since=since, until=until, brief=brief)


@router.get("/api/frames/near")
def get_frame_near(ts: float = Query(..., description="epoch seconds"), service: AllSkyService = Depends(get_service)):
    """The archived frame closest to a moment, for stepping back through the night."""
    _check_ts(ts=ts)
    res = _frame_near_cache.get_or_compute(int(ts), lambda: service.frame_near(ts))
    if res is None:
        raise HTTPException(status_code=404, detail="No archived frame near that time")
    return res


@router.get("/api/frames/span")
def get_frames_span(start: float = Query(...), end: float = Query(...), max: int = Query(default=600, ge=1, le=1500),
                    service: AllSkyService = Depends(get_service)):
    """Archived frames between two moments (epoch seconds), for playback."""
    _check_ts(start=start, end=end)
    if end < start or end - start > 12 * 3600:
        raise HTTPException(status_code=400, detail="end must follow start by at most 12 hours")
    return {"frames": service.frames_span(start, end, max_n=max)}


@router.get("/api/frames/img/{day}/{name}")
def get_frame_image(day: str, name: str, service: AllSkyService = Depends(get_service)):
    """An archived frame (AVIF, or JPEG for older days)."""
    from fastapi.responses import FileResponse

    path = service.frame_image_path(day, name)
    if not path:
        raise HTTPException(status_code=404, detail="Frame not found")
    return FileResponse(path, media_type=frame_codec.media_type(path), headers={"Cache-Control": "public, max-age=86400"})


@router.get("/api/events/config")
def get_event_config(service: AllSkyService = Depends(get_service)):
    """Which sky event types are detected."""
    return service.get_event_config()


@router.post("/api/events/config")
def set_event_config(req: EventConfigRequest, service: AllSkyService = Depends(get_service)):
    """Turn detection of sky event types on or off (persisted)."""
    return service.set_event_config(**req.model_dump(exclude_none=True))


@router.get("/api/events/img/{day}/{name}")
def get_event_image(day: str, name: str, service: AllSkyService = Depends(get_service)):
    """Crop or whole-sky JPEG saved with an event."""
    from fastapi.responses import FileResponse

    path = service.event_image_path(day, name)
    if not path:
        raise HTTPException(status_code=404, detail="Event image not found")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


_EVENT_ID = r"^[a-z]+_\d{8}_\d{6}_\d{3}$"


@router.get("/api/events/labels")
def get_event_labels(service: AllSkyService = Depends(get_service)):
    """Every owner verdict so far: {event_id: {label, actual_type, ts}}; the labelled set for tuning the detectors."""
    return {"labels": service.event_labels()}


@router.get("/api/events/{event_id}")
def get_event(event_id: str = Path(pattern=_EVENT_ID), service: AllSkyService = Depends(get_service)):
    """One saved sky event (deep link: /gallery?event=<id>)."""
    ev = service.get_event(event_id)
    if ev is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return ev


@router.post("/api/events/{event_id}/label")
def label_event(req: EventLabelRequest, event_id: str = Path(pattern=_EVENT_ID), service: AllSkyService = Depends(get_service)):
    """Owner true / false-alarm verdict for one event (appended to events/labels.jsonl; the newest wins). Admin only."""
    rec = service.label_event(event_id, req.label, req.actual_type)
    if rec is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return rec


@router.delete("/api/events/{event_id}")
def delete_event(event_id: str = Path(pattern=_EVENT_ID), service: AllSkyService = Depends(get_service)):
    """Delete one event and its images. Admin only like every write (src/auth/policy.py); logged in the activity log."""
    if not service.delete_event(event_id):
        raise HTTPException(status_code=404, detail="Event not found")
    return {"deleted": event_id}
