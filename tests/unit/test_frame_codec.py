import os

import cv2
import numpy as np

from src.storage import frame_codec as fc


def test_write_read_roundtrip_and_listing(tmp_path):
    frame = (np.random.RandomState(0).rand(730, 1296, 3) * 40 + 80).astype(np.uint8)
    path = fc.write_frame(str(tmp_path / "frame_20261028_010203_004"), frame)
    assert path and path.endswith(".avif" if fc.HAS_AVIF else ".jpg")
    assert fc.read_frame(path).shape == frame.shape
    assert fc.read_frame(path, cv2.IMREAD_REDUCED_GRAYSCALE_4).shape == (183, 324)
    (tmp_path / "frame_20261028_010204_000.jpg").write_bytes(b"x")
    (tmp_path / "telemetry.jsonl").write_text("")
    assert fc.list_frames(str(tmp_path)) == [os.path.basename(path), "frame_20261028_010204_000.jpg"] or fc.HAS_AVIF is False
    assert fc.FRAME_NAME_RE.fullmatch(os.path.basename(path))
