"""
Startrails Generation Pipeline for IMX335 AllSky Observatory.
Synthesizes full-night stellar motion into continuous arcs via maximum-pixel blending,
with support for comet-tail decay and horizon masking.
"""

import os
from typing import List, Optional, Union
import numpy as np
import cv2


def generate_startrails(
    frames: List[np.ndarray],
    comet_mode: bool = False,
    decay_alpha: float = 0.98,
    decay_type: str = "exponential",
    mask: Optional[np.ndarray] = None,
) -> np.ndarray:
    """
    Composite startrails from a sequence of night sky frames.

    Args:
        frames: List of consecutive night sky frames (H, W, C) or (H, W).
        comet_mode: If True, applies temporal fading to produce comet-tail trails.
        decay_alpha: Decay factor for comet mode (nominal: 0.96 .. 0.99 for exponential).
        decay_type: "exponential" or "linear".
        mask: Optional binary or weight mask (H, W) or (H, W, 1). Only masked areas are blended.

    Returns:
        np.ndarray composite startrails image (uint8).
    """
    if not frames:
        raise ValueError("Cannot generate startrails from empty frame sequence")

    # Standardize first frame
    first_frame = frames[0]
    if first_frame.ndim == 2:
        first_frame = cv2.cvtColor(first_frame, cv2.COLOR_GRAY2BGR)

    h, w, c = first_frame.shape

    if not comet_mode:
        # Standard maximum-pixel blending
        # For memory efficiency with large batches, accumulate pairwise
        composite = first_frame.copy()
        for frame in frames[1:]:
            f = frame if frame.ndim == 3 else cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
            if mask is not None:
                # Apply max blending only where mask is positive
                m = mask > 0 if mask.dtype == bool else mask > 127
                if m.ndim == 2:
                    m = m[:, :, np.newaxis]
                blended = np.maximum(composite, f)
                composite = np.where(m, blended, composite)
            else:
                composite = np.maximum(composite, f)
        return composite.astype(np.uint8)

    else:
        # Comet tail mode
        composite_f = first_frame.astype(np.float32)
        alpha = float(np.clip(decay_alpha, 0.0, 1.0))

        for frame in frames[1:]:
            f = frame if frame.ndim == 3 else cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
            f_float = f.astype(np.float32)

            if decay_type == "linear":
                # Linear decay: subtract constant step based on alpha
                decay_step = (1.0 - alpha) * 255.0
                decayed = np.maximum(0.0, composite_f - decay_step)
            else:
                # Exponential decay
                decayed = composite_f * alpha

            if mask is not None:
                m = mask > 0 if mask.dtype == bool else mask > 127
                if m.ndim == 2:
                    m = m[:, :, np.newaxis]
                new_comp = np.maximum(decayed, f_float)
                composite_f = np.where(m, new_comp, f_float)
            else:
                composite_f = np.maximum(decayed, f_float)

        return np.clip(composite_f, 0, 255).astype(np.uint8)


class StartrailsAccumulator:
    """
    Incremental accumulator for generating startrails in real-time as frames are captured.
    Avoids retaining thousands of raw images in memory.
    """

    def __init__(
        self,
        comet_mode: bool = False,
        decay_alpha: float = 0.98,
        decay_type: str = "exponential",
        mask: Optional[np.ndarray] = None,
    ):
        self.comet_mode = comet_mode
        self.decay_alpha = float(decay_alpha)
        self.decay_type = decay_type
        self.mask = mask
        self.composite: Optional[np.ndarray] = None
        self.frame_count: int = 0

    def add_frame(self, frame: np.ndarray) -> np.ndarray:
        """
        Incorporate a new frame into the running startrail composite.

        Returns:
            Current composite image (uint8).
        """
        if frame is None or frame.size == 0:
            raise ValueError("Cannot add empty frame to startrails accumulator")

        f = frame if frame.ndim == 3 else cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)

        if self.composite is None:
            self.composite = f.astype(np.float32) if self.comet_mode else f.copy()
            self.frame_count = 1
            return f.astype(np.uint8)

        # A frame of another size (wrong camera or mode) cannot be stacked: skip it
        if f.shape != self.composite.shape:
            return self.composite.astype(np.uint8)

        self.frame_count += 1

        if not self.comet_mode:
            if self.mask is not None:
                m = self.mask > 0 if self.mask.dtype == bool else self.mask > 127
                if m.ndim == 2:
                    m = m[:, :, np.newaxis]
                blended = np.maximum(self.composite, f)
                self.composite = np.where(m, blended, self.composite)
            else:
                self.composite = np.maximum(self.composite, f)
        else:
            f_float = f.astype(np.float32)
            if self.decay_type == "linear":
                decay_step = (1.0 - self.decay_alpha) * 255.0
                decayed = np.maximum(0.0, self.composite - decay_step)
            else:
                decayed = self.composite * self.decay_alpha

            if self.mask is not None:
                m = self.mask > 0 if self.mask.dtype == bool else self.mask > 127
                if m.ndim == 2:
                    m = m[:, :, np.newaxis]
                new_comp = np.maximum(decayed, f_float)
                self.composite = np.where(m, new_comp, f_float)
            else:
                self.composite = np.maximum(decayed, f_float)

        return self.get_startrails()

    def get_startrails(self) -> np.ndarray:
        """Return the current startrails composite as uint8 array."""
        if self.composite is None:
            return np.zeros((0, 0, 3), dtype=np.uint8)
        if self.comet_mode:
            return np.clip(self.composite, 0, 255).astype(np.uint8)
        return self.composite.astype(np.uint8)

    def save(self, filepath: str) -> bool:
        """Save current startrails image to disk."""
        out = self.get_startrails()
        if out.size == 0:
            return False
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        return bool(cv2.imwrite(filepath, out))

    def reset(self) -> None:
        """Reset the accumulator."""
        self.composite = None
        self.frame_count = 0


