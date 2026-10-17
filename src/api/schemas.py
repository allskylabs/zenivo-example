"""
Pydantic Request and Response Schemas for IMX335 Edge Web API.
Adheres to PROJECT.md § Interface Contracts and AC3 verification specifications.
"""

from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field


class ExposureControlRequest(BaseModel):
    """Request payload for updating camera hardware exposure."""
    exposure_s: float = Field(..., ge=0.00015, le=90.0, description="Exposure duration in seconds")
    gain_db: Optional[float] = Field(None, ge=0.0, le=72.0, description="Analog/digital gain in dB")


class GainControlRequest(BaseModel):
    """Request payload for updating sensor analog/digital gain."""
    gain_db: float = Field(..., ge=0.0, le=72.0, description="Sensor gain in dB")


class CameraModeRequest(BaseModel):
    """Request payload for toggling camera auto/manual exposure mode."""
    mode: Optional[str] = Field(None, description="Exposure mode: 'auto' or 'manual'")
    auto: Optional[bool] = Field(None, description="Boolean flag: True for auto, False for manual")


class MasterDarkRequest(BaseModel):
    """Request payload for generating a Master Dark calibration stack."""
    num_frames: int = Field(default=9, ge=3, le=50, description="Number of dark frames to stack")
    exposure_s: float = Field(default=2.0, ge=0.001, le=90.0, description="Exposure duration in seconds")
    gain_db: float = Field(default=36.0, ge=0.0, le=72.0, description="Sensor gain in dB")


class ImageTransformRequest(BaseModel):
    """Request payload for image orientation: mirror (horizontal), flip (vertical) and rotation."""
    flip_horizontal: Optional[bool] = Field(None, description="Mirror image left-right")
    flip_vertical: Optional[bool] = Field(None, description="Flip image upside-down")
    rotate_deg: Optional[int] = Field(None, description="Rotation in degrees: 0 or 180")
    rotate_isp_180: Optional[bool] = Field(
        None, description="Extra 180° rotation for day (camera ISP / MJPG) frames only; RAW10 night frames unchanged"
    )


class WhiteBalanceRequest(BaseModel):
    """Request payload for RAW10 white balance."""
    mode: Optional[str] = Field(None, pattern="^(auto|manual)$", description="'auto' or 'manual'")
    r_gain: Optional[float] = Field(None, ge=0.4, le=4.0, description="Manual red gain relative to green")
    b_gain: Optional[float] = Field(None, ge=0.4, le=4.0, description="Manual blue gain relative to green")


class OutputResolutionRequest(BaseModel):
    """Request payload for selecting the output image resolution preset."""
    width: int = Field(..., ge=320, le=2592, description="Output width in pixels")
    height: int = Field(..., ge=240, le=1944, description="Output height in pixels")


class StarBoostRequest(BaseModel):
    """Request payload for night star boost (software sum-binning of the viewer frame)."""
    enabled: Optional[bool] = Field(None, description="Turn star boost on or off")
    bin: Optional[int] = Field(None, ge=2, le=4, description="NxN sum-binning window")


class PerformanceSettingsRequest(BaseModel):
    """Request payload for CPU/quality trade-offs; omitted fields stay unchanged."""
    min_capture_interval_s: Optional[float] = Field(None, ge=0.4, le=60, description="Minimum seconds between captures")
    analysis_max_width: Optional[int] = Field(None, ge=320, le=2592, description="Star/cloud analysis width")
    sky_analysis_enabled: Optional[bool] = Field(None, description="Turn star count / cloud cover sampling on or off")
    analysis_interval_s: Optional[float] = Field(
        None, ge=10, le=3600, description="Seconds between star/cloud samples"
    )
    stream_max_width: Optional[int] = Field(None, ge=320, le=2592, description="Live stream JPEG width")
    stream_jpeg_quality: Optional[int] = Field(None, ge=40, le=95, description="Live stream JPEG quality")


class EventConfigRequest(BaseModel):
    """Turn detection of each sky event type on or off; omitted types stay unchanged."""
    meteor: Optional[bool] = None
    plane: Optional[bool] = None
    satellite: Optional[bool] = None
    clouds: Optional[bool] = None
    flash: Optional[bool] = None


class CloudConfigRequest(BaseModel):
    """Request payload for cloud cover analysis: sky region and/or forgetting the learned clear-sky reference."""
    sky_roi: Optional[List[float]] = Field(
        None, min_length=4, max_length=4, description="Sky region as fractions [left, top, right, bottom]"
    )
    reset_reference: bool = Field(False, description="Forget the learned clear-sky reference (after re-aiming the camera)")


class SkyMaskRequest(BaseModel):
    """Plate-solve sky mask: polygons in frame fractions [[x, y], ...] (0..1, x right, y down)."""
    sky: List[List[List[float]]] = Field(
        default_factory=list, max_length=16, description="Open-sky polygons (none = the whole frame)"
    )
    obstruction: List[List[List[float]]] = Field(
        default_factory=list, max_length=16, description="Buildings, trees and lit facades to ignore"
    )


