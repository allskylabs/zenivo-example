"""Relay endpoints: refused unless the owner opted in, signature-bound, re-encoded, public GET."""
import io

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("cryptography")
from PIL import Image

from src.api.main import create_app
from src.api.service import AllSkyService
from src.community import relay
from src.community.card import build_card
from src.community.identity import Identity
from src.hardware.simulator import SimulatedIMX335Controller


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    cam = SimulatedIMX335Controller(width=640, height=480, seed=42)
    cam.open()
    AllSkyService.reset_instance()
    service = AllSkyService.get_instance(camera=cam)
    service.community.data_dir = str(tmp_path_factory.mktemp("relay"))
    with TestClient(create_app(camera=cam)) as c:
        c.service = service
        yield c
    service.close()
    AllSkyService.reset_instance()


def jpeg(w=800, h=600):
    out = io.BytesIO()
    Image.effect_noise((w, h), 40).convert("RGB").save(out, "JPEG")
    return out.getvalue()


def card(path, **kw):
    ident = Identity(str(path))
    args = dict(name="Behind NAT", lat=1.0, lon=2.0, precision_deg=0.1, url=None, camera=None, software="Zenivo 1.1.0")
    args.update(kw)
    return ident, build_card(ident, **args)


def upload(client, ident, body, **over):
    hdr = relay.sign_upload(ident, body)
    hdr.update(over)
    return client.post(f"/api/community/relay/{ident.id}", content=body, headers={"Content-Type": "image/jpeg", **hdr})


class TestRelayAPI:
    def test_refused_validated_stored_and_removed(self, client, tmp_path):
        nat, nat_card = card(tmp_path / "a.pem")
        web, web_card = card(tmp_path / "b.pem", url="https://sky.example.org")
        body = jpeg()
        assert upload(client, nat, body).status_code == 404                      # Community off
        client.post("/api/community/settings", json={"enabled": True})
        assert client.get("/api/community/status").json()["relay"] is True      # on by default
        client.post("/api/community/settings", json={"relay_previews": False})
        assert upload(client, nat, body).status_code == 404                      # owner switched it off
        assert client.get("/api/community/status").json()["relay"] is False
        client.post("/api/community/settings", json={"relay_previews": True})

        assert upload(client, nat, body).status_code == 403                      # card unknown
        assert client.post("/api/community/announce", json=nat_card).json() == {"ok": True, "relay": True}
        client.post("/api/community/announce", json=web_card)
        assert upload(client, web, body).status_code == 403                      # has a public url
        assert upload(client, nat, body, **{"X-Zenivo-Sig": "A" * 86 + "=="}).status_code == 403
        assert upload(client, nat, b"x" * (300 * 1024)).status_code == 413

        assert upload(client, nat, body).status_code == 200
        res = client.get(f"/api/community/relay/{nat.id}.jpg")
        assert res.status_code == 200 and res.headers["content-type"] == "image/jpeg"
        assert res.headers["cross-origin-resource-policy"] == "cross-origin"
        assert res.headers["x-content-type-options"] == "nosniff"
        assert Image.open(io.BytesIO(res.content)).format == "JPEG"
        assert client.get(f"/api/community/relay/{web.id}.jpg").status_code == 404
        assert client.get("/api/community/relay/..%2F..%2Fx.jpg").status_code == 404
        assert client.get("/api/community/peers").json()["relayed"][nat.id] > 0

        client.post("/api/community/settings", json={"relay_previews": False})   # turning it off deletes the images
        assert client.get(f"/api/community/relay/{nat.id}.jpg").status_code == 404
        assert not client.service.community.relay_store().held()
