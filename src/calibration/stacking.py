"""
Fast Astronomical Live Stacking Engine with Sigma-Clipping (kappa = 2.2).

Performs real-time accumulation and statistical outlier rejection on consecutive
calibrated frames to enhance the Signal-to-Noise Ratio (SNR) of astronomical images
without satellite/aircraft streak contamination.
"""

from collections import deque
from typing import List, Optional, Deque
import numpy as np


class LiveStackingEngine:
    """
    Real-time multi-frame stacking engine supporting:
    - Sigma-clipped average stacking (nominal kappa = 2.2)
    - Additive accumulation stacking
    - Rolling window buffer
    """

    def __init__(self, max_frames: int = 10, kappa: float = 2.2):
        self.max_frames = max(1, max_frames)
        self.kappa = float(kappa)
        self.buffer: Deque[np.ndarray] = deque(maxlen=self.max_frames)
        self.accumulated_buffer: Optional[np.ndarray] = None

    def reset(self):
        """Clear all frames in the stack."""
        self.buffer.clear()
        self.accumulated_buffer = None

    def add_frame(self, frame: np.ndarray) -> np.ndarray:
        """
        Add a new calibrated frame to the stacking buffer and return the updated stacked image.
        """
        # Store as float32 for high numerical precision
        self.buffer.append(frame.astype(np.float32))
        return self.compute_stack()

    @property
    def frame_count(self) -> int:
        """Return the current number of frames in the buffer."""
        return len(self.buffer)

    def compute_stack(self) -> np.ndarray:
        """
        Compute the stacked image from all frames currently in the buffer using sigma-clipping.
        """
        if not self.buffer:
            raise ValueError("Cannot compute stack on empty buffer")

        if len(self.buffer) == 1:
            return np.clip(np.round(self.buffer[0]), 0, 255).astype(np.uint8)

        # Stack into shape (N, H, W, C) or (N, H, W)
        stack = np.stack(list(self.buffer), axis=0)
        n = stack.shape[0]

        if n < 3:
            # For 2 frames, sigma clipping is not statistically valid; use simple mean
            mean_frame = np.mean(stack, axis=0)
            return np.clip(np.round(mean_frame), 0, 255).astype(np.uint8)

        # 1. Compute pixel sample median and standard deviation along stack axis 0
        med = np.median(stack, axis=0)
        sigma = np.std(stack, axis=0, ddof=1)

        # 2. Sigma-clipping mask: True for valid inlier pixels, False for outliers
        # Outlier condition: |frame - median| > kappa * sigma
        threshold = self.kappa * sigma
        diff = np.abs(stack - med)
        inlier_mask = diff <= threshold

        # In case all frames at a pixel get clipped (e.g. extreme noise), ensure median survives
        no_survivor = np.sum(inlier_mask, axis=0) == 0
        if np.any(no_survivor):
            inlier_mask[:, no_survivor] = True

        # 3. Average of surviving inliers
        weights = inlier_mask.astype(np.float32)
        weight_sum = np.sum(weights, axis=0)
        weight_sum = np.maximum(weight_sum, 1.0)

        stacked_float = np.sum(stack * weights, axis=0) / weight_sum
        return np.clip(np.round(stacked_float), 0, 255).astype(np.uint8)

    def compute_additive_stack(self) -> np.ndarray:
        """
        Compute additive accumulation of all frames in the buffer, clipped to 255.
        Useful for synthesizing long-exposure startrails or nebula signal.
        """
        if not self.buffer:
            raise ValueError("Buffer is empty")

        accum = np.zeros_like(self.buffer[0], dtype=np.float32)
        for f in self.buffer:
            accum += f

        return np.clip(np.round(accum), 0, 255).astype(np.uint8)


def stack_frames_sigma_clipped(
    frames: List[np.ndarray],
    kappa: float = 2.2,
) -> np.ndarray:
    """
    Stateless functional interface for sigma-clipped frame stacking.
    """
    engine = LiveStackingEngine(max_frames=len(frames), kappa=kappa)
    for f in frames:
        engine.buffer.append(f.astype(np.float32))
    return engine.compute_stack()
