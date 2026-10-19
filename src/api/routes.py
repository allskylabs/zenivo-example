"""
FastAPI Router Definition for IMX335 Edge Web API.
Implements all REST, streaming, and WebSocket endpoints conforming to F15-F17 and AC3.
"""

import os
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Query, WebSocket, status, Depends
from fastapi.responses import Response, StreamingResponse

from src.api.schemas import (
    ExposureControlRequest,
    GainControlRequest,
    CameraModeRequest,
    AeLockRequest,
    TargetBrightnessRequest,
    MasterDarkRequest,
    StationLocationRequest,
    ImageTransformRequest,
    OutputResolutionRequest,
    PerformanceSettingsRequest,
    AdsbConfigRequest,
    EventConfigRequest,
    StarBoostRequest,
    WhiteBalanceRequest,
    StoragePolicyRequest,
    CloudConfigRequest,
    SkyMaskRequest,
    FocusConfigRequest,
    LensRequest,
    LensAutofocusRequest,
)
from src.api.service import AllSkyService
from src.api.stream import mjpeg_frame_generator, handle_live_websocket, sse_event_generator

router = APIRouter()


def get_service() -> AllSkyService:
    """Dependency injector for AllSkyService instance."""
    return AllSkyService.get_instance()


# ----------------------------------------------------------------------
# Camera Status & Telemetry Endpoints (AC3.1, AC3.2)
# ----------------------------------------------------------------------

@router.get("/api/status")
def get_camera_status(service: AllSkyService = Depends(get_service)):
    """
    Retrieve camera hardware status, Sony IMX335 I2C registers (VMAX, HMAX, SHR0, PGC),
    accurate hardware timing, and system uptime.
    """
    return service.get_status()


@router.get("/api/telemetry")
def get_live_telemetry(service: AllSkyService = Depends(get_service)):
    """
    Retrieve real-time astronomical telemetry: exposure, gain, star count,
    cloud cover percentage, and 256-bin RGB/Luma histogram.
    """
    return service.get_telemetry()


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


# ----------------------------------------------------------------------
# Calibration Library Endpoints (AC3.4)
# ----------------------------------------------------------------------

@router.get("/api/calibration/status")
def get_calibration_status(
    service: AllSkyService = Depends(get_service),
):
    """
    Retrieve real-time Master Dark calibration progress, status message,
    latest calibration result, and saved calibration library files.
    """
    return service.get_calibration_status()


@router.post("/api/calibration/auto-library")
def trigger_auto_dark_library(
    only_missing: bool = Query(default=False),
    service: AllSkyService = Depends(get_service),
):
    """
    Automatically capture and median-stack all 6 standard Master Dark profiles
    (Twilight to High-Sensitivity Night) without requiring manual parameter selection.
    """
    return service.trigger_auto_dark_library(only_missing=only_missing)


@router.post("/api/calibration/master-dark")
@router.post("/api/calibration/dark")
def trigger_master_dark(
    req: MasterDarkRequest,
    service: AllSkyService = Depends(get_service),
):
    """
    Capture N frames with matching exposure and gain, median stack into Master Dark,
    detect hot pixels (DN >= 100), and store to calibration library.
    """
    return service.trigger_master_dark(req.num_frames, req.exposure_s, req.gain_db)


# ----------------------------------------------------------------------
# Station Setup & Astrometric Plate-Solving Endpoints (hoan.uk/astronomy/plate-solving)
# ----------------------------------------------------------------------

@router.get("/api/station/config")
def get_station_config(
    service: AllSkyService = Depends(get_service),
):
    """
    Retrieve complete station coordinates, optical parameters, FOV, focal length,
    optical center, zenith tilt, azimuth, altitude, and WCS parameters.
    """
    return service.get_station_config()


