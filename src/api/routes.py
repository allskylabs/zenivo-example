"""
FastAPI Router Definition for IMX335 Edge Web API.
Implements all REST, streaming, and WebSocket endpoints conforming to F15-F17 and AC3.
"""

import json
import os
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, status, Depends
from fastapi.responses import Response, StreamingResponse

from src.version import PRODUCT, __version__
from src.api.ttl_cache import TTLCache
from src.api.guest_view import guest_view, strip_keys
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
    CameraSelectRequest,
    PerformanceSettingsRequest,
    AdsbConfigRequest,
    EventConfigRequest,
    DayObjectsConfigRequest,
    StarBoostRequest,
    WhiteBalanceRequest,
    StoragePolicyRequest,
    CloudConfigRequest,
    SkyMaskRequest,
    ManualStarSolveRequest,
    FocusConfigRequest,
    LensRequest,
    LensAutofocusRequest,
    ObsOverlayRequest,
    CommunitySettingsRequest,
)
from src.api.service import AllSkyService
from src.storage import frame_codec
from src.api.stream import (
    mjpeg_frame_generator, handle_live_websocket, sse_event_generator, guarded_stream, stream_limiter,
    stream_client_key, GUEST_MJPEG_MAX_FPS, GUEST_MJPEG_MAX_WIDTH, GUEST_STREAM_MAX_S,
)
from src.auth.rounding import view_for

router = APIRouter()

# Shared short-lived caches for expensive public GETs (the raw result is cached; each caller's view is applied after)
_sat_cache = TTLCache(ttl_s=5.0, max_size=64)
_traffic_cache = TTLCache(ttl_s=15.0, max_size=64)
_dayobj_cache = TTLCache(ttl_s=15.0, max_size=64)
_frame_near_cache = TTLCache(ttl_s=10.0, max_size=128)
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


@router.get("/api/calibration/dark-preview")
def get_dark_preview(
    file: str = Query(...),
    stats: bool = Query(False),
    service: AllSkyService = Depends(get_service),
):
    """Contrast-stretched PNG preview (or stats JSON with stats=1) of a saved RAW10 master dark."""
    res = _dark_preview_cache.get_or_compute(file, lambda: service.get_dark_preview(file))
    if res is None:
        raise HTTPException(status_code=404, detail="dark not found")
    png, st = res
    if stats:
        return st
    return Response(content=png, media_type="image/png")


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
# Station Setup & Astrometric Plate-Solving Endpoints
# ----------------------------------------------------------------------

@router.get("/api/station/config")
def get_station_config(
    request: Request,
    service: AllSkyService = Depends(get_service),
):
    """
    Retrieve complete station coordinates, optical parameters, FOV, focal length,
    optical center, zenith tilt, azimuth, altitude, and WCS parameters.
    """
    return public_view(request, service, service.get_station_config())


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
            "remote_solver_url",
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
    orientation, tilt, and azimuth parameters (local solver first; optional remote solver if configured).
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


@router.get("/api/planets")
def get_planets():
    """Naked-eye planets (J2000 RA/Dec, visual magnitude) for the live sky overlay."""
    from datetime import datetime, timezone
    from src.calibration.bright_star_solver import planet_positions
    return planet_positions(datetime.now(timezone.utc))


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


@router.get("/api/adsb")
def get_adsb_config(request: Request, service: AllSkyService = Depends(get_service)):
    """ADS-B flight matching: receiver address and live feed status (receiver address and FR24 account e-mail: admin only)."""
    from src.auth.middleware import is_admin
    cfg = service.get_adsb_config()
    if not is_admin(request):
        cfg = dict(cfg, host="", network_host="", fr24_email="", fr24=dict(cfg.get("fr24") or {}, email=""))
    return cfg


