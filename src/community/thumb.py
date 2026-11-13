"""Small preview images for the community map (JPEG, built from the latest frame)."""
from __future__ import annotations

import base64
from typing import Optional

import cv2
import numpy as np

# (width, JPEG quality) tried in order until the image fits the embedded size limit
_EMBED_STEPS = ((160, 50), (128, 45), (112, 40), (96, 35), (64, 30))


def _encode(jpeg: bytes, width: int, quality: int) -> Optional[bytes]:
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return None
    h, w = img.shape[:2]
    if w > width:
        img = cv2.resize(img, (width, max(1, int(h * width / w))), interpolation=cv2.INTER_AREA)
    ok, out = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return out.tobytes() if ok else None


def make_thumb(jpeg: bytes, width: int = 320, quality: int = 70) -> Optional[bytes]:
    """Served at /api/community/thumb.jpg: at most `width` px wide."""
    return _encode(jpeg, width, quality)


def make_preview(jpeg: bytes, width: int = 1280, quality: int = 80) -> Optional[bytes]:
    """Served at /api/community/preview.jpg when the owner allows the large preview: at most `width` px wide."""
    return _encode(jpeg, width, quality)


def make_embedded(jpeg: bytes, limit: int = 8 * 1024) -> Optional[str]:
    """A data: URI of at most `limit` bytes of JPEG for the card of a station without a public URL, or None."""
    for width, quality in _EMBED_STEPS:
        data = _encode(jpeg, width, quality)
        if data is not None and len(data) <= limit:
            return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")
    return None
