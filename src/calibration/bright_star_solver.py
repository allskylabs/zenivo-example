"""
Bright-star pointing solver for light-polluted skies.

General blind plate solvers need dozens of faint stars. Under city skies only a handful of the
brightest stars show, but with a known station position and time their alt/az is known, so the
camera pointing (azimuth, altitude, roll) and horizontal FOV can be found by matching pairs:

1. Detect compact bright points in the frame (background-subtracted, foreground blobs rejected).
2. For each pair of the brightest detections and each pair of catalog stars above the horizon whose
   angular separations agree (for a candidate FOV), solve the rotation camera -> sky (TRIAD).
3. Score each hypothesis by how many other catalog stars land on detections; keep the best and refine
   the rotation (Kabsch) and focal length on all matches.

Rectilinear (gnomonic) lens model, image x to the right, y down, optical axis forward.

Under a bright city sky a single frame is dominated by hot pixels, so the service captures a short
series of long exposures and keeps only points that persist and drift at the sidereal rate
(track_sidereal_points). When the lens intrinsics are already known, solve_fixed_lens only has to find
the rotation, which 3-4 stars (planets included) pin down by triangle matching.
"""

import itertools
import math
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

_CATALOG_JS = os.path.join(os.path.dirname(__file__), "..", "web", "sky_catalog.js")
_DEEP_CATALOG = os.path.join(os.path.dirname(__file__), "stars_mag45.json")
_catalog_cache: Optional[List[Dict[str, Any]]] = None


def load_bright_star_catalog(path: str = _CATALOG_JS) -> List[Dict[str, Any]]:
    """Named bright stars (name, ra, dec, mag) parsed from the web UI catalog."""
    global _catalog_cache
    if _catalog_cache is not None and path == _CATALOG_JS:
        return _catalog_cache
    with open(path, "r", encoding="utf-8") as f:
        js = f.read()
    block = js[js.index("stars: ["): js.index("messier: [")]
    stars = [
        {"name": m[0], "ra": float(m[1]), "dec": float(m[2]), "mag": float(m[3])}
        for m in re.findall(r'name: "([^"]+)".*?ra: ([\d.\-]+), dec: ([\d.\-]+), mag: ([\d.\-]+)', block)
    ]
    if path == _CATALOG_JS:
        _catalog_cache = stars
    return stars


_deep_cache: Optional[List[Dict[str, Any]]] = None


def load_deep_catalog() -> List[Dict[str, Any]]:
    """Web catalog plus the Hipparcos stars down to mag 4.5 (narrow lenses see few bright stars)."""
    global _deep_cache
    if _deep_cache is None:
        _deep_cache = _merge_deep_catalog(load_bright_star_catalog())
    return _deep_cache


