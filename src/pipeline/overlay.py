"""
Astrometric Star & Constellation Fisheye Overlay Pipeline for IMX335 AllSky Observatory.
Calculates Local Sidereal Time (LST), converts celestial RA/Dec to topocentric Alt/Az,
and projects them onto the allsky fisheye image plane using equidistant lens modeling (r = f * theta).
"""

import math
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Any
import numpy as np
import cv2


# Catalog of major bright stars: RA (deg), Dec (deg), visual magnitude, name
BRIGHT_STARS_CATALOG = [
    {"name": "Polaris", "ra": 37.95, "dec": 89.26, "mag": 1.98},
    {"name": "Sirius", "ra": 101.28, "dec": -16.72, "mag": -1.46},
    {"name": "Vega", "ra": 279.23, "dec": 38.78, "mag": 0.03},
    {"name": "Arcturus", "ra": 213.91, "dec": 19.18, "mag": -0.05},
    {"name": "Capella", "ra": 79.17, "dec": 45.99, "mag": 0.08},
    {"name": "Rigel", "ra": 78.63, "dec": -8.20, "mag": 0.13},
    {"name": "Procyon", "ra": 114.82, "dec": 5.22, "mag": 0.34},
    {"name": "Betelgeuse", "ra": 88.79, "dec": 7.41, "mag": 0.50},
    {"name": "Altair", "ra": 297.69, "dec": 8.87, "mag": 0.77},
    {"name": "Aldebaran", "ra": 68.98, "dec": 16.51, "mag": 0.85},
    {"name": "Antares", "ra": 247.35, "dec": -26.43, "mag": 1.06},
    {"name": "Spica", "ra": 201.29, "dec": -11.16, "mag": 0.98},
    {"name": "Pollux", "ra": 116.33, "dec": 28.02, "mag": 1.14},
    {"name": "Deneb", "ra": 310.35, "dec": 45.28, "mag": 1.25},
    {"name": "Regulus", "ra": 152.09, "dec": 11.97, "mag": 1.36},
    {"name": "Castor", "ra": 113.65, "dec": 31.89, "mag": 1.58},
    {"name": "Bellatrix", "ra": 81.28, "dec": 6.35, "mag": 1.64},
    # Ursa Major (Big Dipper) stars
    {"name": "Dubhe", "ra": 165.93, "dec": 61.75, "mag": 1.79},
    {"name": "Merak", "ra": 165.46, "dec": 56.38, "mag": 2.37},
    {"name": "Phecda", "ra": 178.46, "dec": 53.69, "mag": 2.44},
    {"name": "Megrez", "ra": 183.86, "dec": 57.03, "mag": 3.31},
    {"name": "Alioth", "ra": 193.51, "dec": 55.96, "mag": 1.77},
    {"name": "Mizar", "ra": 200.98, "dec": 54.92, "mag": 2.23},
    {"name": "Alkaid", "ra": 206.88, "dec": 49.31, "mag": 1.86},
    # Cassiopeia stars
    {"name": "Schedar", "ra": 10.13, "dec": 56.54, "mag": 2.24},
    {"name": "Caph", "ra": 2.29, "dec": 59.15, "mag": 2.28},
    {"name": "Gamma Cas", "ra": 14.18, "dec": 60.72, "mag": 2.47},
    {"name": "Ruchbah", "ra": 20.30, "dec": 60.23, "mag": 2.68},
    {"name": "Segin", "ra": 26.99, "dec": 63.67, "mag": 3.37},
    # Orion Belt
    {"name": "Alnitak", "ra": 85.19, "dec": -1.94, "mag": 1.77},
    {"name": "Alnilam", "ra": 84.05, "dec": -1.20, "mag": 1.69},
    {"name": "Mintaka", "ra": 83.00, "dec": -0.30, "mag": 2.23},
]

# Major constellation stick-figure connections (pairs of star names)
CONSTELLATION_LINES = [
    # Ursa Major
    ("Dubhe", "Merak"),
    ("Merak", "Phecda"),
    ("Phecda", "Megrez"),
    ("Megrez", "Dubhe"),
    ("Megrez", "Alioth"),
    ("Alioth", "Mizar"),
    ("Mizar", "Alkaid"),
    # Cassiopeia
    ("Caph", "Schedar"),
    ("Schedar", "Gamma Cas"),
    ("Gamma Cas", "Ruchbah"),
    ("Ruchbah", "Segin"),
    # Orion
    ("Betelgeuse", "Bellatrix"),
    ("Betelgeuse", "Alnitak"),
    ("Bellatrix", "Mintaka"),
    ("Mintaka", "Alnilam"),
    ("Alnilam", "Alnitak"),
    ("Alnitak", "Rigel"),
    # Summer Triangle
    ("Vega", "Altair"),
    ("Altair", "Deneb"),
    ("Deneb", "Vega"),
    # Gemini
    ("Castor", "Pollux"),
]


