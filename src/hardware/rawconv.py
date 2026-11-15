"""Shared RAW16 -> 8-bit BGR conversion for the vendor-SDK USB cameras (ZWO, QHY)."""

from typing import Optional

import numpy as np

# Bayer pattern name (as written in camera docs: row 0 then row 1) -> OpenCV code. OpenCV names the
# pattern by its second row/column offset, so RGGB data is COLOR_BayerBG2BGR.
BAYER_TO_CV = {"RGGB": "COLOR_BayerBG2BGR", "BGGR": "COLOR_BayerRG2BGR", "GRBG": "COLOR_BayerGB2BGR", "GBRG": "COLOR_BayerGR2BGR"}


def raw16_to_bgr8(raw: np.ndarray, bayer: Optional[str] = None) -> np.ndarray:
    """RAW (uint16, full 16-bit scale) -> uint8 BGR. `bayer` None means a mono sensor (grey replicated to 3 channels)."""
    if raw.dtype == np.uint16:
        raw = raw >> 8
    raw = raw.astype(np.uint8, copy=False)
    if bayer:
        import cv2

        return cv2.cvtColor(raw, getattr(cv2, BAYER_TO_CV.get(bayer, BAYER_TO_CV["RGGB"])))
    return np.repeat(raw[:, :, None], 3, axis=2)
