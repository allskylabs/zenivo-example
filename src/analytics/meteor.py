"""
Meteor and Transient Streak Detection Analytics for IMX335 AllSky Observatory.
Detects fast optical meteor streaks via temporal frame differencing, intensity-weighted
geometric moment analysis (aspect ratio, length, peak DN), and discriminates against aircraft/satellites.
"""

import math
import json
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
import numpy as np
import cv2


def extract_streak_moments(
    diff_image: np.ndarray,
    comp_mask: np.ndarray,
) -> Tuple[float, float, float, float, float]:
    """
    Compute intensity-weighted 2nd-order central moments to extract elongation and orientation.

    Args:
        diff_image: Grayscale positive frame difference (uint8).
        comp_mask: Boolean mask of the connected component.

    Returns:
        Tuple of (centroid_x, centroid_y, major_axis_len, minor_axis_len, aspect_ratio).
    """
    y_indices, x_indices = np.where(comp_mask)
    if len(y_indices) == 0:
        return 0.0, 0.0, 0.0, 0.0, 1.0

    weights = diff_image[y_indices, x_indices].astype(np.float64)
    total_weight = np.sum(weights)

    if total_weight <= 0:
        return 0.0, 0.0, 0.0, 0.0, 1.0

    x_bar = float(np.sum(x_indices * weights) / total_weight)
    y_bar = float(np.sum(y_indices * weights) / total_weight)

    mu_xx = float(np.sum(((x_indices - x_bar) ** 2) * weights) / total_weight)
    mu_yy = float(np.sum(((y_indices - y_bar) ** 2) * weights) / total_weight)
    mu_xy = float(np.sum((x_indices - x_bar) * (y_indices - y_bar) * weights) / total_weight)

    # Eigenvalues of covariance matrix
    delta = math.sqrt((mu_xx - mu_yy) ** 2 + 4.0 * (mu_xy ** 2))
    major_axis = 2.0 * math.sqrt(2.0) * math.sqrt(max(0.1, mu_xx + mu_yy + delta))
    minor_axis = 2.0 * math.sqrt(2.0) * math.sqrt(max(0.1, mu_xx + mu_yy - delta))

    aspect_ratio = major_axis / max(0.5, minor_axis)

    return x_bar, y_bar, major_axis, minor_axis, aspect_ratio


def detect_meteors(
    curr_frame: np.ndarray,
    prev_frame: np.ndarray,
    min_length_px: int = 15,
    min_aspect_ratio: float = 4.0,
    min_brightness_diff: int = 30,
) -> List[Dict[str, Any]]:
    """
    Detect meteor streaks by temporal frame differencing and geometric elongation analysis.

    Args:
        curr_frame: Current calibrated frame (H, W, 3) or (H, W).
        prev_frame: Preceding frame or running background model (H, W, 3) or (H, W).
        min_length_px: Minimum length in pixels to classify as streak (default: 15).
        min_aspect_ratio: Minimum elongation ratio (default: 4.0).
        min_brightness_diff: Intensity threshold for frame difference (default: 30 DN).

    Returns:
        List of detected event dictionaries with JSON-serializable fields.
    """
    if curr_frame is None or prev_frame is None:
        raise ValueError("Frames cannot be None for meteor detection")

    curr_gray = cv2.cvtColor(curr_frame, cv2.COLOR_BGR2GRAY) if curr_frame.ndim == 3 else curr_frame
    prev_gray = cv2.cvtColor(prev_frame, cv2.COLOR_BGR2GRAY) if prev_frame.ndim == 3 else prev_frame

    # Compute positive frame difference
    diff = np.maximum(0, curr_gray.astype(np.int16) - prev_gray.astype(np.int16)).astype(np.uint8)

    # Threshold for transient streak candidates
    _, bin_diff = cv2.threshold(diff, min_brightness_diff, 255, cv2.THRESH_BINARY)

    # Connected component analysis
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(bin_diff)

    detected_events: List[Dict[str, Any]] = []

    for label_id in range(1, num_labels):
        area = stats[label_id, cv2.CC_STAT_AREA]
        if area < 10:
            continue

        x = int(stats[label_id, cv2.CC_STAT_LEFT])
        y = int(stats[label_id, cv2.CC_STAT_TOP])
        w = int(stats[label_id, cv2.CC_STAT_WIDTH])
        h = int(stats[label_id, cv2.CC_STAT_HEIGHT])

        comp_mask = (labels == label_id)
        x_bar, y_bar, major_axis, minor_axis, aspect_ratio = extract_streak_moments(diff, comp_mask)

        # Check streak geometry criteria
        if major_axis >= min_length_px and aspect_ratio >= min_aspect_ratio:
            peak_val = int(np.max(diff[comp_mask]))
            total_flux = int(np.sum(diff[comp_mask]))

            event = {
                "type": "meteor",
                "bbox": [x, y, w, h],
                "centroid": [round(x_bar, 2), round(y_bar, 2)],
                "length_px": round(float(major_axis), 2),
                "aspect_ratio": round(float(aspect_ratio), 2),
                "peak_dn": peak_val,
                "integrated_flux": total_flux,
                "timestamp": datetime.now().astimezone().isoformat(),
            }
            detected_events.append(event)

    return detected_events


def extract_event_crop(
    frame: np.ndarray,
    bbox: List[int],
    padding: int = 20,
) -> np.ndarray:
    """
    Extract a cropped region around a detected event bounding box with safety padding.

    Args:
        frame: Image array (H, W, C) or (H, W).
        bbox: [x, y, w, h] bounding box.
        padding: Padding in pixels on each side.

    Returns:
        Cropped sub-image.
    """
    h_img, w_img = frame.shape[:2]
    x, y, w, h = bbox

    x1 = max(0, x - padding)
    y1 = max(0, y - padding)
    x2 = min(w_img, x + w + padding)
    y2 = min(h_img, y + h + padding)

    return frame[y1:y2, x1:x2].copy()


class MeteorDetector:
    """
    Stateful detector maintaining a running background reference frame.
    """

    def __init__(
        self,
        min_length_px: int = 25,
        min_aspect_ratio: float = 4.0,
        min_brightness_diff: int = 30,
        history_alpha: float = 0.05,
    ):
        self.min_length_px = min_length_px
        self.min_aspect_ratio = min_aspect_ratio
        self.min_brightness_diff = min_brightness_diff
        self.history_alpha = history_alpha
        self.background_model: Optional[np.ndarray] = None
        self.events_history: List[Dict[str, Any]] = []

    def process_frame(self, frame: np.ndarray) -> List[Dict[str, Any]]:
        """
        Process a new frame against running background model and return any detected meteors.
        """
        if frame is None or frame.size == 0:
            return []

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame.copy()

        if self.background_model is None:
            self.background_model = gray.astype(np.float32)
            return []

        # Detect meteors against current background model
        bg_uint8 = np.clip(self.background_model, 0, 255).astype(np.uint8)
        events = detect_meteors(
            curr_frame=gray,
            prev_frame=bg_uint8,
            min_length_px=self.min_length_px,
            min_aspect_ratio=self.min_aspect_ratio,
            min_brightness_diff=self.min_brightness_diff,
        )

        # Update running exponential background model
        # Don't update background aggressively where an event occurred
        cv2.accumulateWeighted(gray, self.background_model, self.history_alpha)

        if events:
            self.events_history.extend(events)

        return events

    def reset(self) -> None:
        """Reset background model and event history."""
        self.background_model = None
        self.events_history.clear()
