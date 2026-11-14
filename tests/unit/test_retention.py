"""Disk retention: age rules, size cap, extra directories and the storage API."""

import os
from datetime import date, timedelta

from src.storage.retention import RetentionManager, merge_policy

TODAY = date(2026, 10, 16)


def _make_day(root, day: date, n=3, size=1000):
    d = root / "frames" / day.isoformat()
    d.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (d / f"frame_{i}.jpg").write_bytes(b"x" * size)
    return d


def _make_product(root, kind, name, size=1000):
    d = root / "uploads" / "allsky" / kind
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_bytes(b"x" * size)
    return p


def test_age_rules_delete_old_frames_and_products_only(tmp_path):
    old = _make_day(tmp_path, TODAY - timedelta(days=10))
    recent = _make_day(tmp_path, TODAY - timedelta(days=2))
    today = _make_day(tmp_path, TODAY)
    old_mp4 = _make_product(tmp_path, "timelapses", f"{(TODAY - timedelta(days=90)).isoformat()}.mp4")
    new_mp4 = _make_product(tmp_path, "timelapses", f"{(TODAY - timedelta(days=5)).isoformat()}.mp4")
    latest = _make_product(tmp_path, "timelapses", "latest.mp4")

    rm = RetentionManager(str(tmp_path), {"frames_keep_days": 7, "products_keep_days": 60, "min_free_gb": 0.0})
    report = rm.run(today=TODAY)

    assert not old.exists() and not old_mp4.exists()
    assert recent.exists() and today.exists() and new_mp4.exists() and latest.exists()
    assert report["deleted_count"] == 2


def test_size_cap_removes_oldest_but_never_today(tmp_path):
    days = [_make_day(tmp_path, TODAY - timedelta(days=k), n=2, size=500_000) for k in (3, 2, 1, 0)]
    # 4 days x 1 MB; cap at 2.5 MB -> the two oldest days go, today always stays
    rm = RetentionManager(str(tmp_path), {"frames_keep_days": 30, "max_data_gb": 0.0025, "min_free_gb": 0.0})
    rm.run(today=TODAY)
    assert [d.exists() for d in days] == [False, False, True, True]

    rm.policy["max_data_gb"] = 0.0000001
    rm.run(today=TODAY)
    assert days[-1].exists()


def test_extra_dir_cleanup_by_date_and_mtime(tmp_path):
    extra = tmp_path / "extra_archive"
    old_day = extra / (TODAY - timedelta(days=30)).isoformat()
    new_day = extra / date.today().isoformat()
    old_day.mkdir(parents=True)
    new_day.mkdir(parents=True)
    stale = extra / "stale.jpg"
    stale.write_bytes(b"x")
    os.utime(stale, (0, 0))

    rm = RetentionManager(str(tmp_path / "data"), {"min_free_gb": 0.0, "extra_dirs": [{"path": str(extra), "keep_days": 3}]})
    rm.run()
    assert not old_day.exists() and not stale.exists() and new_day.exists()


def test_merge_policy_ignores_unknown_keys():
    p = merge_policy({"frames_keep_days": 3, "bogus": 1})
    assert p["frames_keep_days"] == 3 and "bogus" not in p


def test_storage_api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from src.api.main import create_app
    from src.api.service import AllSkyService

    AllSkyService.reset_instance()
    client = TestClient(create_app())
    monkeypatch.setattr(AllSkyService.get_instance(), "_save_station_config", lambda: None)
    res = client.get("/api/storage")
    assert res.status_code == 200
    body = res.json()
    assert "policy" in body and "disk" in body

    res = client.put("/api/storage/policy", json={"frames_keep_days": 5, "max_data_gb": 4})
    assert res.status_code == 200
    assert res.json()["frames_keep_days"] == 5
    assert client.put("/api/storage/policy", json={"frames_keep_days": 0}).status_code == 422
    assert client.post("/api/storage/cleanup").status_code == 200
    AllSkyService.reset_instance()


def test_activity_log_rotation_dedupe_and_api(tmp_path):
    from fastapi.testclient import TestClient
    from src import activity as act
    from src.api.main import create_app

    log = act.ActivityLog(str(tmp_path), max_bytes=2000, backup_count=2)
    for i in range(60):
        log.info("archive", f"entry {i}")
    log.error("sync", "upload failed")
    log.error("sync", "upload failed")
    files = sorted(os.listdir(tmp_path))
    assert "activity.jsonl" in files and len(files) <= 3  # rotated, bounded
    newest = log.query(limit=1)[0]
    assert newest["level"] == "error" and newest["repeat"] == 2
    assert [e["level"] for e in log.query(level="warning")] == ["error"]

    act._activity = log
    try:
        client = TestClient(create_app())
        body = client.get("/api/logs", params={"level": "error"}).json()
        assert body["entries"][0]["message"] == "upload failed"
        assert body["counts_24h"]["error"] == 2
        assert client.get("/api/logs", params={"level": "bogus"}).status_code == 422
        assert client.get("/api/logs/download").status_code == 200
        assert client.get("/logs").status_code == 200
    finally:
        act._activity = None
