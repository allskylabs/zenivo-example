"""
Authoritative Reference Astronomy Pipeline & Analytics Engine for IMX335 AllSky Testing.
Derived directly from ORIGINAL_REQUEST.md § R2, PROJECT.md § 3, and survey_allsky_pipeline.md § 3.
"""

import math
from typing import List, Dict, Any, Tuple, Optional
from datetime import datetime, timezone
import numpy as np
import cv2


def reference_generate_keogram(frames: List[np.ndarray], slice_width: int = 3) -> np.ndarray:
    """
    Generate a Keogram strip from a temporal sequence of allsky frames.
    Extracts a central vertical meridian slice (N-S axis) from each frame.
    Output: (H, N_frames * slice_width, 3) image.
    """
    if not frames:
        raise ValueError("Cannot generate keogram from empty frame sequence")

    slices = []
    for frame in frames:
        h, w, c = frame.shape
        cx = w // 2
        half_w = slice_width // 2
        x_start = max(0, cx - half_w)
        x_end = min(w, x_start + slice_width)
        sl = frame[:, x_start:x_end, :]
        slices.append(sl)

    keogram = np.concatenate(slices, axis=1)
    return keogram


def reference_generate_startrails(
    frames: List[np.ndarray],
    comet_mode: bool = False,
    decay_alpha: float = 0.98,
) -> np.ndarray:
    """
    Composite startrails from a sequence of night frames.
    - Standard mode: maximum-pixel blending.
    - Comet mode: exponential temporal fading.
    """
    if not frames:
        raise ValueError("Cannot generate startrails from empty frame sequence")

    if not comet_mode:
        # Fast maximum-pixel blending across all frames
        stack = np.stack(frames, axis=0)
        startrails = np.max(stack, axis=0)
        return startrails.astype(np.uint8)
    else:
        # Comet mode with decaying tail
        composite = frames[0].astype(np.float32)
        for frame in frames[1:]:
            composite = np.maximum(composite * decay_alpha, frame.astype(np.float32))
        return np.clip(composite, 0, 255).astype(np.uint8)


def reference_star_count_and_cloud_cover(
    frame: np.ndarray,
    expected_clear_stars: int = 150,
) -> Dict[str, Any]:
    """
    Estimate star count and cloud cover percentage from a calibrated night frame.
    Uses high-pass background subtraction + connected component analysis.
    """
    # Convert to grayscale
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape

    # 1. Background estimation via median filter
    bg = cv2.medianBlur(gray, 31)
    # High-pass star image
    stars_img = np.maximum(0, gray.astype(np.int16) - bg.astype(np.int16)).astype(np.uint8)

    # 2. Noise thresholding
    # Standard astronomical detection threshold: > 20 DN above local background
    thresh_val = max(20, int(np.mean(stars_img) + 3.0 * np.std(stars_img)))
    _, bin_stars = cv2.threshold(stars_img, thresh_val, 255, cv2.THRESH_BINARY)

    # Connected components
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(bin_stars)

    # Filter point-sources by area (2..40 pixels, rejecting 1-pixel noise and large artifacts)
    valid_stars = 0
    for i in range(1, num_labels):
        area = stats[i, cv2.CC_STAT_AREA]
        if 2 <= area <= 40:
            valid_stars += 1

    # Cloud cover calculation
    # Star obscuration ratio
    star_obscuration = max(0.0, min(1.0, 1.0 - (valid_stars / max(1, expected_clear_stars))))

    # Diffuse background luma (computed inside the active sky area, excluding outer black corners)
    sky_pixels = bg[bg > 0]
    mean_bg_luma = float(np.mean(sky_pixels)) if len(sky_pixels) > 0 else 0.0

    # Urban cloud light pollution factor (clear sky ~ 10-12 DN, cloudy/overcast ~ 28-50 DN)
    luma_factor = max(0.0, min(1.0, (mean_bg_luma - 10.0) / 22.0))

    cloud_cover_pct = min(100.0, max(0.0, (0.60 * star_obscuration + 0.40 * luma_factor) * 100.0))

    return {
        "star_count": valid_stars,
        "cloud_cover_pct": round(cloud_cover_pct, 2),
        "mean_background_luma": round(mean_bg_luma, 2),
        "status": "clear" if cloud_cover_pct < 25.0 else ("scattered" if cloud_cover_pct < 70.0 else "overcast"),
    }