def calculate_local_sidereal_time(dt: datetime, longitude_deg: float) -> float:
    """
    Compute Local Sidereal Time (LST) in degrees for a given UTC datetime and station longitude.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    # Julian Date from Unix timestamp
    epoch_sec = dt.timestamp()
    jd = epoch_sec / 86400.0 + 2440587.5
    d = jd - 2451545.0
    t = d / 36525.0

    # Greenwich Mean Sidereal Time in degrees
    gmst = 280.46061837 + 360.98564736629 * d + 0.000387933 * (t ** 2) - (t ** 3) / 38710000.0
    lst = (gmst + longitude_deg) % 360.0
    return float(lst)


def equatorial_to_horizontal(
    ra_deg: float,
    dec_deg: float,
    lst_deg: float,
    latitude_deg: float,
) -> Tuple[float, float]:
    """
    Convert Equatorial coordinates (RA, Dec) to topocentric Horizon coordinates (Alt, Az).

    Returns:
        (altitude_deg, azimuth_deg) in degrees.
        Azimuth is measured North -> East -> South -> West (0 = N, 90 = E, 180 = S, 270 = W).
    """
    ha_rad = math.radians((lst_deg - ra_deg) % 360.0)
    dec_rad = math.radians(dec_deg)
    lat_rad = math.radians(latitude_deg)

    # Altitude
    sin_alt = math.sin(lat_rad) * math.sin(dec_rad) + math.cos(lat_rad) * math.cos(dec_rad) * math.cos(ha_rad)
    sin_alt = max(-1.0, min(1.0, sin_alt))
    alt_rad = math.asin(sin_alt)

    # Azimuth
    cos_alt = math.cos(alt_rad)
    if abs(cos_alt) < 1e-6:
        # At zenith or nadir
        az_deg = 0.0
    else:
        sin_az = -math.cos(dec_rad) * math.sin(ha_rad) / cos_alt
        cos_az = (math.sin(dec_rad) - math.sin(lat_rad) * math.sin(alt_rad)) / (cos_alt * math.cos(lat_rad))
        az_rad = math.atan2(sin_az, cos_az)
        az_deg = (math.degrees(az_rad)) % 360.0

    return math.degrees(alt_rad), az_deg


def project_alt_az_to_fisheye(
    altitude_deg: float,
    azimuth_deg: float,
    center_x: int = 1296,
    center_y: int = 972,
    focal_length_px: float = 618.8,
    heading_offset_deg: float = 0.0,
) -> Optional[Tuple[int, int]]:
    """
    Equidistant fisheye lens projection model: r = f * (90 deg - Alt).
    Converts topocentric Alt/Az (degrees) to 2D image coordinates (u, v).

    Args:
        altitude_deg: Elevation angle above horizon (-90 .. +90 deg).
        azimuth_deg: Azimuth angle (0..360 deg, 0=N, 90=E).
        center_x: Optical center X coordinate.
        center_y: Optical center Y coordinate.
        focal_length_px: Lens focal length in pixels (standard ~618.8 for 1.8mm lens).
        heading_offset_deg: Camera orientation rotation angle relative to True North.

    Returns:
        (u, v) pixel coordinates, or None if the object is below horizon (Alt < 0).
    """
    if altitude_deg < 0.0:
        return None  # Object is below the horizon

    zenith_angle_rad = math.radians(90.0 - altitude_deg)
    r = focal_length_px * zenith_angle_rad

    theta = math.radians(azimuth_deg + heading_offset_deg)
    u = int(round(center_x + r * math.sin(theta)))
    v = int(round(center_y - r * math.cos(theta)))

    return u, v


def render_celestial_overlay(
    canvas: np.ndarray,
    dt: datetime,
    latitude_deg: float = 21.0285,
    longitude_deg: float = 105.8542,
    center_x: int = 1296,
    center_y: int = 972,
    focal_length_px: float = 618.8,
    heading_offset_deg: float = 0.0,
    show_cardinal: bool = True,
    show_zenith: bool = True,
    show_horizon: bool = True,
    show_constellations: bool = True,
    show_stars: bool = True,
    star_color: Tuple[int, int, int] = (0, 255, 255),
    line_color: Tuple[int, int, int] = (0, 165, 255),
    text_color: Tuple[int, int, int] = (220, 220, 220),
) -> np.ndarray:
    """
    Render celestial map overlay (stars, constellation lines, zenith, and cardinal compass points)
    onto a frame or blank canvas.
    """
    annotated = canvas.copy()
    lst = calculate_local_sidereal_time(dt, longitude_deg)

    # 1. Project and store star positions
    star_positions: Dict[str, Tuple[int, int]] = {}
    for star in BRIGHT_STARS_CATALOG:
        alt, az = equatorial_to_horizontal(star["ra"], star["dec"], lst, latitude_deg)
        pos = project_alt_az_to_fisheye(
            alt, az,
            center_x=center_x,
            center_y=center_y,
            focal_length_px=focal_length_px,
            heading_offset_deg=heading_offset_deg,
        )
        if pos is not None:
            star_positions[star["name"]] = pos

    # 2. Render constellation lines
    if show_constellations:
        for s1, s2 in CONSTELLATION_LINES:
            if s1 in star_positions and s2 in star_positions:
                p1 = star_positions[s1]
                p2 = star_positions[s2]
                cv2.line(annotated, p1, p2, line_color, 1, cv2.LINE_AA)

    # 3. Render stars and labels
    if show_stars:
        for star in BRIGHT_STARS_CATALOG:
            name = star["name"]
            if name in star_positions:
                pos = star_positions[name]
                # Radius scaled by magnitude (fainter -> smaller)
                mag = star["mag"]
                radius = max(2, min(5, int(round(5.0 - mag * 0.7))))
                cv2.circle(annotated, pos, radius, star_color, -1, cv2.LINE_AA)
                cv2.putText(
                    annotated,
                    name,
                    (pos[0] + 6, pos[1] - 4),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.35,
                    text_color,
                    1,
                    cv2.LINE_AA,
                )

    # 4. Render Horizon circle (Alt = 0)
    if show_horizon:
        horizon_r = int(round(focal_length_px * math.radians(90.0)))
        cv2.circle(annotated, (center_x, center_y), horizon_r, (80, 80, 80), 1, cv2.LINE_AA)

    # 5. Render Zenith circle / marker (Alt = 90)
    if show_zenith:
        cv2.circle(annotated, (center_x, center_y), 6, (0, 200, 255), 1, cv2.LINE_AA)
        cv2.drawMarker(annotated, (center_x, center_y), (0, 200, 255), cv2.MARKER_CROSS, 10, 1)

    # 6. Render Cardinal compass directions (N, E, S, W)
    if show_cardinal:
        cardinals = [
            ("N", 0.0),
            ("NE", 45.0),
            ("E", 90.0),
            ("SE", 135.0),
            ("S", 180.0),
            ("SW", 225.0),
            ("W", 270.0),
            ("NW", 315.0),
        ]
        horizon_r = focal_length_px * math.radians(90.0)
        cardinal_r = horizon_r * 0.98

        for label, az in cardinals:
            rad = math.radians(az + heading_offset_deg)
            cx_pos = int(round(center_x + cardinal_r * math.sin(rad)))
            cy_pos = int(round(center_y - cardinal_r * math.cos(rad)))

            # Distinguish primary cardinals
            if len(label) == 1:
                cv2.circle(annotated, (cx_pos, cy_pos), 4, (0, 0, 255), -1, cv2.LINE_AA)
                cv2.putText(
                    annotated,
                    label,
                    (cx_pos - 6, cy_pos + 5),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    (0, 255, 255),
                    2,
                    cv2.LINE_AA,
                )
            else:
                cv2.circle(annotated, (cx_pos, cy_pos), 2, (180, 180, 180), -1, cv2.LINE_AA)

    return annotated


def stamp_frame_info(
    frame: np.ndarray,
    timestamp: datetime,
    exposure_s: float,
    gain_db: float,
    brightness: float,
) -> np.ndarray:
    """Copy of frame with capture time, exposure, gain and mean brightness in the top-left corner
    (outside the fisheye sky circle, so star/cloud analysis of archived frames is unaffected)."""
    out = frame.copy()
    w = out.shape[1]
    scale = max(0.4, w / 2000.0)
    thick = max(1, int(round(scale * 1.6)))
    exp_txt = f"{exposure_s * 1000:.2f} ms" if exposure_s < 0.1 else f"{exposure_s:.1f} s"
    lines = [
        timestamp.astimezone().strftime("%Y-%m-%d %H:%M:%S %z"),
        f"Exp {exp_txt}  Gain {gain_db:.1f} dB",
        f"Brightness {brightness:.0f}/255",
    ]
    step = int(30 * scale)
    for i, text in enumerate(lines):
        org = (int(10 * scale), int(28 * scale) + i * step)
        cv2.putText(out, text, org, cv2.FONT_HERSHEY_SIMPLEX, 0.8 * scale, (0, 0, 0), thick + 2, cv2.LINE_AA)
        cv2.putText(out, text, org, cv2.FONT_HERSHEY_SIMPLEX, 0.8 * scale, (255, 255, 255), thick, cv2.LINE_AA)
    return out


def frame_brightness(frame: np.ndarray) -> float:
    """Mean luminance (0-255) of a BGR frame, from a 1/8 subsample."""
    sub = frame[::8, ::8]
    if sub.ndim == 3:
        sub = cv2.cvtColor(np.ascontiguousarray(sub), cv2.COLOR_BGR2GRAY)
    return float(sub.mean())


def stamp_info_box(width: int) -> Tuple[int, int]:
    """(w, h) of the top-left area stamp_frame_info may draw on, for a frame of the given width."""
    scale = max(0.4, width / 2000.0)
    thick = max(1, int(round(scale * 1.6))) + 2
    (tw, _), base = cv2.getTextSize("Exp 999.99 ms  Gain 99.9 dB", cv2.FONT_HERSHEY_SIMPLEX, 0.8 * scale, thick)
    step = int(30 * scale)
    return int(10 * scale) + tw + 4, int(28 * scale) + 2 * step + base + 4