@router.get("/api/adsb/aircraft")
def get_adsb_aircraft(limit: int = Query(default=30, ge=1, le=6000),
                      trail: float = Query(default=0.0, ge=0.0, le=900.0),
                      bounds: Optional[str] = Query(default=None, max_length=120),
                      fr24: Optional[bool] = Query(default=None),
                      compact: bool = Query(default=False),
                      request: Request = None,
                      service: AllSkyService = Depends(get_service)):
    """Aircraft heard right now (local ADS-B + Flightradar24 global feed): flight, altitude, speed, heading,
    distance, direction, in camera view. trail=<seconds> adds lat/lon and each aircraft's recent path (3D map);
    bounds=north,south,west,east or 'global' queries Flightradar24 for that region or the whole globe.
    For guests a box is snapped to a 1-degree grid and must lie within 500 km of the station (else 400)."""
    from src.auth.middleware import is_admin
    if bounds and not is_admin(request):
        from src.analytics.fr24 import normalize_guest_bounds
        try:
            bounds = normalize_guest_bounds(bounds, float(service.station_config.get("latitude_deg", 0.0)),
                                            float(service.station_config.get("longitude_deg", 0.0)))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    return public_view(request, service, service.get_aircraft_now(
        limit=limit, trail_s=trail, bounds=bounds, include_fr24=fr24, compact=compact))


