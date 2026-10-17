"""
Master Dark Frame Generation and Hot Pixel Calibration Engine.

Implements DarkCalibrationInterface from PROJECT.md:
- Pixel-wise median stacking of dark frames
- Extraction of binary hot/bad pixel maps (threshold DN >= 100)
- Calibration pipeline: master dark subtraction + 2D local median interpolation on hot pixels
- Acceptance Criteria AC1 verification (>= 90% reduction of hot pixels on long exposures)
"""

import abc
from typing import List, Dict, Any, Union, Optional
import numpy as np


class DarkCalibrationInterface(abc.ABC):
    """
    Standard Dark Calibration Interface contract from PROJECT.md.
    """

    @abc.abstractmethod
    def create_master_dark(self, dark_frames: List[np.ndarray]) -> np.ndarray:
        """Compute pixel-wise median dark frame from stack of dark frames."""
        ...

    @abc.abstractmethod
    def build_hot_pixel_map(self, master_dark: np.ndarray, threshold: int = 100) -> np.ndarray:
        """Extract 2D boolean mask indicating defective hot pixels (DN >= threshold)."""
        ...

    @abc.abstractmethod
    def calibrate_frame(
        self,
        raw_frame: np.ndarray,
        master_dark: np.ndarray,
        hot_pixel_mask: np.ndarray,
        pedestal: int = 0,
    ) -> np.ndarray:
        """
        Subtract master dark and apply 2D local median interpolation on hot pixels.
        Guarantees >= 90% reduction of pixels >= 100 on dark frames.
        """
        ...


class DarkCalibrationEngine(DarkCalibrationInterface):
    """
    Production-grade astronomical dark frame calibration engine.
    """

    def create_master_dark(self, dark_frames: List[np.ndarray]) -> np.ndarray:
        """
        Generate Master Dark Frame via pixel-wise median stacking.
        Median stacking rejects transient cosmic rays and outliers.

        Args:
            dark_frames: List of dark frames of identical dimensions (H, W) or (H, W, C).

        Returns:
            np.ndarray: Master dark frame with uint8 dtype.
        """
        if not dark_frames:
            raise ValueError("Cannot create master dark from empty frame list")

        # Validate consistent shapes
        base_shape = dark_frames[0].shape
        for i, frame in enumerate(dark_frames):
            if frame.shape != base_shape:
                raise ValueError(f"Frame {i} shape {frame.shape} does not match base shape {base_shape}")

        stacked = np.stack(dark_frames, axis=0)
        master = np.median(stacked, axis=0)
        return np.clip(np.round(master), 0, 255).astype(np.uint8)

    def build_hot_pixel_map(self, master_dark: np.ndarray, threshold: int = 100) -> np.ndarray:
        """
        Extract binary 2D boolean mask indicating hot/bad pixels where DN >= threshold.

        Args:
            master_dark: Master dark image (H, W) or (H, W, C).
            threshold: Digital number threshold (nominal 100 on 8-bit scale).

        Returns:
            np.ndarray: 2D boolean array of shape (H, W), True where pixel is defective.
        """
        if master_dark.ndim == 2:
            return master_dark >= threshold
        elif master_dark.ndim == 3:
            # For 3-channel (BGR/RGB), a pixel is hot if any channel or luma exceeds threshold
            luma = (
                0.114 * master_dark[:, :, 0].astype(np.float32)
                + 0.587 * master_dark[:, :, 1].astype(np.float32)
                + 0.299 * master_dark[:, :, 2].astype(np.float32)
            )
            any_channel_hot = np.any(master_dark >= threshold, axis=2)
            luma_hot = luma >= threshold
            return any_channel_hot | luma_hot
        else:
            raise ValueError(f"Unsupported array dimensions: {master_dark.ndim}")

    def calibrate_frame(
        self,
        raw_frame: np.ndarray,
        master_dark: np.ndarray,
        hot_pixel_mask: np.ndarray,
        pedestal: int = 0,
    ) -> np.ndarray:
        """
        Calibrate raw frame:
        1. Pedestal-protected dark frame subtraction:
           I_sub = clip(I_raw - M_dark + pedestal, 0, 255)
        2. 2D local neighborhood median interpolation on all pixels marked in hot_pixel_mask.
           Defective pixels are replaced with the median of non-defective neighbors.

        Args:
            raw_frame: Input raw captured frame (H, W) or (H, W, C), uint8.
            master_dark: Matched master dark frame, uint8.
            hot_pixel_mask: 2D boolean mask (H, W) of hot pixels.
            pedestal: Optional positive pedestal to avoid clipping background noise floor.

        Returns:
            np.ndarray: Calibrated frame with hot pixels removed and dark current subtracted.
        """
        if raw_frame.shape != master_dark.shape:
            raise ValueError(f"raw_frame shape {raw_frame.shape} != master_dark shape {master_dark.shape}")

        is_3d = raw_frame.ndim == 3
        h, w = raw_frame.shape[:2]

        # Stage 1: Pedestal-protected dark subtraction
        subtracted = np.clip(
            raw_frame.astype(np.int16) - master_dark.astype(np.int16) + pedestal,
            0,
            255,
        ).astype(np.uint8)

        # Stage 2: 2D Local Median Repair for hot pixels
        calibrated = subtracted.copy()
        hot_coords = np.argwhere(hot_pixel_mask)

        if len(hot_coords) == 0:
            return calibrated

        for y, x in hot_coords:
            # 3x3 local neighborhood
            y_min, y_max = max(0, y - 1), min(h, y + 2)
            x_min, x_max = max(0, x - 1), min(w, x + 2)

            win_mask = ~hot_pixel_mask[y_min:y_max, x_min:x_max]
            # Exclude center defective pixel
            win_mask[y - y_min, x - x_min] = False

            if np.any(win_mask):
                if is_3d:
                    for c in range(raw_frame.shape[2]):
                        vals = subtracted[y_min:y_max, x_min:x_max, c][win_mask]
                        calibrated[y, x, c] = int(np.median(vals))
                else:
                    vals = subtracted[y_min:y_max, x_min:x_max][win_mask]
                    calibrated[y, x] = int(np.median(vals))
            else:
                # 5x5 adaptive fallback for clustered defects
                y_min5, y_max5 = max(0, y - 2), min(h, y + 3)
                x_min5, x_max5 = max(0, x - 2), min(w, x + 3)
                win_mask5 = ~hot_pixel_mask[y_min5:y_max5, x_min5:x_max5]
                win_mask5[y - y_min5, x - x_min5] = False

                if np.any(win_mask5):
                    if is_3d:
                        for c in range(raw_frame.shape[2]):
                            vals = subtracted[y_min5:y_max5, x_min5:x_max5, c][win_mask5]
                            calibrated[y, x, c] = int(np.median(vals))
                    else:
                        vals = subtracted[y_min5:y_max5, x_min5:x_max5][win_mask5]
                        calibrated[y, x] = int(np.median(vals))

        return calibrated


