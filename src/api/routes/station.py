"""
Station setup, sky mask and astrometric plate-solving endpoints.
"""

from fastapi import HTTPException, Query, Request, status, Depends

from src.api.schemas import (
    TimezoneRequest,
    StationLocationRequest,
    SkyMaskRequest,
    ManualStarSolveRequest,
)
from src.api.service import AllSkyService
from src.api.routes.common import (router, get_service, public_view)


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


@router.post("/api/station/timezone")
def set_station_timezone(req: TimezoneRequest, service: AllSkyService = Depends(get_service)):
    """Time zone of the station: "" = the operating system's, "coords" = whole-hour offset from the longitude, or an IANA name."""
    try:
        return service.set_timezone(req.timezone)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


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


@router.post("/api/station/sky-mask/auto")
def station_sky_mask_auto(service: AllSkyService = Depends(get_service)):
    """Propose sky and obstruction polygons from the current frame; nothing is saved until you apply them."""
    try:
        return service.auto_sky_mask()
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
