"""Chat hub endpoints: off = 404, signed posts from known stations only, DMs only with signed fetch headers, rate limits."""
import pytest
from fastapi.testclient import TestClient

pytest.importorskip("cryptography")

from src.api.main import create_app
from src.api.service import AllSkyService
from src.community import chat
from src.community.card import build_card
from src.community.identity import Identity
from src.hardware.simulator import SimulatedIMX335Controller

HUB = "https://hub.example.org"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    cam = SimulatedIMX335Controller(width=640, height=480, seed=42)
    cam.open()
    AllSkyService.reset_instance()
    service = AllSkyService.get_instance(camera=cam)
    service.community.data_dir = str(tmp_path_factory.mktemp("chat"))
    with TestClient(create_app(camera=cam)) as c:
        c.service = service
        yield c
    service.close()
    AllSkyService.reset_instance()


def station(client, path):
    ident = Identity(str(path))
    card = build_card(ident, name="S" + ident.id[:4], lat=1.0, lon=2.0, precision_deg=0.1, url=None, camera=None,
                      software="Zenivo 1.1.0")
    assert client.post("/api/community/announce", json=card).status_code == 200
    return ident


def pubkey(ident):
    import base64
    return ident.pubkey if isinstance(ident.pubkey, str) else base64.b64encode(ident.pubkey).decode()


def post(client, payload):
    hits = client.service.community._chat_hits           # the per-address limit has its own test
    for k in [k for k in hits if k.startswith("ip:")]:
        del hits[k]
    return client.post("/api/community/chat/post", json=payload)


def settings(client, **kw):
    r = client.post("/api/community/settings", json=kw)
    assert r.status_code == 200, r.text


class TestChatAPI:
    def test_off_means_404_everywhere(self, client):
        for path in ("/api/community/chat/messages", "/api/community/chat/keys", "/api/community/chat/local"):
            assert client.get(path).status_code == 404
        assert client.post("/api/community/chat/post", json={"msg": {}}).status_code == 404
        assert client.get("/api/community/status").json()["chat"] is False      # sharing off: chat is off too
        settings(client, enabled=True, public_url=HUB, seeds=[])        # sharing on: chat on by default
        assert client.get("/api/community/status").json()["chat"] is True
        settings(client, chat_enabled=False)                            # the owner unticks chat
        assert client.get("/api/community/status").json()["chat"] is False
        assert client.get("/api/community/chat/messages").status_code == 404

    def test_public_post_and_fetch_and_who_may_post(self, client, tmp_path):
        a = station(client, tmp_path / "a.pem")
        settings(client, chat_enabled=True)
        assert client.get("/api/community/status").json()["chat"] is True
        msg = chat.make_public(a, "hello room")
        assert post(client, {"msg": msg}).json() == {"ok": True, "stored": True}
        assert post(client, {"msg": msg}).json()["stored"] is False       # duplicate
        got = client.get("/api/community/chat/messages?since=0").json()
        assert [m["body"] for m in got["messages"]] == ["hello room"]
        assert client.get(f"/api/community/chat/messages?since={got['next']}").json()["messages"] == []
        stranger = Identity(str(tmp_path / "stranger.pem"))             # never announced: card unknown
        assert post(client, {"msg": chat.make_public(stranger, "hi")}).status_code == 403
        forged = chat.make_public(a, "real")
        forged["body"] = "fake"
        assert post(client, {"msg": forged}).status_code == 400
        assert post(client, {"msg": chat.make_public(a, "old", t=1_000_000)}).status_code == 400
        assert post(client, {"nonsense": 1}).status_code == 400

    def test_direct_messages_need_signed_headers(self, client, tmp_path):
        a = station(client, tmp_path / "a.pem")
        b = station(client, tmp_path / "b.pem")
        key_b = chat.ChatKey(str(tmp_path / "b.key"))
        rec = chat.make_key_record(b, key_b)
        assert post(client, {"key": rec}).status_code == 200
        assert any(k["id"] == b.id for k in client.get("/api/community/chat/keys").json()["keys"])
        assert post(client, {"msg": chat.make_dm(a, b.id, rec["xpub"], "psst")}).json()["stored"] is True
        public = client.get("/api/community/chat/messages?since=0").json()["messages"]
        assert all(m["to"] == "*" for m in public)                       # anonymous fetch: no DMs
        mine = client.get("/api/community/chat/messages?since=0", headers=chat.sign_fetch(b)).json()["messages"]
        dm = [m for m in mine if m["to"] == b.id]
        assert len(dm) == 1 and chat.open_dm(dm[0], key_b) == "psst"
        theirs = client.get("/api/community/chat/messages?since=0", headers=chat.sign_fetch(a)).json()["messages"]
        assert not [m for m in theirs if m["to"] == b.id]                # another station's header does not unlock it
        bad = chat.sign_fetch(b)
        bad["X-Zenivo-Id"] = a.id
        assert client.get("/api/community/chat/messages?since=0", headers=bad).status_code == 403

    def test_owner_receives_dm_and_sends(self, client, tmp_path):
        a = station(client, tmp_path / "a.pem")
        keys = client.get("/api/community/chat/keys").json()["keys"]
        me = client.get("/api/community/status").json()["id"]
        mine = next(k for k in keys if k["id"] == me)                     # the hub publishes its own key too
        assert post(client, {"msg": chat.make_dm(a, me, mine["xpub"], "for the owner")}).status_code == 200
        local = client.get(f"/api/community/chat/local?peer={a.id}").json()
        assert [m["text"] for m in local["messages"]] == ["for the owner"] and local["conversations"][0]["unread"] == 1
        assert client.post("/api/community/chat/send", json={"to": "*", "body": "hi all"}).status_code == 200
        room = client.get("/api/community/chat/local?peer=*").json()["messages"]
        assert room[-1]["text"] == "hi all" and room[-1]["in"] is False
        assert client.post("/api/community/chat/block", json={"id": a.id}).status_code == 200
        assert client.get(f"/api/community/chat/local?peer={a.id}").json()["messages"] == []
        assert post(client, {"msg": chat.make_public(a, "blocked talk")}).json()["stored"] is False
        client.post("/api/community/chat/block", json={"id": a.id, "blocked": False})

    def test_rate_limits(self, client, tmp_path):
        c = station(client, tmp_path / "c.pem")
        codes = [post(client, {"msg": chat.make_public(c, f"spam {i}")}).status_code for i in range(22)]
        assert codes[:20] == [200] * 20 and codes[20:] == [429, 429]       # 20 per minute per station
        client.service.community._chat_hits.clear()
        codes = [client.post("/api/community/chat/post", content=b"x").status_code for _ in range(32)]
        assert codes[29] == 400 and codes[30] == 429                       # 30 per minute per client address

    def test_turning_chat_off_deletes_the_hub_store(self, client):
        store_file = client.service.community._path("community_chat.json")
        client.service.community.chat_store().save()
        import os
        assert os.path.exists(store_file)
        settings(client, chat_enabled=False)
        assert not os.path.exists(store_file)
        assert client.get("/api/community/chat/messages").status_code == 404
        assert client.get("/api/community/chat/local").status_code == 404
        settings(client, chat_enabled=True, enabled=False)              # sharing off: chat off whatever the box says
        assert client.get("/api/community/chat/local").status_code == 404