def verify_ac1_reduction(
    raw_dark: np.ndarray,
    calibrated_frame: np.ndarray,
    threshold: int = 100,
) -> Dict[str, Any]:
    """
    Automated verification of Acceptance Criteria AC1:
    Verifies that Master Dark Subtraction + Hot Pixel Map achieves >= 90% reduction
    of hot pixels (DN >= threshold) on long exposures.

    Returns:
        dict: {
            "n_initial_hot_pixels": int,
            "n_final_hot_pixels": int,
            "reduction_percentage": float,
            "ac1_passed": bool
        }
    """
    if raw_dark.ndim == 3:
        # BGR luminance calculation
        luma_raw = (
            0.114 * raw_dark[:, :, 0].astype(np.float32)
            + 0.587 * raw_dark[:, :, 1].astype(np.float32)
            + 0.299 * raw_dark[:, :, 2].astype(np.float32)
        )
        luma_cal = (
            0.114 * calibrated_frame[:, :, 0].astype(np.float32)
            + 0.587 * calibrated_frame[:, :, 1].astype(np.float32)
            + 0.299 * calibrated_frame[:, :, 2].astype(np.float32)
        )
        # Also check channel-wise maxima for strict validation
        raw_mask = (luma_raw >= threshold) | np.any(raw_dark >= threshold, axis=2)
        cal_mask = (luma_cal >= threshold) | np.any(calibrated_frame >= threshold, axis=2)
    else:
        raw_mask = raw_dark >= threshold
        cal_mask = calibrated_frame >= threshold

    n_initial = int(np.sum(raw_mask))
    n_final = int(np.sum(cal_mask))

    if n_initial == 0:
        reduction_pct = 100.0
    else:
        reduction_pct = ((n_initial - n_final) / n_initial) * 100.0

    passed = reduction_pct >= 90.0

    return {
        "n_initial_hot_pixels": n_initial,
        "n_final_hot_pixels": n_final,
        "reduction_percentage": round(reduction_pct, 2),
        "ac1_passed": passed,
    }
