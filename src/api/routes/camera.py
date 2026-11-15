"""
Camera status, telemetry, exposure/gain/mode, white balance, resolution, cloud config.
"""

from typing import Optional
from fastapi import HTTPException, Query, Request, status, Depends

from src.version import PRODUCT, __version__
from src.api.schemas import (
    ExposureControlRequest,
    GainControlRequest,
    CameraModeRequest,
    AeLockRequest,
    TargetBrightnessRequest,
    ImageTransformRequest,
    OutputResolutionRequest,
    CameraSelectRequest,
    PerformanceSettingsRequest,
    StarBoostRequest,
    WhiteBalanceRequest,
    CloudConfigRequest,
)
from src.api.service import AllSkyService
from src.api.routes.common import (router, get_service, public_view)


# ----------------------------------------------------------------------
# Camera Status & Telemetry Endpoints (AC3.1, AC3.2)
# ----------------------------------------------------------------------

@router.get("/api/version")
def get_version():
    """Product name and version (single source: src/version.py)."""
    return {"product": PRODUCT, "version": __version__}


@router.get("/api/status")
def get_camera_status(request: Request, service: AllSkyService = Depends(get_service)):
    """
    Retrieve camera hardware status, Sony IMX335 I2C registers (VMAX, HMAX, SHR0, PGC),
    accurate hardware timing, and system uptime.
    """
    return public_view(request, service, service.get_status())


@router.get("/api/telemetry")
def get_live_telemetry(request: Request, service: AllSkyService = Depends(get_service)):
    """
    Retrieve real-time astronomical telemetry: exposure, gain, star count,
    cloud cover percentage, and 256-bin RGB/Luma histogram.
    """
    return public_view(request, service, service.get_telemetry())


# ----------------------------------------------------------------------
# Hardware Control Endpoints (AC3.3)
# ----------------------------------------------------------------------

@router.post("/api/camera/exposure")
def set_camera_exposure(
    req: ExposureControlRequest,
    service: AllSkyService = Depends(get_service),
):
    """
    Set camera hardware exposure duration (0.001s to 90.0s) and optional gain.
    Rejects out-of-range values with HTTP 422.
    """
    result = service.set_exposure(req.exposure_s, req.gain_db)
    # Ensure settings object includes requested_exposure_s for test compliance
    if "settings" in result and isinstance(result["settings"], dict):
        result["settings"]["requested_exposure_s"] = req.exposure_s
    else:
        result["settings"] = {"requested_exposure_s": req.exposure_s}
    return result


@router.post("/api/camera/gain")
def set_camera_gain(
    req: GainControlRequest,
    service: AllSkyService = Depends(get_service),
):
    """
    Set camera sensor analog/digital gain (0.0 dB to 72.0 dB).
    Rejects out-of-range values with HTTP 422.
    """
    result = service.set_gain(req.gain_db)
    if "settings" in result and isinstance(result["settings"], dict):
        result["settings"]["requested_gain_db"] = req.gain_db
    else:
        result["settings"] = {"requested_gain_db": req.gain_db}
    return result


@router.post("/api/camera/mode")
def set_camera_mode(
    req: CameraModeRequest,
    service: AllSkyService = Depends(get_service),
):
    """
    Toggle between 'auto' (Day/Night ephemeris loop) and 'manual' modes.
    """
    mode_target = "auto" if req.auto is True or (req.mode and req.mode.lower() == "auto") else "manual"
    return service.set_mode(mode_target)


@router.get("/api/camera/ae-lock")
def get_camera_ae_lock(service: AllSkyService = Depends(get_service)):
    """Current exposure / gain / target-brightness locks and the latest frame brightness (cheap, polled)."""
    return {"mode": service.mode, "ae_lock": service.get_ae_lock()}


@router.post("/api/camera/ae-lock")
def set_camera_ae_lock(req: AeLockRequest, service: AllSkyService = Depends(get_service)):
    """Auto exposure with exposure or gain held fixed; the loop then adjusts only the other one."""
    try:
        return service.set_ae_lock(req.lock, req.value, req.exposure_s, req.gain_db)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/api/camera/target-brightness")
def set_camera_target_brightness(req: TargetBrightnessRequest, service: AllSkyService = Depends(get_service)):
    """Lock the brightness auto exposure aims for (frame mean 0-255); null returns to the day/night defaults."""
    return service.set_target_brightness(req.value)


