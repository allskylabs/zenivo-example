"""
Station Configuration, Geolocation, and Astrometric Plate-Solving Calibration Engine.
Integrates with an optional, operator-configured remote plate solver (POST <remote_solver_url>/api/platesolve/solve)
to determine all camera, lens, optical center, FOV, tilt, azimuth, and WCS parameters.
"""

import io
import json
import math
import os
import subprocess
import tempfile
import time
import urllib.request

from src.net import urlopen as net_urlopen
from src.version import __version__
import urllib.error
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
import cv2
import numpy as np


IMX335_SPEC = {
    "sensor_model": "Sony Starvis IMX335",
    "usb_bridge": "Realtek RTS5869 (0bda:5869)",
    "width_px": 2592,
    "height_px": 1944,
    "pixel_pitch_um": 2.0,
    "sensor_width_mm": 5.184,
    "sensor_height_mm": 3.888,
    "sensor_diag_mm": 6.480,
}


def compute_lst_and_zenith(lat_deg: float, lon_deg: float, dt: Optional[datetime] = None) -> Tuple[float, float]:
    """
    Compute Local Sidereal Time (RA of Zenith in degrees) and Zenith Declination.
    """
    if dt is None:
        dt = datetime.now(timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    jd = dt.timestamp() / 86400.0 + 2440587.5
    t = (jd - 2451545.0) / 36525.0
    gmst = (
        280.46061837
        + 360.98564736629 * (jd - 2451545.0)
        + 0.000387933 * (t ** 2)
        - (t ** 3) / 38710000.0
    ) % 360.0
    lst_deg = (gmst + lon_deg) % 360.0
    return round(lst_deg, 4), round(lat_deg, 4)


def radec_to_az_alt(
    ra_deg: float,
    dec_deg: float,
    lat_deg: float,
    lon_deg: float,
    dt: Optional[datetime] = None,
) -> Tuple[float, float, float]:
    """
    Convert celestial (RA, Dec) of image optical center to local horizon:
    - azimuth_deg (0..360 deg, N=0, E=90, S=180, W=270)
    - altitude_deg (-90..+90 deg)
    - zenith_tilt_deg (0..180 deg angle from Zenith)
    """
    lst_deg, _ = compute_lst_and_zenith(lat_deg, lon_deg, dt)
    ha_rad = math.radians((lst_deg - ra_deg + 360.0) % 360.0)
    dec_rad = math.radians(dec_deg)
    lat_rad = math.radians(lat_deg)

    sin_alt = math.sin(dec_rad) * math.sin(lat_rad) + math.cos(dec_rad) * math.cos(lat_rad) * math.cos(ha_rad)
    sin_alt = max(-1.0, min(1.0, sin_alt))
    alt_rad = math.asin(sin_alt)
    alt_deg = math.degrees(alt_rad)

    y = -math.sin(ha_rad) * math.cos(dec_rad)
    x = math.sin(dec_rad) * math.cos(lat_rad) - math.cos(dec_rad) * math.sin(lat_rad) * math.cos(ha_rad)
    az_deg = (math.degrees(math.atan2(y, x)) + 360.0) % 360.0
    zenith_tilt_deg = max(0.0, 90.0 - alt_deg)

    return round(az_deg, 2), round(alt_deg, 2), round(zenith_tilt_deg, 2)


def auto_detect_ip_location() -> Dict[str, Any]:
    """
    Auto-detect station GPS coordinates via public IP geolocation APIs when browser GPS is unavailable.
    Only called when an admin presses the button: the public IP of this host is sent to the (https-only) services below.
    """
    endpoints = [
        ("https://ipwho.is/", "latitude", "longitude", "city", "country"),
        ("https://ipapi.co/json/", "latitude", "longitude", "city", "country_name"),
    ]
    for url, lat_k, lon_k, city_k, country_k in endpoints:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": f"Zenivo/{__version__}"})
            with net_urlopen(req, timeout=4.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                lat = data.get(lat_k)
                lon = data.get(lon_k)
                if lat is not None and lon is not None:
                    city = data.get(city_k, "")
                    country = data.get(country_k, "")
                    loc_name = ", ".join(p for p in [city, country] if p) or "Auto-Detected Location"
                    return {
                        "success": True,
                        "latitude_deg": round(float(lat), 5),
                        "longitude_deg": round(float(lon), 5),
                        "elevation_m": 20.0,
                        "location_name": loc_name,
                        "geo_source": "ip_geolocation",
                    }
        except Exception:
            continue

    return {
        "success": True,
        "latitude_deg": 21.0285,
        "longitude_deg": 105.8542,
        "elevation_m": 20.0,
        "location_name": "Hanoi, Vietnam (Default)",
        "geo_source": "default_fallback",
    }


def _multipart_post_solve(
    endpoint_url: str,
    image_bytes: Optional[bytes],
    fields: Dict[str, Any],
    timeout_s: float = 45.0,
) -> Optional[Dict[str, Any]]:
    """
    Send multipart/form-data or JSON request to the configured remote solver /api/platesolve/solve.
    """
    boundary = f"----AllSkyPlateSolveBoundary{int(time.time() * 1000)}"
    body = io.BytesIO()

    for k, v in fields.items():
        if v is None:
            continue
        body.write(f"--{boundary}\r\n".encode("utf-8"))
        body.write(f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode("utf-8"))
        body.write(f"{v}\r\n".encode("utf-8"))

    if image_bytes is not None:
        body.write(f"--{boundary}\r\n".encode("utf-8"))
        body.write(
            b'Content-Disposition: form-data; name="image"; filename="allsky_capture.jpg"\r\n'
            b"Content-Type: image/jpeg\r\n\r\n"
        )
        body.write(image_bytes)
        body.write(b"\r\n")

    body.write(f"--{boundary}--\r\n".encode("utf-8"))
    payload = body.getvalue()

    req = urllib.request.Request(
        endpoint_url,
        data=payload,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": f"Zenivo/{__version__}",
        },
        method="POST",
    )
    try:
        with net_urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        # The solver answers "no solution" with a JSON body on 4xx/5xx; surface its message
        try:
            body = json.loads(exc.read().decode("utf-8"))
            if isinstance(body, dict):
                body.setdefault("success", False)
                body.setdefault("error", f"HTTP {exc.code}")
                return body
        except Exception:
            pass
        raise


def solve_station_parameters(
    frame_bgr: np.ndarray,
    latitude_deg: float,
    longitude_deg: float,
    elevation_m: float = 20.0,
    fov_hint: str = "auto",
    plate_solving_base_url: str = "",
    sensor_spec: Optional[Dict[str, Any]] = None,
    endpoints: Optional[List[str]] = None,
    timeout_s: float = 180.0,
) -> Dict[str, Any]:
    """
    Solve camera, lens, optical center, FOV (horizontal/vertical/diagonal), focal length,
    azimuth, altitude, zenith tilt, and WCS parameters using the operator-configured remote solver
    (station setting `remote_solver_url`). Uploads a frame, the coordinates and the time to that address.
    With no URL (and no explicit endpoints) nothing is sent and the call fails with a clear message.
    """
    now_dt = datetime.now(timezone.utc)
    obs_time_iso = now_dt.isoformat()

    ret, jpeg_buf = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 92])
    jpeg_bytes = jpeg_buf.tobytes() if ret else None

    base = (plate_solving_base_url or "").strip().rstrip("/")
    if endpoints:
        candidate_endpoints = list(endpoints)
    elif base:
        candidate_endpoints = [f"{base}/api/platesolve/solve"]
    else:
        return {
            "success": False,
            "solved_at": obs_time_iso,
            "error": "local solve not confident; no remote solver configured",
            "attempts": [],
        }

    raw_result: Optional[Dict[str, Any]] = None
    solve_source = "live_camera_frame"
    attempts: List[Dict[str, Any]] = []

    # Only the live camera frame is solved: a reference/sample image would describe some other camera
    if jpeg_bytes is not None:
        for ep in candidate_endpoints:
            try:
                res = _multipart_post_solve(
                    ep,
                    image_bytes=jpeg_bytes,
                    fields={
                        "fovHint": fov_hint,
                        "lat": latitude_deg,
                        "lon": longitude_deg,
                        "obsTime": obs_time_iso,
                    },
                    timeout_s=timeout_s,
                )
            except Exception as exc:
                attempts.append({"endpoint": ep, "error": f"{type(exc).__name__}: {exc}"})
                if isinstance(exc, TimeoutError) or "timed out" in str(exc):
                    break  # the solver is busy on this image; a mirror of the same server would only time out again
                continue
            if res and res.get("success"):
                raw_result = res
                solve_source = f"live_frame ({ep})"
                break
            err = (res or {}).get("error") or (res or {}).get("message") or "no solution"
            attempts.append({"endpoint": ep, "error": str(err)})
            if res is not None:
                break  # the solver answered: the image itself has no solution, other mirrors will agree
    else:
        attempts.append({"endpoint": None, "error": "could not encode camera frame"})

    if not raw_result:
        reason = "; ".join(f"{a['endpoint']}: {a['error']}" for a in attempts) or "no solver reachable"
        return {
            "success": False,
            "solved_at": obs_time_iso,
            "error": reason,
            "attempts": attempts,
        }

    # 4. Construct comprehensive Camera, Lens & Station calibration parameters
    spec = dict(sensor_spec or IMX335_SPEC)
    sensor_w = int(spec.get("width_px") or frame_bgr.shape[1])
    sensor_h = int(spec.get("height_px") or frame_bgr.shape[0])
    pitch_um = float(spec.get("pixel_pitch_um") or 2.0)

    zenith_ra, zenith_dec = compute_lst_and_zenith(latitude_deg, longitude_deg, now_dt)

    wcs = raw_result.get("wcs", {})
    telem = raw_result.get("telemetry", {})
    solve_w = float(raw_result.get("image_width") or wcs.get("image_width") or sensor_w)
    solve_h = float(raw_result.get("image_height") or wcs.get("image_height") or sensor_h)

    scale_x_factor = sensor_w / max(1.0, solve_w)
    scale_y_factor = sensor_h / max(1.0, solve_h)

    center_ra = float(wcs.get("center_ra", telem.get("center_ra_deg", zenith_ra)))
    center_dec = float(wcs.get("center_dec", telem.get("center_dec_deg", zenith_dec)))

    fov_x_deg = float(wcs.get("fov_x_deg", telem.get("fov_x_deg", 180.0)))
    fov_y_deg = float(wcs.get("fov_y_deg", telem.get("fov_y_deg", 135.0)))
    fov_diag_deg = round(math.hypot(fov_x_deg, fov_y_deg), 2)

    # Adjust pixel scale to native IMX335 2592x1944 resolution
    raw_px_scale = float(wcs.get("pixel_scale", telem.get("pixel_scale_arcsec", 250.0)))
    native_px_scale_arcsec = round(raw_px_scale / scale_x_factor, 2)

    # Effective focal length: f(mm) = 206.265 * pixel_pitch(um) / pixel_scale(arcsec/px)
    focal_length_mm = round((206.2648 * pitch_um) / max(0.1, native_px_scale_arcsec), 3)

    orientation_deg = float(raw_result.get("orientation_deg", wcs.get("rotation_deg", 0.0)))
    is_allsky = bool(raw_result.get("is_allsky", fov_x_deg >= 110.0))

    raw_cx = float(wcs.get("center_x") or wcs.get("crpix1") or (solve_w / 2.0))
    raw_cy = float(wcs.get("center_y") or wcs.get("crpix2") or (solve_h / 2.0))
    raw_r90 = float(wcs.get("radius_90") or (min(solve_w, solve_h) * 0.48))

    optical_cx = round(raw_cx * scale_x_factor, 1)
    optical_cy = round(raw_cy * scale_y_factor, 1)
    radius_90_px = round(raw_r90 * min(scale_x_factor, scale_y_factor), 1)
    matched_stars = int(telem.get("matched_stars_count", 48))
    detected_lens = telem.get("detected_device") or (
        f"Fisheye {focal_length_mm:.2f}mm AllSky (180° Hemisphere)"
        if is_allsky
        else f"Rectilinear {focal_length_mm:.2f}mm Lens"
    )
    constellations = [c.get("name", c.get("id")) for c in (raw_result.get("constellations") or [])[:12]]
    cd_matrix = [
        wcs.get("cd11"),
        wcs.get("cd12"),
        wcs.get("cd21"),
        wcs.get("cd22"),
    ]

    az_deg, alt_deg, zenith_tilt_deg = radec_to_az_alt(
        center_ra, center_dec, latitude_deg, longitude_deg, now_dt
    )

    offset_dx_px = round(optical_cx - (sensor_w / 2.0), 1)
    offset_dy_px = round(optical_cy - (sensor_h / 2.0), 1)

    ra_h = int(center_ra / 15.0)
    ra_m = int((center_ra % 15.0) * 4.0)
    ra_s = round(((center_ra % 15.0) * 4.0 % 1.0) * 60.0, 1)
    dec_sign = "+" if center_dec >= 0 else "-"
    abs_dec = abs(center_dec)
    dec_d = int(abs_dec)
    dec_m = int((abs_dec % 1.0) * 60.0)
    dec_s = round(((abs_dec % 1.0) * 60.0 % 1.0) * 60.0, 1)

    return {
        "success": True,
        "plate_solver_endpoint": base or "custom endpoints",
        "solve_source": solve_source,
        "solved_at": obs_time_iso,
        "station": {
            "latitude_deg": round(float(latitude_deg), 5),
            "longitude_deg": round(float(longitude_deg), 5),
            "elevation_m": round(float(elevation_m), 1),
            "zenith_ra_deg": zenith_ra,
            "zenith_dec_deg": zenith_dec,
        },
        "camera_sensor": spec,
        "optics": {
            "lens_model": detected_lens,
            "projection_type": "Fisheye Equidistant AllSky" if is_allsky else "Gnomonic TAN Rectilinear",
            "is_allsky": is_allsky,
            "focal_length_mm": focal_length_mm,
            "pixel_scale_arcsec_px": native_px_scale_arcsec,
            "fov_horizontal_deg": round(fov_x_deg, 2),
            "fov_vertical_deg": round(fov_y_deg, 2),
            "fov_diagonal_deg": fov_diag_deg,
            "optical_center_x_px": optical_cx,
            "optical_center_y_px": optical_cy,
            "sensor_center_offset_px": [offset_dx_px, offset_dy_px],
            "horizon_radius_90_px": radius_90_px,
        },
        "orientation": {
            "center_ra_deg": round(center_ra, 4),
            "center_dec_deg": round(center_dec, 4),
            "center_ra_hms": f"{ra_h:02d}h {ra_m:02d}m {ra_s:04.1f}s",
            "center_dec_dms": f"{dec_sign}{dec_d:02d}° {dec_m:02d}' {dec_s:04.1f}\"",
            "azimuth_deg": az_deg,
            "altitude_deg": alt_deg,
            "zenith_tilt_deg": zenith_tilt_deg,
            "north_position_angle_deg": round(orientation_deg, 2),
            "rotation_deg": round(orientation_deg, 2),
            "cd_matrix": cd_matrix,
        },
        "matched_stars_count": matched_stars,
        "constellations_in_fov": constellations,
    }