def reference_detect_meteors(
    curr_frame: np.ndarray,
    prev_frame: np.ndarray,
    min_length_px: int = 15,
    min_aspect_ratio: float = 4.0,
    min_brightness_diff: int = 30,
) -> List[Dict[str, Any]]:
    """
    Detect meteor streaks by frame differencing and geometric moment analysis.
    Classifies linear streaks with high elongation (AR >= 4.5) as meteor events.
    """
    # Grayscale conversion
    curr_gray = cv2.cvtColor(curr_frame, cv2.COLOR_BGR2GRAY)
    prev_gray = cv2.cvtColor(prev_frame, cv2.COLOR_BGR2GRAY)

    # Positive frame difference
    diff = np.maximum(0, curr_gray.astype(np.int16) - prev_gray.astype(np.int16)).astype(np.uint8)

    # Threshold for streak candidates
    _, bin_diff = cv2.threshold(diff, min_brightness_diff, 255, cv2.THRESH_BINARY)

    # Connected component labeling
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(bin_diff)

    detected_events = []
    for label_id in range(1, num_labels):
        area = stats[label_id, cv2.CC_STAT_AREA]
        if area < 10:
            continue

        x = stats[label_id, cv2.CC_STAT_LEFT]
        y = stats[label_id, cv2.CC_STAT_TOP]
        w = stats[label_id, cv2.CC_STAT_WIDTH]
        h = stats[label_id, cv2.CC_STAT_HEIGHT]

        comp_mask = (labels == label_id)
        # Compute central moments of intensity
        y_indices, x_indices = np.where(comp_mask)
        weights = diff[y_indices, x_indices].astype(np.float64)
        total_weight = np.sum(weights)
        if total_weight <= 0:
            continue

        x_bar = np.sum(x_indices * weights) / total_weight
        y_bar = np.sum(y_indices * weights) / total_weight

        mu_xx = np.sum(((x_indices - x_bar) ** 2) * weights) / total_weight
        mu_yy = np.sum(((y_indices - y_bar) ** 2) * weights) / total_weight
        mu_xy = np.sum((x_indices - x_bar) * (y_indices - y_bar) * weights) / total_weight

        # Eigenvalues of covariance matrix
        delta = math.sqrt((mu_xx - mu_yy) ** 2 + 4 * (mu_xy ** 2))
        major_axis = 2.0 * math.sqrt(2.0) * math.sqrt(max(0.1, mu_xx + mu_yy + delta))
        minor_axis = 2.0 * math.sqrt(2.0) * math.sqrt(max(0.1, mu_xx + mu_yy - delta))

        aspect_ratio = major_axis / max(0.5, minor_axis)
        length = major_axis

        if length >= min_length_px and aspect_ratio >= min_aspect_ratio:
            peak_val = int(np.max(diff[comp_mask]))
            detected_events.append({
                "type": "meteor",
                "bbox": [int(x), int(y), int(w), int(h)],
                "centroid": [round(x_bar, 2), round(y_bar, 2)],
                "length_px": round(length, 2),
                "aspect_ratio": round(aspect_ratio, 2),
                "peak_dn": peak_val,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

    return detected_events


def reference_fisheye_projection(
    altitude_deg: float,
    azimuth_deg: float,
    center_x: int = 1296,
    center_y: int = 972,
    focal_length_px: float = 618.8,
    heading_offset_deg: float = 0.0,
) -> Optional[Tuple[int, int]]:
    """
    Equidistant fisheye projection: r = f * (90 deg - Alt).
    Converts topocentric Alt/Az (degrees) to pixel coordinates (u, v).
    Returns None if object is below horizon (Alt < 0).
    """
    if altitude_deg < 0.0:
        return None  # Below horizon

    zenith_angle_rad = math.radians(90.0 - altitude_deg)
    r = focal_length_px * zenith_angle_rad

    theta = math.radians(azimuth_deg + heading_offset_deg)
    u = int(round(center_x + r * math.sin(theta)))
    v = int(round(center_y - r * math.cos(theta)))

    return u, v
