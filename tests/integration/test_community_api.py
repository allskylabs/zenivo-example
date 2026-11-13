"""Community API: off by default, then a station that announces itself shows up in /peers."""
import os

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("cryptography")

from src.api.main import create_app
from src.api.service import AllSkyService
from src.community.card import build_card, verify
from src.community.identity import Identity
from src.hardware.simulator import SimulatedIMX335Controller


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    cam = SimulatedIMX335Controller(width=640, height=480, seed=42)
    cam.open()
    AllSkyService.reset_instance()
    service = AllSkyService.get_instance(camera=cam)
    service.community.data_dir = str(tmp_path_factory.mktemp("community"))  # never touch the real data/ folder
    app = create_app(camera=cam)
    with TestClient(app) as c:
        c.service = service
        yield c
    service.close()
    AllSkyService.reset_instance()


@pytest.fixture(scope="module")
def foreign(tmp_path_factory):
    ident = Identity(str(tmp_path_factory.mktemp("other") / "k.pem"))
    return build_card(ident, name="Far away", lat=48.85, lon=2.35, precision_deg=0.1, url="https://far.example.org",
                      camera="Sony IMX335", software="Zenivo 1.1.0")


class TestCommunityAPI:
    def test_off_by_default(self, client, foreign):
        assert client.get("/api/community/status").json()["enabled"] is False
        assert client.get("/api/community/peers").json() == {"cards": []}
        assert client.get("/api/community/card").status_code == 404
        assert client.get("/api/community/thumb.jpg").status_code == 404
        assert client.post("/api/community/announce", json=foreign).status_code == 403
        assert os.listdir(client.service.community.data_dir) == []  # nothing written while off

    def test_enable_and_announce(self, client, foreign):
        res = client.post("/api/community/settings", json={"enabled": True, "name": "My sky"})
        assert res.status_code == 200 and res.json()["enabled"] is True
        own = client.get("/api/community/card").json()
        assert verify(own)["name"] == "My sky"

        assert client.post("/api/community/announce", json=foreign).json() == {"ok": True}
        res = client.get("/api/community/peers")
        assert res.headers["access-control-allow-origin"] == "*"
        ids = [c["id"] for c in res.json()["cards"]]
        assert ids == [own["id"], foreign["id"]]
        assert client.get("/api/community/status").json()["known"] == 1

    def test_bad_cards_rejected(self, client, foreign):
        tampered = dict(foreign, name="Hijacked")
        assert client.post("/api/community/announce", json=tampered).status_code == 400
        assert client.post("/api/community/announce", content=b"not json").status_code == 400
        assert client.post("/api/community/announce", content=b"{" + b" " * 20000 + b"}").status_code == 413

    def test_settings_validation(self, client):
        assert client.post("/api/community/settings", json={"public_url": "http://plain.example"}).status_code == 422
        assert client.post("/api/community/settings", json={"precision_deg": 5}).status_code == 422
