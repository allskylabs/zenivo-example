"""
Focus assistant and lens endpoints.
"""

from typing import Optional
from fastapi import HTTPException, Query, Depends
from fastapi.responses import Response

from src.api.schemas import (
    FocusConfigRequest,
    LensRequest,
    LensAutofocusRequest,
)
from src.api.service import AllSkyService
from src.api.routes.common import (_NO_STORE, router, get_service)


# ----------------------------------------------------------------------
# Focus assistant
# ----------------------------------------------------------------------

@router.get("/api/focus")
def get_focus_state(service: AllSkyService = Depends(get_service)):
    """Focus mode state: active flag, ROI, method, best score, exposure."""
    return service.get_focus_state()


@router.post("/api/focus/start")
def start_focus(req: Optional[FocusConfigRequest] = None, service: AllSkyService = Depends(get_service)):
    """Enter focus mode (native resolution, fastest frame rate, auto-exposure and archiving paused)."""
    cfg = req.model_dump(exclude_none=True, exclude={"reset", "autofocus"}) if req else {}
    return service.start_focus(autofocus=req.autofocus if req else None, **cfg)


@router.post("/api/focus/stop")
def stop_focus(service: AllSkyService = Depends(get_service)):
    return service.stop_focus("stopped by user")


@router.post("/api/focus/finish")
def finish_focus(service: AllSkyService = Depends(get_service)):
    """Done focusing: save the result (and the lens position) to settings, notify, turn focus mode off."""
    if not service.focus.active:
        raise HTTPException(status_code=409, detail="Focus mode is not active")
    return service.finish_focus("manual")


@router.post("/api/focus/apply-record")
def apply_focus_record(index: int = Query(-1, ge=-20, le=19), exposure: bool = Query(True),
                       service: AllSkyService = Depends(get_service)):
    """Reuse a saved focus (lens position, zoom region, measure, exposure) from the focus history."""
    try:
        return service.apply_focus_record(index, exposure)
    except (RuntimeError, IndexError) as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/api/focus/config")
def configure_focus(req: FocusConfigRequest, service: AllSkyService = Depends(get_service)):
    cfg = req.model_dump(exclude_none=True, exclude={"reset", "autofocus"})
    return service.configure_focus(reset=req.reset, **cfg)


@router.post("/api/focus/auto-point")
def focus_auto_point(service: AllSkyService = Depends(get_service)):
    """Move the zoom onto the best focus target: a star or light by night, the most detailed area by day."""
    return service.focus_auto_point()


@router.post("/api/focus/auto-roi")
def focus_auto_roi(lights: bool = Query(False), service: AllSkyService = Depends(get_service)):
    """Move the focus ROI onto the best star (or bright light) in the current frame."""
    return service.focus_auto_roi(lights=lights)


@router.get("/api/focus/frame")
def get_focus_frame(
    since: float = Query(0.0, description="Wait for a frame captured after this timestamp"),
    stretch: bool = Query(False, description="Auto-stretch the crop for display"),
    service: AllSkyService = Depends(get_service),
):
    """Native-resolution ROI crop as JPEG; the focus measurement is in the X-Focus header (JSON)."""
    import json

    try:
        jpeg, result = service.get_focus_frame(since_ts=since, stretch=stretch)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    headers = dict(_NO_STORE)
    headers["X-Focus"] = json.dumps(result, separators=(",", ":"))
    return Response(content=jpeg, media_type="image/jpeg", headers=headers)


@router.post("/api/focus/lens")
def set_focus_lens(req: LensRequest, service: AllSkyService = Depends(get_service)):
    """Move a motorised lens (Pi Camera Module 3 ...) and keep it there; the position is saved."""
    try:
        return service.set_lens(position=req.position, delta=req.delta, tracking=req.tracking,
                                mode=req.mode, reset_learning=req.reset_learning,
                                refocus_every_h=req.refocus_every_h)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/api/focus/lens/autofocus")
def start_lens_autofocus(req: LensAutofocusRequest, service: AllSkyService = Depends(get_service)):
    """Autofocus the lens on the focus region; progress and result are in GET /api/focus (lens.sweep)."""
    try:
        return service.start_lens_autofocus(req.mode)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/api/focus/lens/cancel")
def cancel_lens_autofocus(service: AllSkyService = Depends(get_service)):
    return service.cancel_lens_autofocus()


@router.get("/api/focus/overview.jpg")
def get_focus_overview(
    width: int = Query(800, ge=320, le=1600),
    stretch: bool = Query(False, description="Brighten for display"),
    service: AllSkyService = Depends(get_service),
):
    return Response(content=service.get_focus_overview(width, stretch), media_type="image/jpeg", headers=_NO_STORE)


@router.post("/api/focus/auto-exposure")
def focus_auto_exposure(service: AllSkyService = Depends(get_service)):
    """Meter the focus region once (highlights to ~200/255) and hold that exposure."""
    try:
        return service.focus_auto_expose()
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
