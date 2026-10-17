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
"""

import math
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

_CATALOG_JS = os.path.join(os.path.dirname(__file__), "..", "web", "sky_catalog.js")
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


def stars_alt_az(stars: Sequence[Dict[str, Any]], lat_deg: float, lon_deg: float, when: datetime) -> np.ndarray:
    """Return array (N, 2) of [alt, az] degrees (az from north through east)."""
    jd = when.timestamp() / 86400.0 + 2440587.5
    d = jd - 2451545.0
    lst = math.radians((280.46061837 + 360.98564736629 * d + lon_deg) % 360.0)
    lat = math.radians(lat_deg)
    ra = np.radians([s["ra"] for s in stars])
    dec = np.radians([s["dec"] for s in stars])
    ha = lst - ra
    sin_alt = np.sin(lat) * np.sin(dec) + np.cos(lat) * np.cos(dec) * np.cos(ha)
    alt = np.arcsin(np.clip(sin_alt, -1, 1))
    az = np.arctan2(-np.cos(dec) * np.sin(ha), (np.sin(dec) - np.sin(lat) * sin_alt) / np.cos(lat))
    return np.column_stack([np.degrees(alt), np.degrees(az) % 360.0])


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


def detect_bright_points(frame_bgr: np.ndarray, max_points: int = 40, border_px: int = 6,
                         sky_mask: Optional[np.ndarray] = None, sigma: float = 5.0) -> np.ndarray:
    """Compact bright points (x, y, flux) sorted by flux, rejecting extended blobs (buildings, clouds)."""
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY) if frame_bgr.ndim == 3 else frame_bgr
    g = gray.astype(np.float32)
    bg = cv2.medianBlur(gray.astype(np.uint8), 31).astype(np.float32)
    diff = cv2.GaussianBlur(g - bg, (3, 3), 0)
    noise = 1.4826 * float(np.median(np.abs(diff - np.median(diff)))) + 1e-3
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
    e, cx, cy, f, k = best
    _, R = rms_for(cx, cy, f, k)
    return cx, cy, f, k, R, e


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
) -> Dict[str, Any]:
    """Find camera pointing from bright stars. Returns success False with a reason when unsure.

    projection: "rectilinear" (normal lens) or "equidistant" (fisheye; FOV candidates may exceed 180°).
    """
    proj = projection if projection in PROJECTIONS else "rectilinear"
    when = when or datetime.now(timezone.utc)
    h, w = frame_bgr.shape[:2]
    cx, cy = w / 2.0, h / 2.0

    mask = estimate_sky_mask(frame_bgr) if use_sky_mask else None
    if mask is not None and mask.mean() < 0.05:
        mask = None  # no clear sky region found: fall back to the whole frame
    det = detect_bright_points(frame_bgr, max_points=40, sky_mask=mask)
    if mask is not None and len(det) < 15:
        # Faint, light-polluted sky: dig deeper, but only inside the sky region
        det = detect_bright_points(frame_bgr, max_points=40, sky_mask=mask, sigma=3.5)
    if len(det) < 4:
        return {"success": False, "error": f"only {len(det)} star-like points detected"}

    stars = [s for s in load_bright_star_catalog() if s["mag"] <= max_mag]
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

    if proj == "equidistant" and len(best["matches"]) >= 7:
        # Cheap fisheye lenses are rarely centred on the sensor: fit the optical centre too (needs >= 6 stars)
        ci = [c for c, _, _ in best["matches"]]
        di = [d for _, d, _ in best["matches"]]
        cx, cy, f, k_dist, R, _ = _refine_fisheye_model(det[di, :2], enu[ci], f, cx, cy, 0.04 * w)
        xy, _ = _project(R, enu, f, cx, cy, proj, k_dist)
        rematch = _match(xy, det[:, :2], best["tol_px"])
        if len(rematch) >= len(best["matches"]):
            best["matches"] = rematch
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
    matched = [
        {"name": stars[c]["name"], "mag": stars[c]["mag"], "x": round(float(det[d, 0]), 1),
         "y": round(float(det[d, 1]), 1), "err_px": round(dist, 1)}
        for c, d, dist in best["matches"]
    ]
    matched.sort(key=lambda m: m["mag"])
    return {
        "success": True,
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
        "rms_px": round(rms, 2),
        "completeness": round(completeness, 2),
        "matched_stars": matched,
        "detections": int(len(det)),
        "solved_at": when.isoformat(),
    }