@router.get("/api/traffic")
def get_traffic(t0: float = Query(description="start, epoch seconds"),
                t1: float = Query(description="end, epoch seconds"),
                limit: int = Query(default=2000, ge=1, le=20000),
                request: Request = None,
                service: AllSkyService = Depends(get_service)):
    """Aircraft and satellites that were inside the camera view between t0 and t1 (replay / name an event's flight).
    Rows: a = [icao, callsign, reg, type, lat, lon, alt_m, speed_kmh, heading, az, el, x, y], s = [norad, name, az, el, x, y, range_km, sunlit]."""
    _check_range(request, t0, t1)
    samples = _traffic_cache.get_or_compute((int(t0 // 10), int(t1 // 10), limit),
                                            lambda: service.traffic_log().query(t0, t1, limit))
    return public_view(request, service, {"samples": samples})


@router.get("/api/traffic/status")
def get_traffic_status(service: AllSkyService = Depends(get_service)):
    """Traffic log settings (retention days, size cap, interval) and how much is stored."""
    return service.traffic_log().status()


@router.get("/api/day-objects")
def get_day_objects(t0: float = Query(description="start, epoch seconds"),
                    t1: float = Query(description="end, epoch seconds"),
                    limit: int = Query(default=2000, ge=1, le=20000),
                    request: Request = None,
                    service: AllSkyService = Depends(get_service)):
    """Birds, insects and aircraft followed by day: class, size, speed (frame widths/s), straightness and the
    path as [seconds since start, x, y] frame fractions."""
    _check_range(request, t0, t1)
    tracks = _dayobj_cache.get_or_compute((int(t0 // 10), int(t1 // 10), limit),
                                          lambda: service.day_object_log().query(t0, t1, limit))
    return {"tracks": tracks}


@router.get("/api/day-objects/status")
def get_day_objects_status(service: AllSkyService = Depends(get_service)):
    """Stored day-object tracks and the live tracker state (rain detected from drops on the dome, etc.)."""
    tracker = getattr(service, "day_objects", None)
    return {"log": service.day_object_log().status(), "tracker": tracker.status() if tracker is not None else None,
            **service.get_day_objects_config()}


@router.get("/api/day-objects/config")
def get_day_objects_config(service: AllSkyService = Depends(get_service)):
    """Daytime object detection settings (classes recorded, sensitivity, blob size, contrast, retention) and the presets."""
    return service.get_day_objects_config()


@router.post("/api/day-objects/config")
def set_day_objects_config(req: DayObjectsConfigRequest, service: AllSkyService = Depends(get_service)):
    """Change the daytime object detection settings (persisted, applied live). Admin only like every write."""
    fields = {k: v for k, v in req.model_dump(exclude_unset=True).items() if v is not None or k == "min_contrast_dn"}
    return service.set_day_objects_config(**fields)


@router.get("/api/radiosondes")
def get_radiosondes(limit: int = Query(default=40, ge=1, le=200),
                    request: Request = None,
                    service: AllSkyService = Depends(get_service)):
    """Weather balloons (SondeHub radiosondes) near the station: altitude, climb, distance, direction, in camera view."""
    return public_view(request, service, service.get_radiosondes_now(limit=limit))


@router.get("/api/adsb/flight/{fr24_id}")
def get_adsb_flight_detail(fr24_id: str, service: AllSkyService = Depends(get_service)):
    """Full Flightradar24 flight detail and complete historical trail from departure airport."""
    try:
        return service.get_fr24_flight_detail(fr24_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("/api/satellites")
def get_satellites(track: float = Query(default=0.0, ge=0.0, le=120.0),
                   below: bool = Query(default=False),
                   limit: int = Query(default=60, ge=1, le=400),
                   ids: str = Query(default=""),
                   trail: float = Query(default=0.0, ge=0.0, le=1800.0),
                   request: Request = None,
                   service: AllSkyService = Depends(get_service)):
    """Large satellites above the horizon now: az/alt, range, sunlit, lat/lon/height and position in the camera
    frame. track=<minutes> adds each of the highest ones' path (5 min back, <minutes> ahead); below=true also
    lists those under the horizon (3D map); ids=<norad,...> also tracks those."""
    from src.auth.middleware import is_admin
    id_list = [int(i) for i in ids.split(",") if i.strip().isdigit()]
    if not is_admin(request):
        id_list = id_list[:GUEST_MAX_SAT_IDS]
        trail = min(trail, GUEST_MAX_TRAIL_S)
    track_ids = frozenset(id_list)
    raw = _sat_cache.get_or_compute(
        (round(track, 1), below, limit, tuple(sorted(track_ids)), round(trail, 0)),
        lambda: service.get_satellites_now(track_min=track, below=below, limit=limit, trail_s=trail,
                                           track_ids=set(track_ids)))
    return public_view(request, service, raw)


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
    """Set the ADS-B receiver (SBS-1 feed host/port), Flightradar24 Enterprise credentials, or turn flight matching on/off (persisted)."""
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
    request: Request,
    fps: float = Query(default=5.0, ge=0.5, le=30.0),
    downscale: int = Query(default=960, ge=320, le=2592),
    limit: Optional[int] = Query(default=None, ge=1),
    service: AllSkyService = Depends(get_service),
):
    """
    Multipart MJPEG video stream for live browser video preview.
    """
    from src.auth.middleware import is_admin
    key, max_s = None, None
    if not is_admin(request):
        key = stream_client_key(request)
        if not stream_limiter.acquire(key):
            raise HTTPException(status_code=429, detail="Too many live streams, try again shortly",
                                headers={"Retry-After": "10"})
        fps = min(fps, GUEST_MJPEG_MAX_FPS)
        downscale = min(downscale, GUEST_MJPEG_MAX_WIDTH)
        max_s = GUEST_STREAM_MAX_S
        limit = min(limit, int(fps * max_s)) if limit else None
    gen = mjpeg_frame_generator(service, fps=fps, downscale_width=downscale, max_frames=limit, max_seconds=max_s)
    return StreamingResponse(
        guarded_stream(gen, key),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@router.get("/api/stream/sse")
@router.get("/api/sse")
def get_sse_stream(
    request: Request,
    interval: float = Query(default=1.0, ge=0.25, le=10.0),
    service: AllSkyService = Depends(get_service),
):
    """
    Server-Sent Events (SSE) push stream for real-time telemetry and status updates.
    """
    from src.auth.middleware import is_admin
    key, max_s = None, None
    if not is_admin(request):
        key = stream_client_key(request)
        if not stream_limiter.acquire(key):
            raise HTTPException(status_code=429, detail="Too many live streams, try again shortly",
                                headers={"Retry-After": "10"})
        max_s = GUEST_STREAM_MAX_S
    return StreamingResponse(
        guarded_stream(sse_event_generator(service, interval=interval, view=_view_for_request(request, service),
                                           max_seconds=max_s), key),
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


@router.post("/api/station/star-align")
def station_star_align(req: ManualStarSolveRequest, service: AllSkyService = Depends(get_service)):
    """Identify 4+ stars clicked on the live frame and (apply=true) store the camera pointing from them."""
    try:
        return service.manual_star_solve(req.points, apply=req.apply)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/api/station/plate-solve/start")
def start_station_plate_solve(
    fov_hint: str = Query(default="auto", max_length=16),
    mode: str = Query(default="auto", pattern="^(auto|deep)$"),
    service: AllSkyService = Depends(get_service),
):
    """Start plate-solving in the background; poll /api/station/plate-solve/status.
    mode=deep re-measures the lens and sensor instead of reusing the solved lens."""
    return service.start_plate_solve_job(fov_hint=fov_hint, deep=mode == "deep")


@router.post("/api/station/calibration/reset")
def reset_station_calibration(service: AllSkyService = Depends(get_service)):
    """Reset solved lens, sensor and mounting parameters to the attached camera's defaults."""
    return service.reset_station_calibration()


@router.get("/api/station/plate-solve/status")
def get_station_plate_solve_status(request: Request, service: AllSkyService = Depends(get_service)):
    """State of the background plate-solve job (idle / running / success / failed)."""
    return public_view(request, service, service.get_plate_solve_status())


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



# ----------------------------------------------------------------------
# Community: opt-in sharing of a signed station card between Zenivo stations (docs/COMMUNITY.md)
# ----------------------------------------------------------------------

_COMMUNITY_MAX_BODY = 16 * 1024
_COMMUNITY_CORS = {"Access-Control-Allow-Origin": "*", "Cache-Control": "no-store"}


@router.get("/api/community/status")
def get_community_status(request: Request, service: AllSkyService = Depends(get_service)):
    """Public summary; the per-address health list (`targets`) is only added for the admin."""
    from src.auth.middleware import is_admin
    return service.community.status(admin=is_admin(request))


@router.post("/api/community/check")
def post_community_check(service: AllSkyService = Depends(get_service)):
    """Admin: run a gossip round now."""
    service.community.check_now()
    return {"ok": True}


@router.get("/api/community/settings")
def get_community_settings(service: AllSkyService = Depends(get_service)):
    return service.community.settings()


@router.post("/api/community/settings")
def set_community_settings(req: CommunitySettingsRequest, service: AllSkyService = Depends(get_service)):
    """Save the settings; turning sharing on creates the station key, starts the gossip worker and runs a round."""
    try:
        return service.community.update_settings(req.model_dump(exclude_none=True))
    except RuntimeError as exc:  # `cryptography` missing
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.get("/api/community/card")
def get_community_card(service: AllSkyService = Depends(get_service)):
    card = service.community.own_card()
    if card is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Community sharing is off")
    return card


@router.get("/api/community/peers")
def get_community_peers(service: AllSkyService = Depends(get_service)):
    """Own card plus every verified card we know. Open to any origin (no cookies) so other stations' pages can read it."""
    return Response(content=service.community.peers_json(), media_type="application/json", headers=_COMMUNITY_CORS)


@router.post("/api/community/announce")
async def announce_community_card(request: Request, service: AllSkyService = Depends(get_service)):
    """Another station tells us its card. Accepted only when the signature verifies; its url is never fetched here."""
    from starlette.concurrency import run_in_threadpool
    from src.community.card import CardError
    from src.community.manager import RateLimited
    if not service.community.enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Community sharing is off")
    length = request.headers.get("content-length")
    if length is not None and length.isdigit() and int(length) > _COMMUNITY_MAX_BODY:
        raise HTTPException(status_code=413, detail="Card too large")
    chunks, size = [], 0
    async for chunk in request.stream():  # bounded read: a chunked body cannot skip the size cap
        size += len(chunk)
        if size > _COMMUNITY_MAX_BODY:
            raise HTTPException(status_code=413, detail="Card too large")
        chunks.append(chunk)

    def _no_constant(name):
        raise ValueError(name)

    try:  # NaN / Infinity are not JSON: refuse them here, before any card logic sees them
        card = json.loads(b"".join(chunks).decode("utf-8"), parse_constant=_no_constant)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Body is not JSON")
    from src.auth.policy import client_key
    key = client_key({k.lower(): v for k, v in request.headers.items()}, request.client.host if request.client else None)
    try:  # verification and the registry work are blocking: off the event loop
        await run_in_threadpool(service.community.accept_announce, card, key)
    except RateLimited:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many announces")
    except CardError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Card rejected: {exc}")
    return {"ok": True, "relay": service.community.relay_enabled()}   # senders without a public URL read `relay`


_RELAY_MAX_BODY = 256 * 1024


@router.post("/api/community/relay/{station_id}")
async def post_community_relay(station_id: str, request: Request, service: AllSkyService = Depends(get_service)):
    """A station without a public URL pushes its large preview (raw image/jpeg). Refused (404) unless the owner turned
    `relay_previews` on. Checks run cheapest first: rate limit, size, known card without url, signature, interval,
    then the image is re-encoded; see docs/COMMUNITY.md."""
    from starlette.concurrency import run_in_threadpool
    from src.auth.policy import client_key
    from src.community.manager import RateLimited
    from src.community.relay import RelayRefused
    mgr = service.community
    if not mgr.relay_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This station does not relay previews")
    key = client_key({k.lower(): v for k, v in request.headers.items()}, request.client.host if request.client else None)
    try:
        mgr.relay_rate(key)
    except RateLimited:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many uploads")
    length = request.headers.get("content-length")
    if length is not None and length.isdigit() and int(length) > _RELAY_MAX_BODY:
        raise HTTPException(status_code=413, detail="Image too large")
    chunks, size = [], 0
    async for chunk in request.stream():  # bounded read: a chunked body cannot skip the cap
        size += len(chunk)
        if size > _RELAY_MAX_BODY:
            raise HTTPException(status_code=413, detail="Image too large")
        chunks.append(chunk)
    try:
        await run_in_threadpool(mgr.accept_relay, station_id, request.headers.get("x-zenivo-time"),
                                request.headers.get("x-zenivo-sig"), b"".join(chunks))
    except RelayRefused as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)
    return {"ok": True}


@router.get("/api/community/relay/{station_file}")
def get_community_relay(station_file: str, service: AllSkyService = Depends(get_service)):
    """Public: the large preview this station holds for a station without a public URL (<id>.jpg)."""
    data = service.community.relay_jpeg(station_file[:-4]) if station_file.endswith(".jpg") else None
    if data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No preview held for this station")
    return Response(content=data, media_type="image/jpeg", headers={
        "X-Content-Type-Options": "nosniff", "Cache-Control": "public, max-age=300",
        "Cross-Origin-Resource-Policy": "cross-origin", "Access-Control-Allow-Origin": "*"})


@router.get("/api/community/thumb.jpg")
def get_community_thumb(service: AllSkyService = Depends(get_service)):
    """Latest frame, at most 320 px wide (JPEG q70), cached for a minute."""
    try:
        data = service.community.thumb_jpeg()
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Thumbnail failed: {exc}")
    if data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Preview sharing is off")
    return Response(content=data, media_type="image/jpeg", headers={**_COMMUNITY_CORS, "Cache-Control": "public, max-age=60"})


@router.get("/api/community/preview.jpg")
def get_community_preview(service: AllSkyService = Depends(get_service)):
    """Latest frame, at most 1280 px wide (JPEG q80), cached for a minute; only when the owner shares the large preview."""
    try:
        data = service.community.preview_jpeg()
    except Exception:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Preview failed")
    if data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="The large preview is not shared")
    return Response(content=data, media_type="image/jpeg", headers={**_COMMUNITY_CORS, "Cache-Control": "public, max-age=60"})
