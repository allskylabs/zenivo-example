"""
Storage, activity log and OBS overlay endpoints.
"""

import os
from typing import Optional
from fastapi import HTTPException, Query, Request, Depends

from src.api.guest_view import strip_keys
from src.api.schemas import (
    StoragePolicyRequest,
    ObsOverlayRequest,
)
from src.api.service import AllSkyService
from src.api.routes.common import (router, get_service)


# ----------------------------------------------------------------------
# Storage / retention
# ----------------------------------------------------------------------
@router.get("/api/storage")
def get_storage_status(request: Request, service: AllSkyService = Depends(get_service)):
    """Disk usage of the data directory, archived frame days and the active retention policy
    (absolute server paths are removed for non-admins)."""
    from src.auth.middleware import is_admin
    res = service.get_storage_status()
    return res if is_admin(request) else strip_keys(res, ("data_dir", "extra_dirs"))


@router.post("/api/storage/cleanup")
def run_storage_cleanup(service: AllSkyService = Depends(get_service)):
    """Apply the retention policy now."""
    return service.run_storage_cleanup()


@router.put("/api/storage/policy")
def update_storage_policy(payload: StoragePolicyRequest, service: AllSkyService = Depends(get_service)):
    """Change retention limits (days kept, size cap, free-space floor, archive / timelapse intervals)."""
    return service.configure_storage(**payload.model_dump(exclude_none=True))


# ----------------------------------------------------------------------
# Activity log
# ----------------------------------------------------------------------
@router.get("/api/logs")
def get_activity_logs(
    since_id: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
    level: Optional[str] = Query(None, pattern="^(info|success|warning|error)$"),
    category: Optional[str] = Query(None, max_length=32),
):
    """Activity log entries (newest first) plus 24h counts per level."""
    from src.activity import get_activity_log

    log = get_activity_log()
    return {"entries": log.query(since_id=since_id, limit=limit, level=level, category=category), **log.summary()}


@router.get("/api/logs/download")
def download_activity_log():
    """Download the current activity log file (JSON lines)."""
    from fastapi.responses import FileResponse
    from src.activity import get_activity_log

    path = get_activity_log().log_path
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="No activity log file yet")
    return FileResponse(path, media_type="application/x-ndjson", filename="allsky-activity.jsonl")


# ----------------------------------------------------------------------
# OBS Studio & Live Overlay Synchronization Endpoints
# ----------------------------------------------------------------------


@router.get("/api/obs/overlay")
def get_obs_overlay(service: AllSkyService = Depends(get_service)):
    """Retrieve current OBS overlay settings (stars, labels, grid, compass, framing, theme)."""
    return service.get_obs_overlay()


@router.post("/api/obs/overlay")
async def set_obs_overlay(
    req: ObsOverlayRequest,
    service: AllSkyService = Depends(get_service),
):
    """Update OBS overlay display settings and broadcast instantly to all connected viewers."""
    patch = {k: v for k, v in req.model_dump().items() if v is not None}
    updated = service.set_obs_overlay(patch)
    try:
        from src.api.stream import ws_manager
        await ws_manager.broadcast_json({"type": "obs_overlay", "settings": updated})
    except Exception:
        pass
    return {"success": True, "settings": updated}


@router.post("/api/obs/reload")
async def reload_obs_overlay():
    """Broadcast a reload command to connected OBS Browser Source viewers."""
    try:
        from src.api.stream import ws_manager
        await ws_manager.broadcast_json({"type": "obs_reload"})
    except Exception:
        pass
    return {"success": True}