def _merge_deep_catalog(stars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Web catalog names win where both catalogs list a star."""
    try:
        import json
        with open(_DEEP_CATALOG, "r", encoding="utf-8") as f:
            deep = json.load(f)["stars"]
    except (OSError, ValueError, KeyError):
        return stars
    if not stars:
        return [{"name": n, "ra": ra, "dec": dec, "mag": m} for n, ra, dec, m in deep]
    known = np.radians([[s["ra"], s["dec"]] for s in stars])
    kv = np.column_stack([np.cos(known[:, 1]) * np.cos(known[:, 0]),
                          np.cos(known[:, 1]) * np.sin(known[:, 0]), np.sin(known[:, 1])])
    out = list(stars)
    for name, ra, dec, mag in deep:
        r, d = math.radians(ra), math.radians(dec)
        v = np.array([math.cos(d) * math.cos(r), math.cos(d) * math.sin(r), math.sin(d)])
        if float((kv @ v).max()) > math.cos(math.radians(0.2)):
            continue  # already in the web catalog
        out.append({"name": name, "ra": ra, "dec": dec, "mag": mag})
    return out


def _precess_from_j2000(ra_deg: np.ndarray, dec_deg: np.ndarray, when: datetime) -> Tuple[np.ndarray, np.ndarray]:
    """J2000 RA/Dec -> mean equinox of date (IAU 1976). ~0.36° by 2026, which matters for a fixed-lens fit."""
    t = (when.timestamp() / 86400.0 + 2440587.5 - 2451545.0) / 36525.0
    zeta = math.radians((2306.2181 * t + 0.30188 * t * t) / 3600.0)
    z = math.radians((2306.2181 * t + 1.09468 * t * t) / 3600.0)
    theta = math.radians((2004.3109 * t - 0.42665 * t * t) / 3600.0)
    ra, dec = np.radians(ra_deg), np.radians(dec_deg)
    a = np.cos(dec) * np.sin(ra + zeta)
    b = math.cos(theta) * np.cos(dec) * np.cos(ra + zeta) - math.sin(theta) * np.sin(dec)
    c = math.sin(theta) * np.cos(dec) * np.cos(ra + zeta) + math.cos(theta) * np.sin(dec)
    return (np.degrees(np.arctan2(a, b) + z) % 360.0), np.degrees(np.arcsin(np.clip(c, -1, 1)))


def stars_alt_az(stars: Sequence[Dict[str, Any]], lat_deg: float, lon_deg: float, when: datetime) -> np.ndarray:
    """Return array (N, 2) of apparent [alt, az] degrees (az from north through east) for J2000 RA/Dec."""
    jd = when.timestamp() / 86400.0 + 2440587.5
    d = jd - 2451545.0
    lst = math.radians((280.46061837 + 360.98564736629 * d + lon_deg) % 360.0)
    lat = math.radians(lat_deg)
    ra_d, dec_d = _precess_from_j2000(np.array([s["ra"] for s in stars], dtype=np.float64),
                                      np.array([s["dec"] for s in stars], dtype=np.float64), when)
    ra, dec = np.radians(ra_d), np.radians(dec_d)
    ha = lst - ra
    sin_alt = np.sin(lat) * np.sin(dec) + np.cos(lat) * np.cos(dec) * np.cos(ha)
    alt = np.degrees(np.arcsin(np.clip(sin_alt, -1, 1)))
    az = np.arctan2(-np.cos(dec) * np.sin(ha), (np.sin(dec) - np.sin(lat) * sin_alt) / np.cos(lat))
    # Atmospheric refraction (Bennett): 0.5° at the horizon, which a horizon-facing camera sees
    h = np.maximum(alt, -1.0)
    alt = alt + np.where(alt > -1.0, 1.0 / np.tan(np.radians(h + 7.31 / (h + 4.4))) / 60.0, 0.0)
    return np.column_stack([alt, np.degrees(az) % 360.0])


# Keplerian elements from JPL "Approximate Positions of the Planets" (Standish), valid 1800-2050, J2000
# ecliptic: (a [au], e, I, L, long. perihelion, long. node [deg]) and their rates per Julian century.
# Arcminute accuracy, which is plenty for matching a planet to a star-sized point.
_PLANET_ELEMENTS = {
    "Venus": ((0.72333566, 0.00677672, 3.39467605, 181.97909950, 131.60246718, 76.67984255),
              (0.00000390, -0.00004107, -0.00078890, 58517.81538729, 0.00268329, -0.27769418)),
    "Earth": ((1.00000261, 0.01671123, -0.00001531, 100.46457166, 102.93768193, 0.0),
              (0.00000562, -0.00004392, -0.01294668, 35999.37244981, 0.32327364, 0.0)),
    "Mars": ((1.52371034, 0.09339410, 1.84969142, -4.55343205, -23.94362959, 49.55953891),
             (0.00001847, 0.00007882, -0.00813131, 19140.30268499, 0.44441088, -0.29257343)),
    "Jupiter": ((5.20288700, 0.04838624, 1.30439695, 34.39644051, 14.72847983, 100.47390909),
                (-0.00011607, -0.00013253, -0.00183714, 3034.74612775, 0.21252668, 0.20469106)),
    "Saturn": ((9.53667594, 0.05386179, 2.48599187, 49.95424423, 92.59887831, 113.66242448),
               (-0.00125060, -0.00050991, 0.00193609, 1222.49362201, -0.41897216, -0.28867794)),
}
# Visual magnitude = H + 5 log10(r * delta) + c1*i + c2*i^2 + c3*i^3 (i = phase angle, deg), Astronomical
# Almanac; Saturn adds its ring term from the ring tilt (pole RA 40.59°, Dec 83.54°).
_PLANET_MAG = {"Venus": (-4.40, 0.0009, 0.000239, -0.00000065), "Mars": (-1.52, 0.016, 0.0, 0.0),
               "Jupiter": (-9.40, 0.005, 0.0, 0.0), "Saturn": (-8.88, 0.044, 0.0, 0.0)}
_SATURN_POLE = np.array([math.cos(math.radians(83.537)) * math.cos(math.radians(40.589)),
                         math.cos(math.radians(83.537)) * math.sin(math.radians(40.589)),
                         math.sin(math.radians(83.537))])


def _heliocentric_ecliptic(name: str, t: float) -> np.ndarray:
    el0, rate = _PLANET_ELEMENTS[name]
    a, e, inc, mean_long, peri, node = (x + r * t for x, r in zip(el0, rate))
    m = math.radians((mean_long - peri + 180.0) % 360.0 - 180.0)
    ecc = m + e * math.sin(m)
    for _ in range(6):  # Kepler's equation
        ecc -= (ecc - e * math.sin(ecc) - m) / (1.0 - e * math.cos(ecc))
    xp, yp = a * (math.cos(ecc) - e), a * math.sqrt(1.0 - e * e) * math.sin(ecc)
    w, o, i = math.radians(peri - node), math.radians(node), math.radians(inc)
    cw, sw, co, so, ci, si = math.cos(w), math.sin(w), math.cos(o), math.sin(o), math.cos(i), math.sin(i)
    return np.array([(cw * co - sw * so * ci) * xp + (-sw * co - cw * so * ci) * yp,
                     (cw * so + sw * co * ci) * xp + (-sw * so + cw * co * ci) * yp,
                     sw * si * xp + cw * si * yp])


def planet_positions(when: datetime) -> List[Dict[str, Any]]:
    """Venus, Mars, Jupiter, Saturn as catalog entries (J2000 RA/Dec, approximate visual magnitude)."""
    t = (when.timestamp() / 86400.0 + 2440587.5 - 2451545.0) / 36525.0
    earth = _heliocentric_ecliptic("Earth", t)
    eps = math.radians(23.43928)
    out = []
    for name, (h_mag, c1, c2, c3) in _PLANET_MAG.items():
        helio = _heliocentric_ecliptic(name, t)
        geo = helio - earth
        r, delta = float(np.linalg.norm(helio)), float(np.linalg.norm(geo))
        geo = geo - (delta / 173.1446) * (_heliocentric_ecliptic(name, t - delta / 173.1446 / 36525.0) - helio)  # light time
        x, y, z = geo
        y, z = y * math.cos(eps) - z * math.sin(eps), y * math.sin(eps) + z * math.cos(eps)
        phase = math.degrees(math.acos(np.clip(helio @ (helio - earth) / (r * delta), -1.0, 1.0)))
        mag = h_mag + 5.0 * math.log10(r * delta) + c1 * phase + c2 * phase ** 2 + c3 * phase ** 3
        if name == "Saturn":
            sin_b = abs(float(np.array([x, y, z]) @ _SATURN_POLE)) / delta
            mag += -2.60 * sin_b + 1.25 * sin_b * sin_b
        out.append({"name": name, "ra": math.degrees(math.atan2(y, x)) % 360.0,
                    "dec": math.degrees(math.asin(z / math.sqrt(x * x + y * y + z * z))),
                    "mag": round(mag, 2), "planet": True})
    return out


def bright_objects(when: datetime, max_mag: float, deep: bool = False) -> List[Dict[str, Any]]:
    """Catalog stars and naked-eye planets no fainter than max_mag (deep: include the mag 4.5 catalog)."""
    objs = [s for s in (load_deep_catalog() if deep else load_bright_star_catalog()) if s["mag"] <= max_mag]
    return objs + [p for p in planet_positions(when) if p["mag"] <= max_mag]


def _enu(alt_deg: np.ndarray, az_deg: np.ndarray) -> np.ndarray:
    alt, az = np.radians(alt_deg), np.radians(az_deg)
    return np.column_stack([np.cos(alt) * np.sin(az), np.cos(alt) * np.cos(az), np.sin(alt)])


def estimate_sky_mask(frame_bgr: np.ndarray) -> np.ndarray:
    """1 where the background is smooth and unsaturated (open sky), 0 on buildings, trees and lit horizon."""
    g = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY) if frame_bgr.ndim == 3 else frame_bgr
    h, w = g.shape
    k = max(15, (min(h, w) // 40) | 1)
    bg = cv2.medianBlur(g, k).astype(np.float32)  # removes stars and other point sources
    mu = cv2.blur(bg, (k, k))
    std = np.sqrt(np.maximum(cv2.blur(bg * bg, (k, k)) - mu * mu, 0))
    m = ((std < 6.0) & (bg < 235)).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((k, k), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m)
    keep = np.zeros_like(m)
    for i in range(1, n):
        if stats[i, 4] > 0.03 * h * w:
            keep[lab == i] = 1
    return cv2.erode(keep, np.ones((k, k), np.uint8))


def polygon_mask(shape: Tuple[int, int], sky: Optional[Sequence] = None,
                 obstruction: Optional[Sequence] = None) -> Optional[np.ndarray]:
    """User sky mask from polygons in frame fractions ([[x, y], ...], 0..1): 1 inside any sky polygon
    (the whole frame when none is given), 0 inside any obstruction polygon. None when no polygon is set."""
    h, w = shape[:2]

    def to_px(poly):
        pts = np.array(poly, dtype=np.float64).reshape(-1, 2)
        return np.round(pts * [w - 1, h - 1]).astype(np.int32) if len(pts) >= 3 else None

    sky_px = [p for p in (to_px(q) for q in (sky or [])) if p is not None]
    obs_px = [p for p in (to_px(q) for q in (obstruction or [])) if p is not None]
    if not sky_px and not obs_px:
        return None
    m = np.zeros((h, w), np.uint8) if sky_px else np.ones((h, w), np.uint8)
    if sky_px:
        cv2.fillPoly(m, sky_px, 1)
    if obs_px:
        cv2.fillPoly(m, obs_px, 0)
    return m


def combined_sky_mask(frame_bgr: np.ndarray, user_mask: Optional[Dict[str, Any]] = None,
                      use_auto: bool = True) -> Optional[np.ndarray]:
    """Automatic smooth-sky mask intersected with the user's sky/obstruction polygons.

    The automatic mask can keep a smooth, evenly lit building facade as sky; the user polygons fix that.
    Returns None (search the whole frame) when neither yields a usable sky region."""
    user = polygon_mask(frame_bgr.shape, (user_mask or {}).get("sky"), (user_mask or {}).get("obstruction"))
    auto = estimate_sky_mask(frame_bgr) if use_auto else None
    if auto is not None and auto.mean() < 0.05:
        auto = None  # no clear sky region found
    if auto is not None and user is not None:
        both = auto & user
        return both if both.mean() >= 0.01 else user
    return user if user is not None else auto


def detect_bright_points(frame_bgr: np.ndarray, max_points: int = 40, border_px: int = 6,
                         sky_mask: Optional[np.ndarray] = None, sigma: float = 5.0) -> np.ndarray:
    """Compact bright points (x, y, flux) sorted by flux, rejecting extended blobs (buildings, clouds)."""
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY) if frame_bgr.ndim == 3 else frame_bgr
    g = gray.astype(np.float32)
    bg = cv2.medianBlur(gray.astype(np.uint8), 31).astype(np.float32)
    diff = cv2.GaussianBlur(g - bg, (3, 3), 0)
    # Floor at 0.5 DN: on flat or clipped 8-bit frames the MAD collapses to 0 and every pixel would count
    noise = max(1.4826 * float(np.median(np.abs(diff - np.median(diff)))), 0.5)
    mask = (diff > sigma * noise).astype(np.uint8)
    n, lab, stats, cent = cv2.connectedComponentsWithStats(mask)
    h, w = g.shape
    pts = []
    for i in range(1, n):
        x0, y0, bw, bh, area = stats[i]
        if area < 3 or area > 80 or max(bw, bh) > 3 * max(1, min(bw, bh)) + 2:
            continue
        cx, cy = cent[i]
        if cx < border_px or cy < border_px or cx > w - border_px or cy > h - border_px:
            continue
        if sky_mask is not None and not sky_mask[int(cy), int(cx)]:
            continue
        flux = float(diff[lab == i].sum())
        pts.append((cx, cy, flux))
    pts.sort(key=lambda p: -p[2])
    return np.array(pts[:max_points], dtype=np.float64).reshape(-1, 3)


PROJECTIONS = ("rectilinear", "equidistant")


def _pix_to_cam(xy: np.ndarray, f: float, cx: float, cy: float, proj: str = "rectilinear",
                k: float = 0.0) -> np.ndarray:
    dx, dy = (xy[:, 0] - cx) / f, (xy[:, 1] - cy) / f
    if proj == "equidistant":  # fisheye: radius = f * (theta + k * theta^3)
        rn = np.hypot(dx, dy)
        theta = rn.copy()
        for _ in range(8):  # Newton: theta + k theta^3 = rn
            theta -= (theta + k * theta ** 3 - rn) / (1.0 + 3.0 * k * theta ** 2)
        phi = np.arctan2(dy, dx)
        return np.column_stack([np.sin(theta) * np.cos(phi), np.sin(theta) * np.sin(phi), np.cos(theta)])
    v = np.column_stack([dx, dy, np.ones(len(xy))])
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def _fov_to_f(fov_deg: float, half_size_px: float, proj: str) -> float:
    if proj == "equidistant":
        return half_size_px / math.radians(fov_deg / 2.0)
    return half_size_px / math.tan(math.radians(fov_deg / 2.0))


def _f_to_fov(f: float, half_size_px: float, proj: str) -> float:
    if proj == "equidistant":
        return math.degrees(2.0 * half_size_px / f)
    return math.degrees(2.0 * math.atan(half_size_px / f))


def _triad(a1, a2, b1, b2) -> np.ndarray:
    """Rotation R with R @ a ≈ b for two vector pairs."""
    def frame(u, v):
        t1 = u
        t2 = np.cross(u, v)
        t2 /= np.linalg.norm(t2) + 1e-12
        return np.column_stack([t1, t2, np.cross(t1, t2)])
    return frame(b1, b2) @ frame(a1, a2).T


def _kabsch(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Best rotation R with R @ a_i ≈ b_i."""
    h = a.T @ b
    u, _, vt = np.linalg.svd(h)
    d = np.sign(np.linalg.det(vt.T @ u.T))
    return vt.T @ np.diag([1, 1, d]) @ u.T


def _project(R: np.ndarray, enu: np.ndarray, f: float, cx: float, cy: float,
             proj: str = "rectilinear", k: float = 0.0) -> Tuple[np.ndarray, np.ndarray]:
    cam = enu @ R  # R.T @ enu_i for each row
    xy = np.full((len(enu), 2), np.nan)
    if proj == "equidistant":
        theta = np.arccos(np.clip(cam[:, 2], -1, 1))
        ok = theta < math.radians(110)
        rho = np.hypot(cam[:, 0], cam[:, 1]) + 1e-12
        r = f * (theta + k * theta ** 3)
        xy[ok, 0] = cx + r[ok] * cam[ok, 0] / rho[ok]
        xy[ok, 1] = cy + r[ok] * cam[ok, 1] / rho[ok]
        return xy, ok
    ok = cam[:, 2] > 0.05
    xy[ok, 0] = f * cam[ok, 0] / cam[ok, 2] + cx
    xy[ok, 1] = f * cam[ok, 1] / cam[ok, 2] + cy
    return xy, ok


def _match(xy_pred: np.ndarray, det_xy: np.ndarray, tol_px: float) -> List[Tuple[int, int, float]]:
    """Greedy one-to-one nearest matches (catalog index, detection index, distance)."""
    valid = np.where(~np.isnan(xy_pred[:, 0]))[0]
    if len(valid) == 0 or len(det_xy) == 0:
        return []
    d = np.linalg.norm(xy_pred[valid, None, :] - det_xy[None, :, :], axis=2)
    pairs = []
    used_c, used_d = set(), set()
    for flat in np.argsort(d, axis=None):
        ci, di = np.unravel_index(flat, d.shape)
        if d[ci, di] > tol_px:
            break
        if ci in used_c or di in used_d:
            continue
        used_c.add(ci)
        used_d.add(di)
        pairs.append((int(valid[ci]), int(di), float(d[ci, di])))
    return pairs


def _refine_fisheye_model(xy: np.ndarray, enu: np.ndarray, f0: float, cx0: float, cy0: float,
                          max_shift_px: float) -> Tuple[float, float, float, float, np.ndarray, float]:
    """Fit optical centre, focal length and distortion k on fixed star pairs (coarse-to-fine grid + Kabsch)."""
    def rms_for(cx, cy, f, k):
        cam = _pix_to_cam(xy, f, cx, cy, "equidistant", k)
        R = _kabsch(cam, enu)
        p, _ = _project(R, enu, f, cx, cy, "equidistant", k)
        return float(np.sqrt(np.mean(np.sum((p - xy) ** 2, axis=1)))), R

    best = (rms_for(cx0, cy0, f0, 0.0)[0], cx0, cy0, f0, 0.0)
    span_c, span_f, span_k = max_shift_px, 0.15 * f0, 0.12
    for _ in range(4):
        _, bcx, bcy, bf, bk = best
        for dx in np.linspace(-span_c, span_c, 7):
            for dy in np.linspace(-span_c, span_c, 7):
                for f in np.linspace(bf - span_f, bf + span_f, 7):
                    for k in np.linspace(bk - span_k, bk + span_k, 7):
                        e, _ = rms_for(bcx + dx, bcy + dy, f, k)
                        if e < best[0]:
                            best = (e, bcx + dx, bcy + dy, f, k)
        span_c, span_f, span_k = span_c / 3.0, span_f / 3.0, span_k / 3.0

    # Levenberg-Marquardt polish: the grid lands near the optimum, but centre, focal length and
    # distortion are coupled and a coarse grid can stop a few pixels off
    def resid(p):
        cam = _pix_to_cam(xy, p[2], p[0], p[1], "equidistant", p[3])
        R = _kabsch(cam, enu)
        pr, _ = _project(R, enu, p[2], p[0], p[1], "equidistant", p[3])
        return np.nan_to_num((pr - xy).ravel(), nan=1e3)

    p = np.array(best[1:], dtype=np.float64)
    r = resid(p)
    lam = 1e-3
    steps = np.array([0.5, 0.5, 0.5, 1e-3])
    for _ in range(30):
        J = np.column_stack([(resid(p + np.eye(4)[i] * steps[i]) - r) / steps[i] for i in range(4)])
        A, g = J.T @ J, J.T @ r
        trial = p - np.linalg.solve(A + lam * np.diag(np.diag(A) + 1e-9), g)
        if abs(trial[0] - cx0) > max_shift_px or abs(trial[1] - cy0) > max_shift_px:
            break
        r_trial = resid(trial)
        if r_trial @ r_trial < r @ r:
            done = r @ r - r_trial @ r_trial < 1e-6 * (r @ r)
            p, r, lam = trial, r_trial, lam / 3.0
            if done:
                break
        else:
            lam *= 4.0
            if lam > 1e6:
                break
    e_lm = float(np.sqrt(np.mean(np.sum(r.reshape(-1, 2) ** 2, axis=1))))
    if e_lm < best[0]:
        best = (e_lm, *p)
    e, cx, cy, f, k = best
    _, R = rms_for(cx, cy, f, k)
    return cx, cy, f, k, R, e


SIDEREAL_RATE_RAD_S = 7.2921159e-5


def track_sidereal_points(frames: Sequence[np.ndarray], times: Sequence[float], focal_px: float,
                          sky_mask: Optional[np.ndarray] = None, sigma: float = 3.0, max_points: int = 600,
                          min_drift_px: float = 1.8, link_px: float = 3.0) -> Dict[str, Any]:
    """Keep only point sources that persist across a frame series AND drift with the sky.

    Under a city sky most single-frame "stars" are hot pixels (0 px drift) or noise (does not persist).
    A real star moves in a straight line at up to focal_px * 7.29e-5 rad/s. Returns points at the series'
    mean time: "points" (N, 3) x, y, flux sorted by flux, "velocity" (N, 2) px/s, "time" (unix s),
    "span_s", and counts for diagnostics. focal_px only sets the plausible speed range, so a rough value works.
    """
    n = len(frames)
    t = np.asarray(times, dtype=np.float64)
    t_ref = float(t.mean())
    span = float(t.max() - t.min()) if n > 1 else 0.0
    v_max = 2.5 * focal_px * SIDEREAL_RATE_RAD_S  # rectilinear lenses stretch the image edges
    dets = [detect_bright_points(f, max_points=max_points, sky_mask=sky_mask, sigma=sigma) for f in frames]
    tracks: List[List[Tuple[int, float, float, float]]] = []
    for fi, d in enumerate(dets):
        taken = set()
        for tr in tracks:
            fl, xl, yl, _ = tr[-1]
            if fl == fi or len(d) == 0:
                continue
            dt = t[fi] - t[fl]
            if len(tr) >= 2:  # predict along the track's motion so far
                f0, x0, y0, _ = tr[0]
                vx, vy = (xl - x0) / (t[fl] - t[f0]), (yl - y0) / (t[fl] - t[f0])
            else:
                vx = vy = 0.0
            px, py = xl + vx * dt, yl + vy * dt
            r = link_px + (0.0 if len(tr) >= 2 else v_max * dt)
            dd = np.hypot(d[:, 0] - px, d[:, 1] - py)
            j = int(np.argmin(dd))
            if dd[j] <= r and j not in taken:
                taken.add(j)
                tr.append((fi, float(d[j, 0]), float(d[j, 1]), float(d[j, 2])))
        for j in range(len(d)):
            if j not in taken:
                tracks.append([(fi, float(d[j, 0]), float(d[j, 1]), float(d[j, 2]))])

    need = max(3, int(math.ceil(0.66 * n)))
    pts, vel = [], []
    n_static = n_persistent = 0
    for tr in tracks:
        if len(tr) < need:
            continue
        n_persistent += 1
        a = np.array(tr)
        tt = t[a[:, 0].astype(int)] - t_ref
        A = np.column_stack([np.ones_like(tt), tt])
        cx, *_ = np.linalg.lstsq(A, a[:, 1], rcond=None)
        cy, *_ = np.linalg.lstsq(A, a[:, 2], rcond=None)
        resid = np.sqrt(np.mean((A @ cx - a[:, 1]) ** 2 + (A @ cy - a[:, 2]) ** 2))
        drift = math.hypot(cx[1], cy[1]) * (tt.max() - tt.min())
        if drift < min_drift_px:
            n_static += 1
            continue
        if math.hypot(cx[1], cy[1]) > v_max or resid > 1.5:
            continue  # aircraft, satellites, flicker
        pts.append((cx[0], cy[0], float(np.median(a[:, 3]))))
        vel.append((cx[1], cy[1]))
    order = np.argsort([-p[2] for p in pts])
    return {
        "points": np.array([pts[i] for i in order], dtype=np.float64).reshape(-1, 3),
        "velocity": np.array([vel[i] for i in order], dtype=np.float64).reshape(-1, 2),
        "time": t_ref,
        "span_s": span,
        "detections_per_frame": [int(len(d)) for d in dets],
        "persistent": n_persistent,
        "static": n_static,
    }


def _pointing_result(R: np.ndarray, f: float, cx: float, cy: float, k_dist: float, proj: str,
                     w: int, h: int, latitude_deg: float, longitude_deg: float, when: datetime) -> Dict[str, Any]:
    """Camera axis az/alt/roll, FOV and axis RA/Dec for rotation R (camera -> ENU)."""
    axis = R @ np.array([0.0, 0.0, 1.0])        # optical axis in ENU
    up_img = R @ np.array([0.0, -1.0, 0.0])      # image "up" direction in ENU
    alt = math.degrees(math.asin(np.clip(axis[2], -1, 1)))
    az = math.degrees(math.atan2(axis[0], axis[1])) % 360.0
    # Roll: angle between image-up and the local vertical projected on the image plane
    zen = np.array([0.0, 0.0, 1.0])
    vert = zen - axis * (zen @ axis)
    roll = 0.0
    if np.linalg.norm(vert) > 1e-6:
        vert /= np.linalg.norm(vert)
        roll = math.degrees(math.atan2(np.cross(vert, up_img) @ axis, vert @ up_img))
    # Optical axis in equatorial coordinates
    lat = math.radians(latitude_deg)
    alt_r, az_r = math.radians(alt), math.radians(az)
    dec_c = math.asin(math.sin(lat) * math.sin(alt_r) + math.cos(lat) * math.cos(alt_r) * math.cos(az_r))
    ha_c = math.atan2(-math.sin(az_r) * math.cos(alt_r),
                      math.cos(lat) * math.sin(alt_r) - math.sin(lat) * math.cos(alt_r) * math.cos(az_r))
    jd = when.timestamp() / 86400.0 + 2440587.5
    lst = (280.46061837 + 360.98564736629 * (jd - 2451545.0) + longitude_deg) % 360.0
    ra_c = (lst - math.degrees(ha_c)) % 360.0
    if proj == "equidistant":
        edge = _pix_to_cam(np.array([[w, cy], [cx, h]], dtype=np.float64), f, cx, cy, proj, k_dist)
        hfov = 2.0 * math.degrees(math.acos(np.clip(edge[0, 2], -1, 1)))
        vfov = 2.0 * math.degrees(math.acos(np.clip(edge[1, 2], -1, 1)))
    else:
        hfov = _f_to_fov(f, w / 2.0, proj)
        vfov = _f_to_fov(f, h / 2.0, proj)
    return {
        "pointing_az_deg": round(az, 2),
        "pointing_alt_deg": round(alt, 2),
        "roll_deg": round(roll, 2),
        "fov_horizontal_deg": round(hfov, 2),
        "fov_vertical_deg": round(vfov, 2),
        "center_ra_deg": round(ra_c, 4),
        "center_dec_deg": round(math.degrees(dec_c), 4),
        "focal_px": round(float(f), 1),
        "projection": proj,
        "optical_center_px": [round(float(cx), 1), round(float(cy), 1)],
        "distortion_k": round(float(k_dist), 4),
    }


def _rotation_angle_deg(Ra: np.ndarray, Rb: np.ndarray) -> float:
    return math.degrees(math.acos(np.clip((np.trace(Ra.T @ Rb) - 1.0) / 2.0, -1.0, 1.0)))


def solve_fixed_lens(
    points: np.ndarray,
    image_size: Tuple[int, int],
    latitude_deg: float,
    longitude_deg: float,
    when: Optional[datetime],
    focal_px: float,
    center_px: Tuple[float, float],
    distortion_k: float = 0.0,
    projection: str = "equidistant",
    velocity: Optional[np.ndarray] = None,
    sky_mask: Optional[np.ndarray] = None,
    max_mag: float = 3.5,
    narrow_max_mag: float = 4.5,
    min_alt_deg: float = 2.0,
    n_seed_det: int = 16,
    n_seed_cat: int = 28,
    n_seed_cat_local: int = 12,
    tile_radius_deg: Optional[float] = None,
    side_tol_deg: float = 0.25,
    max_scale_err: float = 0.03,
    tol_px: float = 5.0,
    min_matches: int = 3,
    max_rms_px: float = 2.5,
    min_completeness: float = 0.5,
) -> Dict[str, Any]:
    """Pointing for a lens whose intrinsics (focal length, optical centre, distortion) are already known.

    Only the rotation is unknown, so 3 stars fix it (the focal length may still float by max_scale_err, since
    focus and temperature shift it by ~1%). Triangles of the brightest detections are matched to
    catalog triangles (stars + planets) by their angular sides, handedness and brightness order; each
    candidate rotation is scored against all detections. A solution is accepted only if it fits tightly,
    predicts no bright star on open sky that is missing, moves the matched stars the way they were seen
    drifting (when velocity is given), and no different rotation explains the data as well.
    points: (N, 3) x, y, flux in image pixels; velocity: (N, 2) px/s at `when`.
    """
    proj = projection if projection in PROJECTIONS else "equidistant"
    when = when or datetime.now().astimezone()
    w, h = image_size
    cx, cy = center_px
    f, kd = float(focal_px), float(distortion_k)
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    if len(pts) < 3:
        return {"success": False, "error": f"only {len(pts)} star-like points to match"}
    cam = _pix_to_cam(pts[:, :2], f, cx, cy, proj, kd)

    corner = _pix_to_cam(np.array([[0.0, 0.0], [w, 0.0], [0.0, h], [w, h]]), f, cx, cy, proj, kd)
    half_diag = math.degrees(math.acos(float(np.clip(corner[:, 2].min(), -1, 1))))
    narrow = half_diag < 60.0
    objs = bright_objects(when, max(max_mag, narrow_max_mag) if narrow else max_mag, deep=narrow)
    altaz = stars_alt_az(objs, latitude_deg, longitude_deg, when)
    up = altaz[:, 0] > min_alt_deg
    objs = [o for o, u in zip(objs, up) if u]
    if len(objs) < 3:
        return {"success": False, "error": "too few bright stars/planets above the horizon"}
    enu = _enu(altaz[up, 0], altaz[up, 1])
    mags = np.array([o["mag"] for o in objs])
    seeds = np.argsort(mags)[:n_seed_cat]
    sep_tol = math.radians(side_tol_deg)

    def triangles(vecs, idx):
        """All ordered vertex triples of idx: sides opposite each vertex (rad) and handedness."""
        out_v, out_s, out_h = [], [], []
        for a, b, c in itertools.permutations(idx, 3):
            va, vb, vc = vecs[a], vecs[b], vecs[c]
            out_v.append((a, b, c))
            out_s.append((math.acos(np.clip(vb @ vc, -1, 1)), math.acos(np.clip(va @ vc, -1, 1)),
                          math.acos(np.clip(va @ vb, -1, 1))))
            out_h.append(np.sign(np.cross(va, vb) @ vc))
        return np.array(out_v, dtype=int).reshape(-1, 3), np.array(out_s).reshape(-1, 3), np.array(out_h)

    if narrow:
        # Narrow lens: the brightest stars of the whole sky are rarely in the frame together, so take the
        # brightest few inside each frame-sized patch of sky as seeds instead
        tri_sets = []
        gold = math.pi * (3.0 - math.sqrt(5.0))
        edge = _pix_to_cam(np.array([[cx, 0.0], [cx, h], [0.0, cy], [w, cy]]), f, cx, cy, proj, kd)
        half_short = math.degrees(math.acos(float(np.clip(edge[:, 2].max(), -1, 1))))
        tile_r = tile_radius_deg or 1.2 * half_short  # about the frame's short side: seeds that fit together
        n_tiles = int(np.clip(2.0 * (180.0 / max(tile_r, 5.0)) ** 2, 12, 600))
        cos_r = math.cos(math.radians(tile_r))
        seen = set()
        for t in range(n_tiles):  # Fibonacci points on the upper hemisphere
            z = 1.0 - (t + 0.5) / n_tiles
            rr = math.sqrt(1.0 - z * z)
            c = np.array([rr * math.cos(gold * t), rr * math.sin(gold * t), z])
            near = np.where(enu @ c > cos_r)[0]
            if len(near) < 3:
                continue
            local = tuple(sorted(near[np.argsort(mags[near])][:n_seed_cat_local]))
            if local in seen:
                continue
            seen.add(local)
            tri_sets.append(triangles(enu, list(local)))
        cat_v = np.concatenate([tv for tv, _, _ in tri_sets]) if tri_sets else np.zeros((0, 3), int)
        cat_s = np.concatenate([ts for _, ts, _ in tri_sets]) if tri_sets else np.zeros((0, 3))
        cat_h = np.concatenate([th for _, _, th in tri_sets]) if tri_sets else np.zeros(0)
        if len(cat_v):
            cat_v, first = np.unique(cat_v, axis=0, return_index=True)
            cat_s, cat_h = cat_s[first], cat_h[first]
    else:
        cat_v, cat_s, cat_h = triangles(enu, seeds)
    keep = cat_s.min(axis=1) > math.radians(2.0) if len(cat_v) else np.zeros(0, bool)
    cat_v, cat_s, cat_h = cat_v[keep], cat_s[keep], cat_h[keep]
    det_idx = list(range(min(n_seed_det, len(pts))))
    det_v, det_s, det_h = [], [], []
    for tri in itertools.combinations(det_idx, 3):
        v, sd, hd = triangles(cam, tri)
        det_v.append(v[0]), det_s.append(sd[0]), det_h.append(hd[0])
    det_v, det_s, det_h = np.array(det_v), np.array(det_s), np.array(det_h)

    def predicted_velocity(R, fs, idx):
        """Image velocity (px/s) of catalog objects idx under rotation R, from their sky motion."""
        later = datetime.fromtimestamp(when.timestamp() + 60.0).astimezone()
        aa = stars_alt_az([objs[i] for i in idx], latitude_deg, longitude_deg, later)
        p0, _ = _project(R, enu[idx], fs, cx, cy, proj, kd)
        p1, _ = _project(R, _enu(aa[:, 0], aa[:, 1]), fs, cx, cy, proj, kd)
        return (p1 - p0) / 60.0

    def project_inside(R, fs):
        xy, _ = _project(R, enu, fs, cx, cy, proj, kd)
        inside = np.isfinite(xy[:, 0]) & (xy[:, 0] >= 0) & (xy[:, 0] < w) & (xy[:, 1] >= 0) & (xy[:, 1] < h)
        return np.where(inside[:, None], xy, np.nan)

    def fit(ci, di):
        """Best focal length (within max_scale_err) and rotation for fixed star pairs."""
        best_fit = None
        for fs in f * np.linspace(1.0 - max_scale_err, 1.0 + max_scale_err, 25):
            R = _kabsch(_pix_to_cam(pts[di, :2], fs, cx, cy, proj, kd), enu[ci])
            p, _ = _project(R, enu[ci], fs, cx, cy, proj, kd)
            err = float(np.nanmean(np.sum((p - pts[di, :2]) ** 2, axis=1)))
            if best_fit is None or err < best_fit[0]:
                best_fit = (err, fs, R)
        return best_fit[1], best_fit[2]

    def evaluate(R, fs):
        matches = _match(project_inside(R, fs), pts[:, :2], tol_px)
        if len(matches) < min_matches:
            return None
        fs, R = fit([c for c, _, _ in matches], [d for _, d, _ in matches])
        matches = _match(project_inside(R, fs), pts[:, :2], tol_px)
        if len(matches) < min_matches:
            return None
        ci = [c for c, _, _ in matches]
        di = [d for _, d, _ in matches]
        fs, R = fit(ci, di)
        xy = project_inside(R, fs)
        matches = [(c, d, float(np.linalg.norm(xy[c] - pts[d, :2]))) for c, d in zip(ci, di)]
        rms = float(np.sqrt(np.mean([e * e for _, _, e in matches])))
        # Bright objects the rotation puts on open sky must be seen
        # (up to the 60th percentile of the matched magnitudes: one chance match of a faint star must not
        # make every faint catalog star "expected", detection is incomplete near the limit)
        mm = sorted(mags[c] for c, _, _ in matches)
        faintest = mm[max(0, math.ceil(0.6 * len(mm)) - 1)] if len(mm) > 4 else mm[-1]
        exp = [i for i in range(len(objs)) if mags[i] <= faintest and np.isfinite(xy[i, 0])
               and 8 <= xy[i, 0] < w - 8 and 8 <= xy[i, 1] < h - 8
               and (sky_mask is None or sky_mask[int(xy[i, 1]), int(xy[i, 0])])]
        matched = {c for c, _, _ in matches}
        completeness = len(matched & set(exp)) / max(1, len(exp))
        missing = len(set(exp) - matched)
        return {"R": R, "f": fs, "matches": matches, "rms": rms, "completeness": completeness,
                "expected": len(exp), "score": len(matches) - 0.7 * missing - 0.1 * rms}

    candidates: List[Dict[str, Any]] = []
    tried: List[np.ndarray] = []
    for (i, j, k), sd, hd in zip(det_v, det_s, det_h):
        scale = sd.sum() / cat_s.sum(axis=1)  # one focal-length scale for all three sides
        ok = ((np.abs(scale - 1.0) <= max_scale_err) & (cat_h == hd)
              & (np.abs(cat_s * scale[:, None] - sd).max(axis=1) < sep_tol))
        for (a, b, c), sc in zip(cat_v[ok], scale[ok]):
            # Brightness order: a clearly brighter catalog object (>= 1 mag) must not be the fainter point
            trio = ((i, a), (j, b), (k, c))
            if any(mags[ca] + 1.0 <= mags[cb] and pts[da, 2] < pts[db, 2]
                   for da, ca in trio for db, cb in trio if da != db):
                continue
            fs = f * sc
            R = _kabsch(_pix_to_cam(pts[[i, j, k], :2], fs, cx, cy, proj, kd), enu[[a, b, c]])
            if any(_rotation_angle_deg(R, Rt) < 0.3 for Rt in tried):
                continue
            tried.append(R)
            res = evaluate(R, fs)
            if res is not None:
                candidates.append(res)
    if not candidates:
        return {"success": False, "error": "no star triangle matched the catalog with the known lens",
                "detections": int(len(pts))}

    # Keep candidates that pass every check, then demand that the best is unambiguous
    good = []
    for c in candidates:
        c["reason"] = None
        if c["rms"] > max_rms_px:
            c["reason"] = f"fit too loose (RMS {c['rms']:.1f} px)"
        elif c["completeness"] < min_completeness:
            c["reason"] = (f"predicts {c['expected']} bright stars on open sky but only "
                           f"{len(c['matches'])} are seen")
        elif velocity is not None and len(velocity) == len(pts):
            ci = [m[0] for m in c["matches"]]
            di = [m[1] for m in c["matches"]]
            v_pred = predicted_velocity(c["R"], c["f"], ci)
            v_meas = np.asarray(velocity)[di]
            err = np.linalg.norm(v_pred - v_meas, axis=1)
            if np.any(err > np.maximum(0.35 * np.linalg.norm(v_pred, axis=1), 0.01)):
                c["reason"] = "matched stars do not drift the way the sky moves"
            c["drift_err_px_s"] = round(float(err.max()), 4)
        elif len(c["matches"]) < min_matches + 1:
            c["reason"] = "3 stars without drift data are not enough to be sure"
        if c["reason"] is None:
            good.append(c)
    if not good:
        best = max(candidates, key=lambda c: c["score"])
        return {"success": False, "error": f"best star triangle rejected: {best['reason']} "
                f"({len(best['matches'])} matches)", "detections": int(len(pts))}
    good.sort(key=lambda c: -c["score"])
    best = good[0]
    for other in good[1:]:
        if _rotation_angle_deg(best["R"], other["R"]) > 1.0 and other["score"] > best["score"] - 1.0:
            return {"success": False, "error": "ambiguous: two different pointings fit the stars equally well",
                    "detections": int(len(pts))}

    out = {"success": True, "mode": "fixed_lens"}
    out.update(_pointing_result(best["R"], best["f"], cx, cy, kd, proj, w, h, latitude_deg, longitude_deg, when))
    out["focal_scale"] = round(best["f"] / f, 4)
    matched = [{"name": objs[c]["name"], "mag": float(objs[c]["mag"]), "x": round(float(pts[d, 0]), 1),
                "y": round(float(pts[d, 1]), 1), "err_px": round(e, 1)} for c, d, e in best["matches"]]
    matched.sort(key=lambda m: m["mag"])
    out.update({
        "rms_px": round(best["rms"], 2),
        "completeness": round(best["completeness"], 2),
        "matched_stars": matched,
        "detections": int(len(pts)),
        "solved_at": when.isoformat(),
    })
    if "drift_err_px_s" in best:
        out["drift_err_px_s"] = best["drift_err_px_s"]
    return out


def solve_bright_stars(
    frame_bgr: np.ndarray,
    latitude_deg: float,
    longitude_deg: float,
    when: Optional[datetime] = None,
    hfov_candidates_deg: Sequence[float] = tuple(range(50, 125, 4)),
    max_mag: float = 3.0,
    min_alt_deg: float = 3.0,
    n_detections: int = 14,
    n_seed_stars: int = 30,
    sep_tol_deg: float = 0.8,
    min_matches: int = 5,
    max_residual_px: float = 4.0,
    max_rms_px: float = 2.5,
    projection: str = "rectilinear",
    use_sky_mask: bool = True,
    min_completeness: float = 0.6,
    detections: Optional[np.ndarray] = None,
    sky_mask: Optional[np.ndarray] = None,
    deep: bool = False,
) -> Dict[str, Any]:
    """Find camera pointing from bright stars. Returns success False with a reason when unsure.

    projection: "rectilinear" (normal lens) or "equidistant" (fisheye; FOV candidates may exceed 180°).
    detections: (N, 3) x, y, flux already found (e.g. by track_sidereal_points) instead of detecting here;
    sky_mask: open-sky mask to use instead of estimate_sky_mask.
    """
    proj = projection if projection in PROJECTIONS else "rectilinear"
    when = when or datetime.now().astimezone()
    h, w = frame_bgr.shape[:2]
    cx, cy = w / 2.0, h / 2.0

    if sky_mask is not None:
        mask = sky_mask
    else:
        mask = estimate_sky_mask(frame_bgr) if use_sky_mask else None
        if mask is not None and mask.mean() < 0.05:
            mask = None  # no clear sky region found: fall back to the whole frame
    if detections is not None:
        det = np.asarray(detections, dtype=np.float64).reshape(-1, 3)[:40]
    else:
        det = detect_bright_points(frame_bgr, max_points=40, sky_mask=mask)
        if mask is not None and len(det) < 15:
            # Faint, light-polluted sky: dig deeper, but only inside the sky region
            det = detect_bright_points(frame_bgr, max_points=40, sky_mask=mask, sigma=3.5)
    if len(det) < 4:
        return {"success": False, "error": f"only {len(det)} star-like points detected"}

    stars = bright_objects(when, max_mag, deep=deep)
    altaz = stars_alt_az(stars, latitude_deg, longitude_deg, when)
    up = altaz[:, 0] > min_alt_deg
    stars = [s for s, u in zip(stars, up) if u]
    altaz = altaz[up]
    if len(stars) < 4:
        return {"success": False, "error": "too few bright catalog stars above the horizon"}
    enu = _enu(altaz[:, 0], altaz[:, 1])
    order = np.argsort([s["mag"] for s in stars])
    seeds = order[:n_seed_stars]

    seed_det = det[:n_detections]
    # Catalog pair separations (degrees)
    cat_pairs = [(a, b) for i, a in enumerate(seeds) for b in seeds[i + 1:]]
    cat_sep = np.array([math.degrees(math.acos(np.clip(enu[a] @ enu[b], -1, 1))) for a, b in cat_pairs])

    best: Optional[Dict[str, Any]] = None
    for hfov in hfov_candidates_deg:
        f = _fov_to_f(hfov, w / 2.0, proj)
        cam = _pix_to_cam(seed_det[:, :2], f, cx, cy, proj)
        tol_px = max(6.0, f * math.radians(0.6))
        for i in range(len(seed_det)):
            for j in range(i + 1, len(seed_det)):
                sep = math.degrees(math.acos(np.clip(cam[i] @ cam[j], -1, 1)))
                if sep < 3.0:
                    continue
                for k in np.where(np.abs(cat_sep - sep) < sep_tol_deg)[0]:
                    a, b = cat_pairs[k]
                    for s1, s2 in ((a, b), (b, a)):
                        R = _triad(cam[i], cam[j], enu[s1], enu[s2])
                        xy, _ = _project(R, enu, f, cx, cy, proj)
                        matches = _match(xy, det[:, :2], tol_px)
                        if len(matches) < 3:
                            continue
                        score = len(matches) - 0.01 * sum(m[2] for m in matches) / tol_px
                        if best is None or score > best["score"]:
                            best = {"score": score, "R": R, "f": f, "matches": matches, "tol_px": tol_px}

    if best is None or len(best["matches"]) < min_matches:
        n = 0 if best is None else len(best["matches"])
        return {"success": False, "error": f"no consistent bright-star pattern (best {n} matches, need {min_matches})",
                "detections": len(det)}

    # Refine rotation, focal length (and fisheye distortion k) on all matches
    k_values = np.round(np.arange(-0.12, 0.1201, 0.01), 3) if proj == "equidistant" else [0.0]
    refined = None
    for k in k_values:
        R, f, matches = best["R"], best["f"], best["matches"]
        err_best = float("inf")
        for _ in range(3):
            f_best, R_best, m_best = f, R, matches
            for f_try in f * np.linspace(0.9, 1.1, 21):
                xy, _ = _project(R, enu, f_try, cx, cy, proj, k)
                m = _match(xy, det[:, :2], best["tol_px"])
                if len(m) < len(best["matches"]):
                    continue
                ci = [c for c, _, _ in m]
                di = [d for _, d, _ in m]
                R_try = _kabsch(_pix_to_cam(det[di, :2], f_try, cx, cy, proj, k), enu[ci])
                xy2, _ = _project(R_try, enu, f_try, cx, cy, proj, k)
                err = float(np.sqrt(np.nanmean(np.sum((xy2[ci] - det[di, :2]) ** 2, axis=1))))
                if err < err_best:
                    f_best, err_best, R_best, m_best = f_try, err, R_try, m
            R, f, matches = R_best, f_best, m_best
        # Prefer more matches, then the tighter fit
        key = (len(matches), -err_best)
        if refined is None or key > refined[0]:
            refined = (key, R, f, matches, float(k))
    _, R, f, best["matches"], k_dist = refined

    for _ in range(3 if proj == "equidistant" else 0):
        if len(best["matches"]) < 7:
            break
        # Cheap fisheye lenses are rarely centred on the sensor: fit the optical centre too (needs >= 6 stars),
        # then pick up the stars the better model now reaches and fit again
        ci = [c for c, _, _ in best["matches"]]
        di = [d for _, d, _ in best["matches"]]
        cx2, cy2, f2, k2, R2, _ = _refine_fisheye_model(det[di, :2], enu[ci], f, cx, cy, 0.04 * w)
        xy, _ = _project(R2, enu, f2, cx2, cy2, proj, k2)
        rematch = _match(xy, det[:, :2], best["tol_px"])
        if len(rematch) < len(best["matches"]):
            break
        grew = len(rematch) > len(best["matches"])
        cx, cy, f, k_dist, R, best["matches"] = cx2, cy2, f2, k2, R2, rematch
        if not grew:
            break
    # Final residuals with the refined model
    xy_final, _ = _project(R, enu, f, cx, cy, proj, k_dist)
    final = []
    for c, d, _ in best["matches"]:
        final.append((c, d, float(np.linalg.norm(xy_final[c] - det[d, :2]))))
    # A genuine solution fits to ~1 px; chance alignments of noise/hot pixels sit near the match tolerance
    final = [m for m in final if m[2] <= max_residual_px]
    if len(final) < min_matches:
        return {"success": False, "error": f"pattern did not hold up after refinement ({len(final)} stars within "
                f"{max_residual_px} px, need {min_matches})", "detections": len(det)}
    best["matches"] = final
    rms = float(np.sqrt(np.mean([e * e for _, _, e in final])))
    if rms > max_rms_px:
        return {"success": False, "error": f"star fit too loose (RMS {rms:.1f} px > {max_rms_px} px)",
                "detections": len(det)}

    # Completeness: catalog stars at least as bright as the faintest match that land on open sky must
    # mostly be detected too. A chance fit predicts bright stars where the image shows nothing.
    faintest = max(stars[c]["mag"] for c, _, _ in final)
    matched_idx = {c for c, _, _ in final}
    expected = 0
    for idx in range(len(stars)):
        if stars[idx]["mag"] > faintest:
            continue
        x, y = xy_final[idx]
        if not (np.isfinite(x) and 8 <= x < w - 8 and 8 <= y < h - 8):
            continue
        if mask is not None and not mask[int(y), int(x)]:
            continue
        expected += 1
    completeness = len(matched_idx) / max(1, expected)
    if completeness < min_completeness:
        return {"success": False, "error": f"pattern predicts {expected} bright stars on open sky but only "
                f"{len(matched_idx)} are seen ({completeness:.0%})", "detections": len(det)}

    matched = [
        {"name": stars[c]["name"], "mag": stars[c]["mag"], "x": round(float(det[d, 0]), 1),
         "y": round(float(det[d, 1]), 1), "err_px": round(dist, 1)}
        for c, d, dist in best["matches"]
    ]
    matched.sort(key=lambda m: m["mag"])
    out = {"success": True, "mode": "free_lens"}
    out.update(_pointing_result(R, f, cx, cy, k_dist, proj, w, h, latitude_deg, longitude_deg, when))
    out.update({
        "rms_px": round(rms, 2),
        "completeness": round(completeness, 2),
        "matched_stars": matched,
        "detections": int(len(det)),
        "solved_at": when.isoformat(),
    })
    return out