class StartrailsFrameGate:
    """
    Decides whether an archived frame may enter the max-pixel startrails stack. A single bad frame
    (daylight, a headlight or lightning flash, an AE glitch, a bumped camera) otherwise burns into
    the composite for the rest of the night, so frames are rejected when:
      - the sun is above ``max_sun_elev`` (twilight/day),
      - more than ``max_saturated_frac`` of the sky is clipped,
      - the median brightness leaves [low_ratio, high_ratio] x the running median of recent
        accepted frames (or the absolute [min_median, max_median] window for the first frame),
      - the static foreground has shifted more than ``max_shift_px`` (full-res) from the last
        accepted frame (phase correlation on a 1/4-scale grey image, ~2 ms).
    After ``reset_after`` consecutive brightness/alignment rejections the references are dropped,
    so a genuine level change (moonrise) or a permanent camera move does not starve the stack.
    """

    def __init__(
        self,
        max_sun_elev: float = -12.0,
        max_saturated_frac: float = 0.01,
        min_median: float = 4.0,
        max_median: float = 150.0,
        low_ratio: float = 0.6,
        high_ratio: float = 1.35,
        max_shift_px: float = 4.0,
        history: int = 9,
        reset_after: int = 4,
    ):
        self.max_sun_elev = max_sun_elev
        self.max_saturated_frac = max_saturated_frac
        self.min_median = min_median
        self.max_median = max_median
        self.low_ratio = low_ratio
        self.high_ratio = high_ratio
        self.max_shift_px = max_shift_px
        self.history = history
        self.reset_after = reset_after
        self.reset()

    def reset(self) -> None:
        self._medians: List[float] = []
        self._ref: Optional[np.ndarray] = None
        self._streak = 0
        self.accepted = 0
        self.rejected: dict = {}

    def check(self, frame: np.ndarray, sun_elev: Optional[float] = None) -> Optional[str]:
        """Return None when the frame is accepted (and update references), else the reject reason."""
        reason = self._check(frame, sun_elev)
        if reason is None:
            self.accepted += 1
        elif reason != "daylight":  # expected every day, not a fault worth counting
            self.rejected[reason] = self.rejected.get(reason, 0) + 1
        return reason

    def _check(self, frame: np.ndarray, sun_elev: Optional[float]) -> Optional[str]:
        if sun_elev is not None and sun_elev > self.max_sun_elev:
            return "daylight"
        small = cv2.resize(frame, (frame.shape[1] // 4, frame.shape[0] // 4), interpolation=cv2.INTER_AREA)
        grey = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY) if small.ndim == 3 else small
        if float(np.count_nonzero(grey >= 250)) / grey.size > self.max_saturated_frac:
            return "saturated"
        med = float(np.median(grey))
        if self._medians:
            ref_med = float(np.median(self._medians))
            lo, hi = self.low_ratio * ref_med, self.high_ratio * ref_med
        else:
            lo, hi = self.min_median, self.max_median
        if med > hi:
            return self._soft_reject("too_bright")
        if med < lo:
            return self._soft_reject("too_dark")
        g = grey.astype(np.float32)
        if self._ref is not None and self._ref.shape == g.shape:
            (dx, dy), resp = cv2.phaseCorrelate(self._ref, g)
            if resp >= 0.1 and 4.0 * float(np.hypot(dx, dy)) > self.max_shift_px:
                return self._soft_reject("misaligned")
        self._ref = g
        self._medians = (self._medians + [med])[-self.history:]
        self._streak = 0
        return None

    def _soft_reject(self, reason: str) -> str:
        self._streak += 1
        if self._streak >= self.reset_after:
            self._medians, self._ref, self._streak = [], None, 0
        return reason
