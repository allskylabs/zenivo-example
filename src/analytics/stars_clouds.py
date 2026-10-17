"""
Cloud Cover Estimation and Star Counting Analytics for IMX335 AllSky Observatory.
Implements point-source blob detection for star extraction and background sky luminance
modeling for real-time cloud obscuration percentage analysis.
"""

from typing import Dict, List, Optional, Tuple, Any
import numpy as np
import cv2


def detect_stars_blob(
    gray_frame: np.ndarray,
    min_area: int = 2,
    max_area: int = 40,
    median_blur_ksize: int = 31,
) -> Tuple[int, np.ndarray, List[Dict[str, Any]]]:
    """
    Extract point-source astronomical stars using background subtraction and connected components.

    Args:
        gray_frame: Grayscale image (H, W) uint8.
        min_area: Minimum connected component area in pixels (rejects single-pixel noise).
        max_area: Maximum area in pixels (rejects large clouds/blobs).
        median_blur_ksize: Kernel size for diffuse background estimation.

    Returns:
        Tuple of (star_count, binary_star_mask, list_of_star_dicts).
    """
    # 1. Background estimation: the sky background is smooth, so a median at 1/4 resolution (kernel scaled to
    #    match) is equivalent and ~16x cheaper than a full-resolution median with a 31 px kernel
    ksize = median_blur_ksize if median_blur_ksize % 2 == 1 else median_blur_ksize + 1
    h_img, w_img = gray_frame.shape[:2]
    factor = 4 if min(h_img, w_img) >= 4 * ksize else (2 if h_img * w_img > 1_000_000 else 1)
    if factor > 1:
        small = cv2.resize(gray_frame, (w_img // factor, h_img // factor), interpolation=cv2.INTER_AREA)
        bg_small = cv2.medianBlur(small, max(3, (ksize // factor) | 1))
        bg = cv2.resize(bg_small, (w_img, h_img), interpolation=cv2.INTER_LINEAR)
    else:
        bg = cv2.medianBlur(gray_frame, ksize)

    # 2. High-pass starlight isolation
    stars_img = cv2.subtract(gray_frame, bg)  # saturating uint8 difference, clipped at 0

    # 3. Dynamic astronomical detection threshold
    mean_val, std_val = cv2.meanStdDev(stars_img)
    thresh_val = max(20, int(float(mean_val[0][0]) + 3.0 * float(std_val[0][0])))

    _, bin_stars = cv2.threshold(stars_img, thresh_val, 255, cv2.THRESH_BINARY)

    # 4. Connected components; per-star peak brightness computed for all labels at once
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(bin_stars)
    areas = stats[1:, cv2.CC_STAT_AREA]
    keep = np.nonzero((areas >= min_area) & (areas <= max_area))[0] + 1
    valid_stars = int(len(keep))
    stars_list = []
    if valid_stars:
        ys, xs = np.nonzero(bin_stars)
        lab = labels[ys, xs]
        peaks = np.zeros(num_labels, dtype=np.int32)
        np.maximum.at(peaks, lab, stars_img[ys, xs])
        for i in keep.tolist():
            x, y, w, h = (int(v) for v in stats[i, :4])
            x_c, y_c = centroids[i]
            stars_list.append({
                "x": round(float(x_c), 1),
                "y": round(float(y_c), 1),
                "bbox": [x, y, w, h],
                "area": int(stats[i, cv2.CC_STAT_AREA]),
                "peak_intensity": int(peaks[i]),
            })

    return valid_stars, bin_stars, stars_list


def estimate_stars_and_clouds(
    frame: np.ndarray,
    expected_clear_stars: int = 150,
) -> Dict[str, Any]:
    """
    Estimate detected star count and cloud cover percentage from an allsky frame.

    Args:
        frame: Input image (H, W, 3) BGR or (H, W) grayscale.
        expected_clear_stars: Baseline expected star count for clear sky.

    Returns:
        Dictionary containing:
        - star_count: int
        - cloud_cover_pct: float (0.0 .. 100.0)
        - mean_background_luma: float
        - status: "clear" | "scattered" | "overcast"
    """
    if frame is None or frame.size == 0:
        raise ValueError("Cannot estimate stars and clouds on empty frame")

    # Grayscale conversion
    if frame.ndim == 3:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    else:
        gray = frame

    # 1. Star extraction
    valid_stars, _, stars_list = detect_stars_blob(gray)

    # 2. Background diffuse luma analysis (subsample 4x for speed when frame is large)
    sub_gray = gray[::4, ::4] if gray.size > 500_000 else gray
    bg = cv2.medianBlur(sub_gray, 7 if gray.size > 500_000 else 31)
    sky_pixels = bg[bg > 0]
    mean_bg_luma = float(np.mean(sky_pixels)) if len(sky_pixels) > 0 else 0.0

    # 3. Cloud cover calculation
    # Star obscuration component
    star_obscuration = max(0.0, min(1.0, 1.0 - (valid_stars / max(1, expected_clear_stars))))

    # Urban light pollution factor (clear ~ 10-12 DN, overcast ~ 28-50 DN)
    luma_factor = max(0.0, min(1.0, (mean_bg_luma - 10.0) / 22.0))

    # Composite cloud cover percentage
    cloud_cover_pct = min(100.0, max(0.0, (0.60 * star_obscuration + 0.40 * luma_factor) * 100.0))

    status = "clear" if cloud_cover_pct < 25.0 else ("scattered" if cloud_cover_pct < 70.0 else "overcast")

    return {
        "star_count": valid_stars,
        "cloud_cover_pct": round(cloud_cover_pct, 2),
        "cloud_cover_percent": round(cloud_cover_pct, 2),  # Alias for interface contract
        "mean_background_luma": round(mean_bg_luma, 2),
        "status": status,
        "stars": stars_list,
    }


def detect_stars_and_clouds(frame: np.ndarray) -> Dict[str, Any]:
    """
    Standard interface contract alias for AstroPipelineInterface.
    """
    return estimate_stars_and_clouds(frame)


class StarAndCloudAnalyzer:
    """
    High-level analyzer for celestial clarity and star tracking.
    """

    def __init__(self, default_expected_stars: int = 150):
        self.default_expected_stars = default_expected_stars

    def analyze(self, frame: np.ndarray, expected_stars: Optional[int] = None) -> Dict[str, Any]:
        exp = expected_stars or self.default_expected_stars
        return estimate_stars_and_clouds(frame, expected_clear_stars=exp)
