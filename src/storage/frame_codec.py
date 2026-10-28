"""Archived-frame image codec: AVIF when an encoder is available, JPEG otherwise.

cv2 (opencv-python-headless) has no AVIF support, so AVIF goes through Pillow (>= 11.3 bundles libavif;
older Pillow needs pillow-avif-plugin). Legacy ``frame_*.jpg`` files stay readable next to new ``.avif`` ones.
"""
import io
import os
import re
from typing import List, Optional

import cv2
import numpy as np

try:  # Pillow < 11.3 only gets AVIF from this plugin
    import pillow_avif  # noqa: F401
except ImportError:
    pass
from PIL import Image

AVIF_QUALITY = 62   # ~7x smaller than the old JPEG q88 on night frames; stars stay crisp
AVIF_SPEED = 8      # libavif 0..10, 8 is ~0.1 s for a 1296x730 frame on the Pi 4

FRAME_EXTS = (".avif", ".jpg")
FRAME_NAME_RE = re.compile(r"frame_\d{8}_\d{6}_\d{3}\.(?:avif|jpg)")

Image.init()
HAS_AVIF = "AVIF" in Image.SAVE and "AVIF" in Image.OPEN


def is_frame_name(name: str) -> bool:
    return name.startswith("frame_") and name.endswith(FRAME_EXTS)


def media_type(path: str) -> str:
    return "image/avif" if path.endswith(".avif") else "image/jpeg"


def list_frames(day_dir: str) -> List[str]:
    """Sorted archived frame file names in a day folder (both formats)."""
    if not os.path.isdir(day_dir):
        return []
    return sorted(f for f in os.listdir(day_dir) if is_frame_name(f))


def write_frame(path_noext: str, bgr: np.ndarray) -> Optional[str]:
    """Writes the frame as AVIF (JPEG if AVIF is unavailable); returns the written path or None."""
    if HAS_AVIF:
        try:
            buf = io.BytesIO()
            Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)).save(buf, "AVIF", quality=AVIF_QUALITY, speed=AVIF_SPEED)
            path = path_noext + ".avif"
            with open(path, "wb") as fp:
                fp.write(buf.getvalue())
            return path
        except Exception:
            pass
    path = path_noext + ".jpg"
    return path if cv2.imwrite(path, bgr, [cv2.IMWRITE_JPEG_QUALITY, 88]) else None


def read_frame(path: str, flags: int = cv2.IMREAD_COLOR) -> Optional[np.ndarray]:
    """cv2.imread replacement that also decodes AVIF. Honours IMREAD_COLOR / IMREAD_GRAYSCALE / IMREAD_REDUCED_GRAYSCALE_4."""
    if not path.endswith(".avif"):
        return cv2.imread(path, flags)
    try:
        with Image.open(path) as im:
            rgb = np.asarray(im.convert("RGB"))
    except Exception:
        return None
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    if flags == cv2.IMREAD_REDUCED_GRAYSCALE_4:
        g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        return cv2.resize(g, ((g.shape[1] + 3) // 4, (g.shape[0] + 3) // 4), interpolation=cv2.INTER_AREA)
    if flags == cv2.IMREAD_GRAYSCALE:
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    return bgr
