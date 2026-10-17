"""
Keogram Generation Pipeline for IMX335 AllSky Observatory.
Extracts a central vertical meridian slice (N-S celestial axis) from each frame over time
and builds diurnal / nocturnal spatio-temporal strips.
"""

import os
from datetime import datetime
from typing import List, Optional, Tuple, Union
import numpy as np
import cv2


def extract_meridian_slice(frame: np.ndarray, slice_width: int = 3) -> np.ndarray:
    """
    Extract the central vertical meridian slice from an allsky frame.

    Args:
        frame: Input image array (H, W, C) or (H, W).
        slice_width: Width of the vertical slice in pixels (default: 3).

    Returns:
        np.ndarray of shape (H, slice_width, C) and dtype uint8.
    """
    if frame is None or frame.size == 0:
        raise ValueError("Cannot extract slice from empty or None frame")

    # Ensure 3-channel BGR format
    if frame.ndim == 2:
        frame_bgr = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    elif frame.ndim == 3 and frame.shape[2] == 1:
        frame_bgr = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    else:
        frame_bgr = frame

    h, w, c = frame_bgr.shape
    if slice_width <= 0:
        raise ValueError(f"Slice width must be positive, got {slice_width}")

    cx = w // 2
    half_w = slice_width // 2
    x_start = max(0, cx - half_w)
    x_end = min(w, x_start + slice_width)

    # If rounding caused slice to be narrower than requested and room permits, adjust start
    if (x_end - x_start) < slice_width and x_start > 0:
        x_start = max(0, x_end - slice_width)

    slice_img = frame_bgr[:, x_start:x_end, :].copy()
    return slice_img.astype(np.uint8)


def generate_keogram(
    frames: List[np.ndarray],
    slice_width: int = 3,
) -> np.ndarray:
    """
    Generate a complete Keogram from a temporal list of allsky frames.

    Args:
        frames: List of image arrays.
        slice_width: Width of vertical slice extracted per frame (default: 3).

    Returns:
        Keogram composite image (H, N_frames * slice_width, 3) uint8.
    """
    if not frames:
        raise ValueError("Cannot generate keogram from empty frame sequence")

    slices = []
    for frame in frames:
        sl = extract_meridian_slice(frame, slice_width=slice_width)
        slices.append(sl)

    keogram = np.concatenate(slices, axis=1)
    return keogram.astype(np.uint8)


class KeogramGenerator:
    """
    Accumulator and renderer for real-time Keogram generation.
    Supports streaming frame ingestion, time-stamped ruler rendering, and file serialization.
    """

    def __init__(self, slice_width: int = 3, max_slices: Optional[int] = None):
        self.slice_width = int(slice_width)
        self.max_slices = max_slices
        self.slices: List[np.ndarray] = []
        self.timestamps: List[datetime] = []

    def add_frame(self, frame: np.ndarray, timestamp: Optional[datetime] = None) -> np.ndarray:
        """
        Extract central slice from frame and append to accumulator.

        Returns:
            Extracted slice array (H, slice_width, 3).
        """
        ts = timestamp or datetime.now().astimezone()
        sl = extract_meridian_slice(frame, slice_width=self.slice_width)
        self.slices.append(sl)
        self.timestamps.append(ts)

        if self.max_slices is not None and len(self.slices) > self.max_slices:
            self.slices.pop(0)
            self.timestamps.pop(0)

        return sl

    def get_keogram(self) -> np.ndarray:
        """Return the current keogram image."""
        if not self.slices:
            return np.zeros((0, 0, 3), dtype=np.uint8)
        return np.concatenate(self.slices, axis=1).astype(np.uint8)

    def render_with_timestamp_ruler(
        self,
        ruler_height: int = 36,
        tick_interval_slices: int = 20,
    ) -> np.ndarray:
        """
        Render keogram with a timestamp scale/ruler at the bottom.
        """
        keogram = self.get_keogram()
        if keogram.size == 0:
            return keogram

        h, w, c = keogram.shape
        ruler = np.zeros((ruler_height, w, 3), dtype=np.uint8)
        ruler[:] = (20, 20, 20)  # Dark gray background

        # Render top divider line
        cv2.line(ruler, (0, 0), (w, 0), (100, 100, 100), 1)

        # Draw ticks and time labels
        num_slices = len(self.slices)
        interval = max(1, tick_interval_slices)

        for i in range(0, num_slices, interval):
            x_pos = min(w - 1, i * self.slice_width)
            cv2.line(ruler, (x_pos, 0), (x_pos, 8), (200, 200, 200), 1)

            if i < len(self.timestamps):
                ts_str = self.timestamps[i].astimezone().strftime("%H:%M")
                cv2.putText(
                    ruler,
                    ts_str,
                    (max(2, x_pos - 15), ruler_height - 10),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.35,
                    (220, 220, 220),
                    1,
                    cv2.LINE_AA,
                )

        return np.vstack([keogram, ruler])

    def save(self, filepath: str, add_timestamps: bool = True) -> bool:
        """
        Save keogram to disk. Creates parent directories if needed.
        """
        if add_timestamps and self.timestamps:
            output = self.render_with_timestamp_ruler()
        else:
            output = self.get_keogram()

        if output.size == 0:
            return False

        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        return bool(cv2.imwrite(filepath, output))

    def reset(self) -> None:
        """Clear all accumulated slices."""
        self.slices.clear()
        self.timestamps.clear()

    def __len__(self) -> int:
        return len(self.slices)
