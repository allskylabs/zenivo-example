"""Startup restore reloads keogram/startrails from their saved JPEGs; master darks are cached LRU."""

import os

import cv2
import numpy as np

from src.api.service import AllSkyService, _BoundedCache


def _svc(monkeypatch, tmp_path):
    AllSkyService.reset_instance()
    svc = AllSkyService.get_instance()
    svc.keograms_dir = str(tmp_path / "keograms")
    svc.startrails_dir = str(tmp_path / "startrails")
    svc.frames_archive_root = str(tmp_path / "frames")
    for d in (svc.keograms_dir, svc.startrails_dir, svc.frames_archive_root):
        os.makedirs(d, exist_ok=True)
    monkeypatch.setattr(svc, "_trigger_background_timelapse_compile", lambda fps=12.0: None)
    monkeypatch.setattr(svc, "_startrails_sun_elev", lambda dt=None: -30.0)
    svc.keogram_gen.reset()
    svc.startrails_acc.reset()
    return svc


def _archive(svc, n):
    ramp = np.linspace(0, 150, 972, dtype=np.float32)[:, None, None]
    for i in range(n):
        frame = np.broadcast_to(ramp + 8 * i, (972, 1296, 3)).astype(np.uint8)
        assert svc._maybe_archive_frame_and_update_products(frame, {}, force=True)


def test_restore_reads_saved_products_not_frames(monkeypatch, tmp_path):
    svc = _svc(monkeypatch, tmp_path)
    _archive(svc, 5)
    want_ts = list(svc.keogram_gen.timestamps)
    want_keo = svc.keogram_gen.get_keogram()
    assert os.path.isfile(os.path.join(svc.keograms_dir, "latest.jpg"))
    assert os.path.isfile(os.path.join(svc.startrails_dir, "latest.jpg"))

    reads = []
    real_imread = cv2.imread
    monkeypatch.setattr("src.api.service.cv2.imread", lambda p, *a: reads.append(p) or real_imread(p, *a))
    svc.keogram_gen.reset()
    svc.startrails_acc.reset()
    svc._restore_today_products()

    reads = [p for p in reads if str(p).startswith(str(tmp_path))]  # ignore other service threads
    # the two dated products are decoded, plus at most the newest frame (for its shape; the frame codec may use
    # another decoder than cv2.imread, so it is not always counted here); never the whole day's frames
    assert 2 <= len(reads) <= 3
    assert len(svc.keogram_gen) == 5
    assert [t.isoformat() for t in svc.keogram_gen.timestamps] == [t.isoformat() for t in want_ts]
    got = svc.keogram_gen.get_keogram()
    assert got.shape == want_keo.shape
    assert float(np.mean(np.abs(got.astype(int) - want_keo.astype(int)))) < 3.0  # JPEG round trip
    assert svc.startrails_acc.composite.shape == (972, 1296, 3)
    assert svc.startrails_acc.frame_count == 5

    # Accumulation carries on from the restored state
    _archive(svc, 1)
    assert len(svc.keogram_gen) == 6
    AllSkyService.reset_instance()


def test_restore_falls_back_to_frames_without_saved_products(monkeypatch, tmp_path):
    svc = _svc(monkeypatch, tmp_path)
    _archive(svc, 3)
    for d in (svc.keograms_dir, svc.startrails_dir):
        for f in os.listdir(d):
            os.remove(os.path.join(d, f))
    svc.keogram_gen.reset()
    svc.startrails_acc.reset()
    svc._restore_today_products()
    assert len(svc.keogram_gen) == 3
    assert svc.startrails_acc.frame_count == 3
    assert os.path.isfile(os.path.join(svc.keograms_dir, "latest.jpg"))
    AllSkyService.reset_instance()


def test_dark_cache_keeps_only_recent_entries():
    cache = _BoundedCache(max_items=2)
    cache["a"] = 1
    cache["b"] = 2
    assert cache.get("a") == 1  # refreshes "a"
    cache["c"] = 3
    assert list(cache) == ["a", "c"]
    assert cache.get("b") is None