@router.post("/api/station/location")
@router.post("/api/station/settings")
def update_station_location(
    req: StationLocationRequest,
    service: AllSkyService = Depends(get_service),
):
    """
    Save user coordinates (manual input or browser GPS) and/or manual camera/lens/station
    parameter overrides from the Web UI. Only runs plate-solving if auto_plate_solve=True.
    """
    lat = req.latitude_deg if req.latitude_deg is not None else req.latitude
    lon = req.longitude_deg if req.longitude_deg is not None else req.longitude
    name = req.location_name if req.location_name is not None else req.station_name
    overrides = {
        k: getattr(req, k)
        for k in (
            "lens_model",
            "focal_length_mm",
            "pixel_scale_arcsec_px",
            "fov_horizontal_deg",
            "fov_vertical_deg",
            "fov_diagonal_deg",
            "optical_center_x_px",
            "optical_center_y_px",
            "horizon_radius_90_px",
            "azimuth_deg",
            "altitude_deg",
            "zenith_tilt_deg",
            "north_position_angle_deg",
        )
        if getattr(req, k) is not None
    }
    return service.update_station_location(
        latitude_deg=lat,
        longitude_deg=lon,
        elevation_m=req.elevation_m,
        location_name=name,
        geo_source=req.geo_source or "manual",
        fov_hint=req.fov_hint or "auto",
        auto_plate_solve=req.auto_plate_solve,
        manual_overrides=overrides if overrides else None,
    )


@router.post("/api/station/auto-locate")
def auto_locate_station(
    auto_plate_solve: bool = Query(default=False),
    service: AllSkyService = Depends(get_service),
):
    """
    Automatically detect station coordinates via IP Geolocation fallback when user clicks on Web UI.
    Does not run plate-solving unless auto_plate_solve=True.
    """
    return service.auto_locate_station(auto_plate_solve=auto_plate_solve)


@router.post("/api/station/plate-solve")
def run_station_plate_solve(
    fov_hint: str = Query(default="auto"),
    service: AllSkyService = Depends(get_service),
):
    """
    Capture a live frame and solve all camera, lens, FOV, focal length, optical center,
    orientation, tilt, and azimuth parameters via https://hoan.uk/astronomy/plate-solving.
    """
    return service.run_station_plate_solve(fov_hint=fov_hint)


# ----------------------------------------------------------------------
# Live Snapshot & Histogram Endpoints (AC3.5, AC3.6)
# ----------------------------------------------------------------------

# Live frames change every capture; without this Cloudflare adds a 4 h browser TTL and /live shows a stale frame
_NO_STORE = {"Cache-Control": "no-store, max-age=0"}


@router.get("/api/live/snapshot.webp")
@router.get("/api/snapshot.webp")
def get_snapshot_webp(service: AllSkyService = Depends(get_service)):
    """
    Return latest calibrated 2592x1944 WebP frame (captured as RAW10, subtracted in RAW10, then saved as WebP).
    """
    try:
        webp_bytes = service.get_snapshot_webp()
        return Response(content=webp_bytes, media_type="image/webp", headers=_NO_STORE)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"WebP snapshot generation failed: {e}")


@router.get("/api/live/snapshot.jpg")
@router.get("/api/snapshot.jpg")
def get_snapshot_jpeg(service: AllSkyService = Depends(get_service)):
    """
    Return latest calibrated 2592x1944 JPEG frame with valid magic bytes (0xFFD8...0xFFD9).
    """
    try:
        jpeg_bytes = service.get_snapshot_jpeg()
        return Response(content=jpeg_bytes, media_type="image/jpeg", headers=_NO_STORE)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Snapshot generation failed: {e}")


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


@router.get("/api/stream/histogram")
def get_live_histogram(service: AllSkyService = Depends(get_service)):
    """
    Compute and return vectorized 256-bin RGB and Luma histogram in < 10ms with statistics.
    """
    frame, _ = service.capture_frame()
    from src.api.histogram import compute_fast_histogram
    return compute_fast_histogram(frame)


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



@router.get("/api/events")
@router.get("/api/events/meteors")
def get_meteor_events(
    limit: int = Query(default=20, ge=1, le=100),
    type: Optional[str] = Query(default=None, pattern="^(meteor|plane|satellite|clouds|flash)$"),
    service: AllSkyService = Depends(get_service),
):
    """
    List detected sky events (meteors, aircraft, satellites, cloud changes, flashes), newest first.
    """
    return service.get_events(limit=limit, event_type=type)


@router.get("/api/events/config")
def get_event_config(service: AllSkyService = Depends(get_service)):
    """Which sky event types are detected."""
    return service.get_event_config()


@router.post("/api/events/config")
def set_event_config(req: EventConfigRequest, service: AllSkyService = Depends(get_service)):
    """Turn detection of sky event types on or off (persisted)."""
    return service.set_event_config(**req.model_dump(exclude_none=True))


@router.get("/api/adsb")
def get_adsb_config(service: AllSkyService = Depends(get_service)):
    """ADS-B flight matching: receiver address and live feed status."""
    return service.get_adsb_config()


