"""
Star boost: software sum-binning for the night display frame.

Averaging NxN pixels (or downscaling the 5 MP frame to a 960 px live view) spreads a 1-2 px star
over its neighbours and it disappears into the sky. Sum-binning adds the NxN signal instead, so a
star gets ~N^2 times brighter while the noise only grows ~N times. Here the sum is taken over the
signal *above the local sky background*, in linear light, with a sliding NxN window: the frame keeps
its size (overlays and plate-solve coordinates stay valid), the sky brightness is unchanged (auto
exposure is not disturbed), and only pixels whose binned signal clearly exceeds the binned noise
are lifted, so the background does not get grainier.
"""

from typing import Optional

import cv2
import numpy as np

STAR_BOOST_BINS = (2, 3, 4)

# Display gamma of the frame: the RTS5869 RAW10 path applies v ** 0.62, ISP / libcamera output is ~sRGB
GAMMA_RAW10 = 0.62
GAMMA_SRGB = 1.0 / 2.2

# Local noise floor = this x the measured local mean |signal| (raise to lift fewer cloud specks, lower for more stars)
CLOUD_NOISE_SCALE = 1.5

_LUT_CACHE: dict = {}


def _to_linear_lut(gamma: float) -> np.ndarray:
    lut = _LUT_CACHE.get(gamma)
    if lut is None:
        lut = _LUT_CACHE[gamma] = ((np.arange(256, dtype=np.float32) / 255.0) ** (1.0 / gamma)).astype(np.float32)
    return lut


def sky_polygon_mask(shape, user_mask: Optional[dict]) -> Optional[np.ndarray]:
    """uint8 mask from the station sky mask (polygons in frame fractions): 1 inside any "sky" polygon
    (whole frame when none), 0 inside any "obstruction" polygon. None when no polygon is set."""
    h, w = shape[:2]
    polys = {}
    for key in ("sky", "obstruction"):
        polys[key] = [
            np.round(np.asarray(p, dtype=np.float64).reshape(-1, 2) * [w - 1, h - 1]).astype(np.int32)
            for p in (user_mask or {}).get(key) or [] if len(p) >= 3
        ]
    if not polys["sky"] and not polys["obstruction"]:
        return None
    m = np.zeros((h, w), np.uint8) if polys["sky"] else np.ones((h, w), np.uint8)
    if polys["sky"]:
        cv2.fillPoly(m, polys["sky"], 1)
    if polys["obstruction"]:
        cv2.fillPoly(m, polys["obstruction"], 0)
    return m


def star_bin_boost(
    frame_bgr: np.ndarray,
    bin_factor: int = 3,
    threshold_sigma: float = 2.5,
    sky_mask: Optional[np.ndarray] = None,
    gamma: float = GAMMA_RAW10,
) -> np.ndarray:
    """Return a copy of an 8-bit BGR frame with stars sum-binned over an NxN window.

    bin_factor: NxN window (2..4). threshold_sigma: binned signal must exceed this many binned-noise
    sigmas before it is added. sky_mask: optional uint8/bool mask (same HxW) limiting the boost to the sky.
    gamma: display gamma of the frame (GAMMA_RAW10 or GAMMA_SRGB), used to work in linear light.
    """
    n = int(bin_factor)
    if frame_bgr is None or frame_bgr.ndim != 3 or frame_bgr.dtype != np.uint8 or n < 2:
        return frame_bgr
    h, w = frame_bgr.shape[:2]
    lut = _to_linear_lut(float(gamma))

    # Luma in linear light (single channel: the colour planes are only touched where a star is lifted)
    y = cv2.LUT(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY), lut)

    # Sky background: median of a 16x-reduced copy (stars vanish there), smoothed back to full size
    small = cv2.resize(y, (max(8, w // 16), max(8, h // 16)), interpolation=cv2.INTER_AREA)
    bg = cv2.resize(cv2.blur(cv2.medianBlur(small, 5), (3, 3)), (w, h), interpolation=cv2.INTER_LINEAR)
    d = y - bg

    # Binned signal: sum of the background-subtracted signal over the NxN window
    s = cv2.boxFilter(d, -1, (n, n), normalize=False, borderType=cv2.BORDER_REFLECT)

    # Per-pixel noise from a robust MAD over a sparse sample; summing n^2 pixels scales it by n
    sample = d[::7, ::7]
    sigma = 1.4826 * float(np.median(np.abs(sample - np.median(sample))))
    # Local noise floor: shot noise and cloud texture grow with brightness, so under cloud a global sigma
    # (dominated by the quiet clear sky) is far too low and every bright cloud speck would be lifted as a
    # "star" (white grain). Local mean |d| on the 16x-reduced grid, never below the global sigma.
    small_d = cv2.resize(np.abs(d), (small.shape[1], small.shape[0]), interpolation=cv2.INTER_AREA)
    sigma_map = 1.2533 * CLOUD_NOISE_SCALE * cv2.resize(cv2.blur(small_d, (5, 5)), (w, h), interpolation=cv2.INTER_LINEAR)
    thr = threshold_sigma * n * np.maximum(sigma_map, max(sigma, 1e-4))

    # Soft-threshold: only the part of the binned signal above the noise floor lifts the pixel
    lift = s - thr
    lift -= np.maximum(d, 0.0)
    if sky_mask is not None and sky_mask.shape[:2] == (h, w):
        lift[sky_mask == 0] = 0.0
    ys, xs = np.nonzero(lift > 0.0)
    out = frame_bgr.copy()
    if ys.size:
        px = lut[frame_bgr[ys, xs]] + lift[ys, xs][:, None]
        np.clip(px, 0.0, 1.0, out=px)
        out[ys, xs] = (np.power(px, gamma) * 255.0 + 0.5).astype(np.uint8)
    return out
