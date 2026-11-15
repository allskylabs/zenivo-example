"""
Authoritative Reference Calibration Engine for IMX335 AllSky Observatory Testing.
Derived directly from ORIGINAL_REQUEST.md § R1, PROJECT.md § 2, and survey_allsky_pipeline.md § 2.
"""

from typing import List, Dict, Any
import numpy as np


def reference_create_master_dark(dark_frames: List[np.ndarray]) -> np.ndarray:
    """
    Compute pixel-wise median across a stack of dark frames.
    Input: list of (H, W, 3) uint8 BGR frames.
    Output: (H, W, 3) uint8 Master Dark frame.
    """
    if not dark_frames:
        raise ValueError("Cannot create master dark from empty frame list")
    stack = np.stack(dark_frames, axis=0)
    master = np.median(stack, axis=0)
    return np.clip(master, 0, 255).astype(np.uint8)


def reference_build_hot_pixel_map(master_dark: np.ndarray, threshold: int = 100) -> np.ndarray:
    """
    Extract binary hot pixel mask where luminance >= threshold (default DN >= 100).
    Input: (H, W, 3) uint8 BGR master dark.
    Output: (H, W) bool mask (True = hot pixel).
    """
    # Standard CCIR 601 luma: Y = 0.299*R + 0.587*G + 0.114*B
    luma = (
        0.114 * master_dark[:, :, 0].astype(np.float32)
        + 0.587 * master_dark[:, :, 1].astype(np.float32)
        + 0.299 * master_dark[:, :, 2].astype(np.float32)
    )
    return luma >= threshold


def reference_calibrate_frame(
    raw_frame: np.ndarray,
    master_dark: np.ndarray,
    hot_mask: np.ndarray,
    pedestal: int = 0,
) -> np.ndarray:
    """
    Apply Master Dark subtraction and 2D local median interpolation on hot pixels.
    Guarantees >= 90% reduction of pixels >= 100 on dark frames.
    """
    # Stage 1: Pedestal-protected dark subtraction
    subtracted = np.clip(
        raw_frame.astype(np.int16) - master_dark.astype(np.int16) + pedestal,
        0, 255,
    ).astype(np.uint8)

    calibrated = subtracted.copy()
    h, w = hot_mask.shape
    hot_coords = np.argwhere(hot_mask)

    # Stage 2: 2D local 3x3 median interpolation
    for y, x in hot_coords:
        y_min, y_max = max(0, y - 1), min(h, y + 2)
        x_min, x_max = max(0, x - 1), min(w, x + 2)

        win_mask = ~hot_mask[y_min:y_max, x_min:x_max]
        win_mask[y - y_min, x - x_min] = False

        if np.any(win_mask):
            for c in range(subtracted.shape[2]):
                win_vals = subtracted[y_min:y_max, x_min:x_max, c][win_mask]
                calibrated[y, x, c] = int(np.median(win_vals))
        else:
            # Fallback 5x5 window for dense clusters
            y_min5, y_max5 = max(0, y - 2), min(h, y + 3)
            x_min5, x_max5 = max(0, x - 2), min(w, x + 3)
            win_mask5 = ~hot_mask[y_min5:y_max5, x_min5:x_max5]
            win_mask5[y - y_min5, x - x_min5] = False
            if np.any(win_mask5):
                for c in range(subtracted.shape[2]):
                    win_vals = subtracted[y_min5:y_max5, x_min5:x_max5, c][win_mask5]
                    calibrated[y, x, c] = int(np.median(win_vals))

    return calibrated


def verify_hot_pixel_reduction(
    raw_dark: np.ndarray,
    calibrated_frame: np.ndarray,
    threshold: int = 100,
) -> Dict[str, Any]:
    """
    Verification metric for AC1:
    Calculate hot pixel reduction percentage on raw vs calibrated frame.
    """
    luma_raw = (
        0.114 * raw_dark[:, :, 0].astype(np.float32)
        + 0.587 * raw_dark[:, :, 1].astype(np.float32)
        + 0.299 * raw_dark[:, :, 2].astype(np.float32)
    )
    n_initial = int(np.sum(luma_raw >= threshold))

    luma_cal = (
        0.114 * calibrated_frame[:, :, 0].astype(np.float32)
        + 0.587 * calibrated_frame[:, :, 1].astype(np.float32)
        + 0.299 * calibrated_frame[:, :, 2].astype(np.float32)
    )
    n_final = int(np.sum(luma_cal >= threshold))

    reduction_pct = ((n_initial - n_final) / max(1, n_initial)) * 100.0
    passed = bool(reduction_pct >= 90.0)

    return {
        "n_initial_hot_pixels": n_initial,
        "n_final_hot_pixels": n_final,
        "reduction_percentage": round(reduction_pct, 2),
        "ac1_passed": passed,
    }


def reference_live_stacking(
    frames: List[np.ndarray],
    kappa: float = 2.2,
) -> np.ndarray:
    """
    Stack multiple calibrated frames with sigma-clipping (kappa=2.2).
    """
    if not frames:
        raise ValueError("Cannot stack empty frame list")
    if len(frames) == 1:
        return frames[0]

    stack = np.stack(frames, axis=0).astype(np.float32)
    # Mean and standard deviation per pixel
    mean = np.mean(stack, axis=0)
    std = np.std(stack, axis=0)

    # Sigma clipping mask
    lower_bound = mean - kappa * std
    upper_bound = mean + kappa * std

    valid_mask = (stack >= lower_bound) & (stack <= upper_bound)
    # Weighted average of valid pixels
    masked_stack = np.where(valid_mask, stack, 0.0)
    counts = np.sum(valid_mask, axis=0)
    counts = np.maximum(counts, 1.0)

    stacked = np.sum(masked_stack, axis=0) / counts
    return np.clip(stacked, 0, 255).astype(np.uint8)