@router.get("/api/adsb/aircraft")
def get_adsb_aircraft(limit: int = Query(default=30, ge=1, le=400),
                      trail: float = Query(default=0.0, ge=0.0, le=900.0),
                      service: AllSkyService = Depends(get_service)):
    """Aircraft heard right now: flight, altitude, speed, heading, distance, direction, in camera view.
    trail=<seconds> adds lat/lon and each aircraft's recent path (3D map)."""
    return service.get_aircraft_now(limit=limit, trail_s=trail)


@router.get("/api/map/config")
def get_map_config(service: AllSkyService = Depends(get_service)):
    """Basemap settings for the 3D flights map (Google Maps key, if configured)."""
    return service.get_map_config()


@router.get("/api/map/airports")
def get_map_airports(service: AllSkyService = Depends(get_service)):
    """Airports and runways around the station (OurAirports, public domain) for the 3D flights map."""
    return service.get_airports()


@router.post("/api/adsb")
def set_adsb_config(req: AdsbConfigRequest, service: AllSkyService = Depends(get_service)):
    """Set the ADS-B receiver (SBS-1 feed host/port) or turn flight matching on/off (persisted)."""
    try:
        return service.set_adsb_config(**req.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("/api/events/img/{day}/{name}")
def get_event_image(day: str, name: str, service: AllSkyService = Depends(get_service)):
    """Crop or whole-sky JPEG saved with an event."""
    from fastapi.responses import FileResponse

    path = service.event_image_path(day, name)
    if not path:
        raise HTTPException(status_code=404, detail="Event image not found")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


# ----------------------------------------------------------------------
# Live Video Stream (MJPEG & WebSocket)
# ----------------------------------------------------------------------

@router.get("/api/stream/mjpeg")
@router.get("/api/live/mjpeg")
def get_mjpeg_stream(
    fps: float = Query(default=5.0, ge=0.5, le=30.0),
    downscale: int = Query(default=960, ge=320, le=2592),
    limit: Optional[int] = Query(default=None, ge=1),
    service: AllSkyService = Depends(get_service),
):
    """
    Multipart MJPEG video stream for live browser video preview.
    """
    return StreamingResponse(
        mjpeg_frame_generator(service, fps=fps, downscale_width=downscale, max_frames=limit),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@router.get("/api/stream/sse")
@router.get("/api/sse")
def get_sse_stream(
    interval: float = Query(default=1.0, ge=0.25, le=10.0),
    service: AllSkyService = Depends(get_service),
):
    """
    Server-Sent Events (SSE) push stream for real-time telemetry and status updates.
    """
    return StreamingResponse(
        sse_event_generator(service, interval=interval),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.websocket("/ws/live")
async def websocket_live_stream(
    websocket: WebSocket,
    service: AllSkyService = Depends(get_service),
):
    """
    Bidirectional WebSocket feed for real-time binary JPEG frames, telemetry streaming, and commands.
    """
    await handle_live_websocket(websocket, service)


# ----------------------------------------------------------------------
# Storage / retention
# ----------------------------------------------------------------------
@router.get("/api/storage")
def get_storage_status(service: AllSkyService = Depends(get_service)):
    """Disk usage of the data directory, archived frame days and the active retention policy."""
    return service.get_storage_status()


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


@router.get("/api/station/sky-mask")
def get_station_sky_mask(service: AllSkyService = Depends(get_service)):
    """User sky/obstruction polygons the plate solver intersects with its automatic sky mask."""
    return service.get_sky_mask()


@router.post("/api/station/sky-mask")
def set_station_sky_mask(req: SkyMaskRequest, service: AllSkyService = Depends(get_service)):
    """Save sky/obstruction polygons (frame fractions); send both lists empty to clear the mask."""
    try:
        return service.set_sky_mask(sky=req.sky, obstruction=req.obstruction)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/api/station/plate-solve/start")
def start_station_plate_solve(
    fov_hint: str = Query(default="auto", max_length=16),
    service: AllSkyService = Depends(get_service),
):
    """Start plate-solving in the background; poll /api/station/plate-solve/status."""
    return service.start_plate_solve_job(fov_hint=fov_hint)


@router.get("/api/station/plate-solve/status")
def get_station_plate_solve_status(service: AllSkyService = Depends(get_service)):
    """State of the background plate-solve job (idle / running / success / failed)."""
    return service.get_plate_solve_status()
