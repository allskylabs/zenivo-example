"""Outbound-connection defaults and guest-reachable abuse limits (one representative test per rule)."""

import math
import sys

import numpy as np
import pytest


def test_sync_is_off_by_default(monkeypatch):
    import run_allsky
    from src.sync.client import SyncClient

    monkeypatch.setattr(sys, "argv", ["run_allsky.py"])
    assert run_allsky.parse_args().sync_target == ""
    client = SyncClient()
    assert client.base_url == "" and client.device_id.startswith("zenivo-")


def test_remote_solver_not_called_when_url_unset(monkeypatch):
    from src.calibration import station_solver

    calls = []
    monkeypatch.setattr(station_solver, "_multipart_post_solve", lambda *a, **k: calls.append(a) or {})
    res = station_solver.solve_station_parameters(np.zeros((48, 64, 3), np.uint8), 10.0, 20.0)
    assert res["success"] is False
    assert "no remote solver configured" in res["error"]
    assert calls == []


def test_stream_limiter_global_and_per_client():
    from src.api.stream import StreamLimiter

    lim = StreamLimiter(total=3, per_client=2)
    assert lim.acquire("a") and lim.acquire("a")
    assert not lim.acquire("a")          # per-client cap
    assert lim.acquire("b")
    assert not lim.acquire("c")          # global cap
    lim.release("a")
    assert lim.acquire("c")


def test_replay_path_cannot_leave_uploads(tmp_path):
    from src.api.stream import resolve_uploads_path

    (tmp_path / "uploads").mkdir()
    (tmp_path / "uploads" / "ok.jpg").write_bytes(b"x")
    (tmp_path / "secret.txt").write_bytes(b"s")
    assert resolve_uploads_path(str(tmp_path), "/uploads/ok.jpg?x=1") is not None
    assert resolve_uploads_path(str(tmp_path), "/uploads/../secret.txt") is None


def test_fr24_id_validation_cache_bound_and_guest_bounds():
    from src.analytics.fr24 import Fr24Feed, CACHE_MAX, _lru_put, normalize_guest_bounds
    from collections import OrderedDict

    feed = Fr24Feed(enabled=True)
    for bad in ("", "../x", "ABC", "zz", "a" * 17, "é1"):
        with pytest.raises(ValueError):
            feed.flight_detail(bad)
    cache = OrderedDict()
    for i in range(CACHE_MAX + 20):
        _lru_put(cache, i, i, CACHE_MAX)
    assert len(cache) == CACHE_MAX and 0 not in cache
    assert normalize_guest_bounds("21.3,20.2,105.1,106.4", 21.0, 105.8) == "22,20,105,107"
    for bad in ("0,-5,10,20", "nan,1,2,3", "global2"):
        with pytest.raises(ValueError):
            normalize_guest_bounds(bad, 21.0, 105.8)


def test_invalid_timestamps_are_400(guest_client):
    for q in ("t0=nan&t1=10", "t0=0&t1=1e20", "t0=5&t1=1"):
        assert guest_client.get(f"/api/traffic?{q}").status_code == 400
    assert guest_client.get("/api/frames/near?ts=nan").status_code == 400


def test_guest_range_limited_to_24h(guest_client):
    assert guest_client.get("/api/day-objects?t0=1700000000&t1=1700200000").status_code == 400


def test_guest_station_config_hides_secrets(guest_client):
    guest_client.service.station_config.update({"google_maps_key": "KEY123", "remote_solver_url": "https://solver.example"})
    body = guest_client.get("/api/station/config").text
    assert "KEY123" not in body and "solver.example" not in body
    assert "KEY123" in guest_client.get("/api/map/config").text  # the browser needs it there


def test_security_headers_and_cors(guest_client):
    r = guest_client.get("/api/version")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "SAMEORIGIN"
    assert "geolocation=(self)" in r.headers["permissions-policy"]
    assert "content-security-policy-report-only" in r.headers
    pre = guest_client.options("/api/station/settings", headers={"Origin": "https://evil.example",
                                                           "Access-Control-Request-Method": "POST"})
    assert pre.status_code == 400