class FocusConfigRequest(BaseModel):
    """Focus assistant region of interest and measurement method."""
    cx: Optional[float] = Field(None, ge=0.0, le=1.0, description="ROI centre x as a fraction of the frame width")
    cy: Optional[float] = Field(None, ge=0.0, le=1.0, description="ROI centre y as a fraction of the frame height")
    roi_size: Optional[int] = Field(None, ge=64, le=1024, description="ROI side in native pixels")
    method: Optional[str] = Field(None, pattern="^(auto|stars|lights|contrast)$", description="Focus measure")
    smooth: Optional[int] = Field(None, ge=1, le=30, description="Frames in the robust running average of the score")
    reset: bool = Field(False, description="Forget the score history and best score")


class LensRequest(BaseModel):
    """Manual focus of a motorised lens, in dioptres (1 / distance in m; 0 = infinity)."""
    position: Optional[float] = Field(None, ge=0.0, le=32.0, description="Absolute lens position (D)")
    delta: Optional[float] = Field(None, ge=-5.0, le=5.0, description="Relative move (D)")
    tracking: Optional[bool] = Field(None, description="Night focus tracking (temperature drift) on/off")
    mode: Optional[str] = Field(None, pattern="^(auto|manual)$", description="auto: temperature compensation + nightly star checks; manual: hold")
    reset_learning: bool = Field(False, description="Forget the learned focus-vs-temperature samples")


class LensAutofocusRequest(BaseModel):
    mode: str = Field("sweep", pattern="^(sweep|camera)$", description="sweep: this app's score; camera: libcamera AF")


class StationLocationRequest(BaseModel):
    """Request payload for setting station coordinates and/or manually adjusting optical & orientation parameters."""
    latitude_deg: Optional[float] = Field(default=None, ge=-90.0, le=90.0, description="Station latitude in degrees (-90 to +90)")
    longitude_deg: Optional[float] = Field(default=None, ge=-180.0, le=180.0, description="Station longitude in degrees (-180 to +180)")
    latitude: Optional[float] = Field(default=None, ge=-90.0, le=90.0, description="Alias for latitude_deg")
    longitude: Optional[float] = Field(default=None, ge=-180.0, le=180.0, description="Alias for longitude_deg")
    elevation_m: float = Field(default=20.0, ge=-500.0, le=9000.0, description="Station elevation in meters")
    location_name: Optional[str] = Field(default=None, description="Station name or city")
    station_name: Optional[str] = Field(default=None, description="Alias for location_name")
    geo_source: Optional[str] = Field(default="manual", description="Coordinate source: browser_gps, ip_geolocation, or manual")
    fov_hint: Optional[str] = Field(default="auto", description="Optional FOV hint in degrees or 'auto'")
    auto_plate_solve: bool = Field(default=False, description="Only run plate-solving when explicitly requested")
    # Optional manual optical & orientation overrides ("fix, settings" on Web UI)
    lens_model: Optional[str] = None
    focal_length_mm: Optional[float] = None
    pixel_scale_arcsec_px: Optional[float] = None
    fov_horizontal_deg: Optional[float] = None
    fov_vertical_deg: Optional[float] = None
    fov_diagonal_deg: Optional[float] = None
    optical_center_x_px: Optional[float] = None
    optical_center_y_px: Optional[float] = None
    horizon_radius_90_px: Optional[float] = None
    azimuth_deg: Optional[float] = None
    altitude_deg: Optional[float] = None
    zenith_tilt_deg: Optional[float] = None
    north_position_angle_deg: Optional[float] = None


class HistogramStats(BaseModel):
    mean: float
    stddev: float
    median: int
    clipping_low_pct: float
    clipping_high_pct: float


class HistogramResponse(BaseModel):
    r: List[int]
    g: List[int]
    b: List[int]
    y: List[int]
    calculation_time_ms: float
    stats: HistogramStats


class GalleryItem(BaseModel):
    type: str
    url: str
    title: Optional[str] = None
    timestamp: Optional[str] = None


class GalleryResponse(BaseModel):
    date: str
    items: List[GalleryItem]


class MeteorEventItem(BaseModel):
    event_id: str
    type: str = "meteor"
    timestamp: str
    length_px: float
    aspect_ratio: float
    peak_dn: int
    bbox: List[int]
    thumbnail_url: Optional[str] = None


class MeteorEventsResponse(BaseModel):
    events: List[MeteorEventItem]
    total_count: int


class StoragePolicyRequest(BaseModel):
    """Retention policy update; omitted fields keep their current value."""
    frames_keep_days: Optional[int] = Field(None, ge=1, le=365)
    products_keep_days: Optional[int] = Field(None, ge=1, le=3650)
    max_data_gb: Optional[float] = Field(None, ge=0.5, le=10000)
    min_free_gb: Optional[float] = Field(None, ge=0.1, le=1000)
    archive_interval_s: Optional[float] = Field(None, ge=5, le=3600)
    timelapse_refresh_s: Optional[float] = Field(None, ge=60, le=86400)