@router.get("/api/camera/orientation")
def get_camera_orientation(service: AllSkyService = Depends(get_service)):
    """Return the current mirror / flip / rotate settings."""
    return service.get_station_config().get("image_transform", {})


@router.post("/api/camera/orientation")
def set_camera_orientation(
    req: ImageTransformRequest,
    service: AllSkyService = Depends(get_service),
):
    """Set mirror (horizontal), flip (vertical) and 0/180° rotation of all output images."""
    if req.rotate_deg is not None and req.rotate_deg % 360 not in (0, 180):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="rotate_deg must be 0 or 180")
    return service.set_image_transform(req.flip_horizontal, req.flip_vertical, req.rotate_deg, req.rotate_isp_180)


@router.get("/api/camera/white-balance")
def get_camera_white_balance(service: AllSkyService = Depends(get_service)):
    """Return RAW10 white balance mode, manual gains and gains currently applied."""
    return service.get_white_balance()


@router.post("/api/camera/white-balance")
def set_camera_white_balance(
    req: WhiteBalanceRequest,
    service: AllSkyService = Depends(get_service),
):
    """Set RAW10 white balance: auto (gray-world) or manual R/B gains."""
    return service.set_white_balance(req.mode, req.r_gain, req.b_gain)


@router.get("/api/camera/devices")
def get_camera_devices(service: AllSkyService = Depends(get_service)):
    """Cameras attached now, the active one and the saved choice (admin)."""
    return service.list_camera_options()


@router.post("/api/camera/select")
def select_camera(req: CameraSelectRequest, service: AllSkyService = Depends(get_service)):
    """Switch the active camera and remember the choice."""
    try:
        return service.select_camera(req.type, req.index)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("/api/camera/resolution")
def get_camera_resolution(service: AllSkyService = Depends(get_service)):
    """Return the current output resolution and selectable presets."""
    return service.get_resolution_options()


@router.post("/api/camera/resolution")
def set_camera_resolution(
    req: OutputResolutionRequest,
    service: AllSkyService = Depends(get_service),
):
    """Select the output resolution (downscaled from the native 2592x1944 frame)."""
    try:
        return service.set_output_resolution(req.width, req.height)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("/api/camera/star-boost")
def get_star_boost(service: AllSkyService = Depends(get_service)):
    """Return night star boost settings (software sum-binning of the viewer frame)."""
    return service.get_star_boost()


@router.post("/api/camera/star-boost")
def set_star_boost(
    req: StarBoostRequest,
    service: AllSkyService = Depends(get_service),
):
    """Turn night star boost on/off and choose the NxN sum-binning window."""
    try:
        return service.set_star_boost(req.enabled, req.bin)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("/api/camera/performance")
def get_camera_performance(service: AllSkyService = Depends(get_service)):
    """Return CPU/quality settings (capture interval, analysis and stream size)."""
    return service.get_performance()


@router.post("/api/camera/performance")
def set_camera_performance(
    req: PerformanceSettingsRequest,
    service: AllSkyService = Depends(get_service),
):
    """Update CPU/quality settings; omitted fields stay unchanged."""
    return service.set_performance(**req.model_dump(exclude_none=True))


@router.get("/api/sky/clouds/history")
def get_cloud_history(
    hours: float = Query(24.0, gt=0, le=9600, description="Window length in hours, ending at `end`"),
    end: Optional[float] = Query(None, description="Window end as Unix seconds (default: now)"),
    max_points: int = Query(600, ge=10, le=5000, description="Average into this many buckets at most"),
    service: AllSkyService = Depends(get_service),
):
    """Cloud cover time series (one sample per analysis interval) with a small summary."""
    return service.get_cloud_history(hours=hours, end_ts=end, max_points=max_points)


@router.get("/api/sky/clouds/config")
def get_cloud_config(service: AllSkyService = Depends(get_service)):
    """Sky region used for cloud analysis and the learned clear-sky reference."""
    return service.get_cloud_config()


@router.post("/api/sky/clouds/config")
def set_cloud_config(req: CloudConfigRequest, service: AllSkyService = Depends(get_service)):
    """Set the sky region and/or reset the learned clear-sky reference."""
    try:
        return service.set_cloud_config(sky_roi=req.sky_roi, reset_reference=req.reset_reference)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/api/camera/auto")
def toggle_camera_auto(
    service: AllSkyService = Depends(get_service),
):
    """Convenience alias for toggling auto mode."""
    new_mode = "manual" if service.mode == "auto" else "auto"
    return service.set_mode(new_mode)
