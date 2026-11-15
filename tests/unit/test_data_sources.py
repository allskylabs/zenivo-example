"""Settings > Integrations: satellite settings and the data-source status list."""

import pytest

from src.analytics import satellites as sats


@pytest.fixture
def client_service(tmp_path):
    from fastapi.testclient import TestClient
    from src.api.main import create_app
    from src.api.service import AllSkyService
    from src.hardware.simulator import SimulatedIMX335Controller

    sim = SimulatedIMX335Controller(width=640, height=480, seed=1)
    sim.open()
    AllSkyService.reset_instance()
    service = AllSkyService.get_instance(camera=sim)
    service.station_config_path = str(tmp_path / "station_config.json")
    for k in ("satellites", "adsb"):  # the repo data/ may hold settings made in the UI
        service.station_config.pop(k, None)
    service.output_dir = str(tmp_path)
    service._sat_tracker = None
    with TestClient(create_app(camera=sim)) as client:
        yield client, service
    service.close()
    AllSkyService.reset_instance()


def test_defaults_and_choices(client_service):
    client, _ = client_service
    d = client.get("/api/satellites/config").json()
    assert d["enabled"] == 1 and d["groups"] == ["stations", "visual"] and d["refresh_h"] == 24
    assert d["tle_url"] == "" and "weather" in d["group_choices"] and "{group}" in d["default_url"]
    assert d["status"]["groups"] == ["stations", "visual"]


def test_save_groups_refresh_and_off_switch(client_service):
    client, service = client_service
    r = client.post("/api/satellites/config", json={"groups": ["weather", "stations"], "refresh_h": 12, "enabled": False})
    assert r.status_code == 200
    d = r.json()
    assert d["groups"] == ["stations", "weather"] and d["refresh_h"] == 12 and d["enabled"] == 0
    assert service._sat_tracker.groups == ["stations", "weather"] and service._sat_tracker.refresh_s == 12 * 3600
    # switched off: the overlay endpoint reports nothing instead of downloading orbits
    assert client.get("/api/satellites").json()["satellites"] == []


@pytest.mark.parametrize("body", [
    {"groups": ["stations", "nope"]}, {"groups": []}, {"refresh_h": 5},
    {"tle_url": "http://example.org/{group}"}, {"tle_url": "https://example.org/tle"},
])
def test_invalid_settings_rejected(client_service, body):
    client, _ = client_service
    assert client.post("/api/satellites/config", json=body).status_code in (400, 422)


def test_private_tle_source_refused(client_service):
    client, _ = client_service
    r = client.post("/api/satellites/config", json={"tle_url": "https://127.0.0.1/{group}"})
    assert r.status_code == 400


def test_tracker_configure_drops_unselected_groups_and_forces_refetch(tmp_path):
    t = sats.SatelliteTracker(str(tmp_path / "tle.json"))
    t._sats = [("A", "stations", object()), ("B", "visual", object())]
    t._fetched_at = 123.0
    t.configure(groups=["stations"])
    assert [s[0] for s in t._sats] == ["A"] and t._fetched_at == 0.0
    assert t.status()["source"] == "celestrak.org"


def test_fetch_failure_is_reported_not_raised(tmp_path, monkeypatch):
    t = sats.SatelliteTracker(str(tmp_path / "tle.json"))

    def boom(*a, **k):
        raise OSError("network down")

    monkeypatch.setattr(sats, "net_urlopen", boom)
    t._fetching = True
    t._fetch()
    st = t.status()
    assert "network down" in st["error"] and st["fetching"] is False


def test_data_sources_lists_every_feed(client_service):
    client, _ = client_service
    d = client.get("/api/data-sources").json()
    ids = [s["id"] for s in d["sources"]]
    assert ids == ["adsb", "adsb_public", "fr24", "satellites", "radiosondes", "airports"]
    by = {s["id"]: s for s in d["sources"]}
    assert by["adsb"]["state"] == "off" and by["adsb_public"]["enabled"] is False and by["fr24"]["state"] == "off"
    assert by["satellites"]["enabled"] is True
    for s in d["sources"]:
        assert s["state"] in ("ok", "waiting", "error", "off") and s["where"]
