"""
Unit Tests for AllSky OBS Overlay Synchronization and OBS Custom Dock.
Verifies:
- GET /api/obs/overlay returns default or persisted settings
- POST /api/obs/overlay updates settings and persists to station_config
- Endpoints /obs, /obs/dock, /obs/control, /dock serve valid HTML
"""

import pytest
from fastapi.testclient import TestClient

from src.hardware.simulator import SimulatedIMX335Controller
from src.api.service import AllSkyService
from src.api.main import create_app


@pytest.fixture(scope="module")
def sim_camera():
    cam = SimulatedIMX335Controller(width=640, height=480, seed=42)
    cam.open()
    yield cam
    cam.close()


@pytest.fixture(scope="module")
def api_client(sim_camera):
    AllSkyService.reset_instance()
    service = AllSkyService.get_instance(camera=sim_camera)
    app = create_app(camera=sim_camera)
    with TestClient(app) as client:
        yield client
    service.close()
    AllSkyService.reset_instance()


def test_obs_overlay_get_defaults(api_client):
    res = api_client.get("/api/obs/overlay")
    assert res.status_code == 200
    data = res.json()
    assert "sky_overlay" in data
    assert "sky_labels" in data
    assert "grid_overlay" in data
    assert "sun_moon" in data
    assert "badge" in data
    assert "fit" in data
    assert "bg" in data


def test_obs_overlay_post_update(api_client):
    patch = {
        "sky_overlay": True,
        "sky_labels": True,
        "grid_overlay": True,
        "adsb_overlay": True,
        "events_overlay": True,
        "fit": "contain",
        "bg": "transparent",
        "night_vision": True,
        "opacity": 0.85,
        "zoom": 2.5,
        "pan_x": 15.0,
        "pan_y": -10.0,
        "rotate": 90.0,
        "flip_h": True,
        "brightness": 1.2,
        "contrast": 1.1,
        "title_text": "AllSky Hanoi",
        "title_sub": "Live Meteor Watch",
        "title_pos": "bottom-center",
        "view_mode": "event",
        "replay_url": "/api/events/img/2026-10-22/test.jpg",
    }
    res = api_client.post("/api/obs/overlay", json=patch)
    assert res.status_code == 200
    resp_data = res.json()
    assert resp_data["success"] is True
    settings = resp_data["settings"]
    assert settings["sky_overlay"] is True
    assert settings["adsb_overlay"] is True
    assert settings["events_overlay"] is True
    assert settings["fit"] == "contain"
    assert settings["bg"] == "transparent"
    assert settings["zoom"] == 2.5
    assert settings["pan_x"] == 15.0
    assert settings["pan_y"] == -10.0
    assert settings["rotate"] == 90.0
    assert settings["flip_h"] is True
    assert settings["title_text"] == "AllSky Hanoi"
    assert settings["view_mode"] == "event"

    # Verify GET returns updated values
    get_res = api_client.get("/api/obs/overlay")
    assert get_res.status_code == 200
    get_data = get_res.json()
    assert get_data["zoom"] == 2.5
    assert get_data["title_text"] == "AllSky Hanoi"
    assert get_data["fit"] == "contain"



def test_obs_html_routes(api_client):
    for path in ["/obs", "/full", "/live/full", "/obs/dock", "/obs/control", "/dock"]:
        res = api_client.get(path)
        assert res.status_code == 200
        assert "text/html" in res.headers.get("content-type", "")
