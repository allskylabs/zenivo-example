"""Preview relay: upload signature, image re-encoding, the on-disk store, card compatibility and the sender."""
import base64
import io
import os
import time

import pytest

pytest.importorskip("cryptography")
from PIL import Image

from src.community import card as cardmod
from src.community import relay
from src.community.card import CardError, build_card, canonical, verify
from src.community.identity import Identity
from src.community.manager import CommunityManager
from src.community.relay import RelayRefused, RelayStore


def jpeg(w=800, h=600, exif=False, fmt="JPEG"):
    im = Image.effect_noise((w, h), 40).convert("RGB")
    out = io.BytesIO()
    kw = {}
    if exif:
        e = Image.Exif()
        e[0x010E] = "SECRET-DESCRIPTION"
        kw["exif"] = e
    im.save(out, fmt, **kw)
    return out.getvalue()


@pytest.fixture(scope="module")
def ident(tmp_path_factory):
    return Identity(str(tmp_path_factory.mktemp("rk") / "k.pem"))


def card_of(ident, **kw):
    args = dict(name="NAT sky", lat=1.0, lon=2.0, precision_deg=0.1, url=None, camera=None, software="Zenivo 1.1.0")
    args.update(kw)
    return build_card(ident, **args)


class TestSignature:
    def test_good_wrong_key_stale_and_wrong_body(self, ident, tmp_path):
        body, pub = jpeg(), base64.b64encode(ident.pubkey).decode()
        h = relay.sign_upload(ident, body)
        assert relay.verify_upload(pub, ident.id, h["X-Zenivo-Time"], h["X-Zenivo-Sig"], body)
        other = Identity(str(tmp_path / "o.pem"))
        bad = relay.sign_upload(other, body)
        assert not relay.verify_upload(pub, ident.id, bad["X-Zenivo-Time"], bad["X-Zenivo-Sig"], body)
        old = relay.sign_upload(ident, body, t=int(time.time()) - 1000)
        assert not relay.verify_upload(pub, ident.id, old["X-Zenivo-Time"], old["X-Zenivo-Sig"], body)
        assert not relay.verify_upload(pub, ident.id, h["X-Zenivo-Time"], h["X-Zenivo-Sig"], body + b"x")
        assert not relay.verify_upload(pub, ident.id, None, None, body)


class TestReencode:
    def test_strips_metadata_and_bounds_size(self):
        raw = jpeg(2400, 1200, exif=True)
        assert b"SECRET-DESCRIPTION" in raw
        out = relay.reencode(raw)
        assert b"SECRET-DESCRIPTION" not in out
        im = Image.open(io.BytesIO(out))
        assert im.format == "JPEG" and max(im.size) == 1280 and not im.getexif()

    @pytest.mark.parametrize("data", [b"not an image", jpeg(32, 32), jpeg(300, 200, fmt="PNG"), jpeg(5000, 5000)])
    def test_refuses_garbage_tiny_other_formats_and_bombs(self, data):
        with pytest.raises(RelayRefused):
            relay.reencode(data)

    def test_fit_for_upload_limit(self):
        assert len(relay.fit_for_upload(jpeg(1280, 960), 60 * 1024)) <= 60 * 1024


class TestStore:
    def test_caps_eviction_ttl_and_safe_names(self, tmp_path):
        st = RelayStore(str(tmp_path / "r"), max_stations=3, max_total=10_000, ttl=3600)
        ids = [f"{i:032x}" for i in range(5)]
        for n, i in enumerate(ids[:4]):
            st.put(i, b"x" * 100, now=1000 + n)
        assert set(st.held()) == set(ids[1:4])          # at most 3: the least recently updated went
        st.put(ids[1], b"y" * 100, now=2000)             # refreshing keeps it newest
        st.put(ids[4], b"z" * 100, now=2001)
        assert set(st.held()) == {ids[1], ids[3], ids[4]}
        st.put(ids[3], b"q" * 6000, now=2002)            # size cap: 10 000 bytes in total
        assert sum(os.path.getsize(os.path.join(st.folder, f)) for f in os.listdir(st.folder)) <= 10_000
        assert st.get("../../etc/passwd") is None and st.get("A" * 32) is None
        with pytest.raises(ValueError):
            st.put("../evil", b"x")
        left = set(st.held())
        assert st.sweep(now=2002 + 3601) == len(left) and not st.held()   # TTL
        st.put(ids[0], b"x", now=time.time())
        assert st.sweep(keep=lambda i: False) == 1 and not st.held()      # card gone / got a url
        st.put(ids[0], b"x")
        st.clear()
        assert not st.held()


class TestCardCompat:
    def test_relay_fields_roundtrip_and_old_cards_unchanged(self, ident):
        plain = card_of(ident)
        assert set(plain) <= cardmod._FIELDS_V1            # a card without relays is exactly what older stations know
        c = card_of(ident, relays=["https://relay.example.org/"], preview_sha="a" * 64, preview_t=int(time.time()))
        assert verify(c)["relays"] == ["https://relay.example.org"]
        assert set(c) - cardmod._FIELDS_V1 == cardmod._RELAY_FIELDS   # older stations refuse these: "unknown field"
        c["relays"] = ["https://other.example.org"]            # signed: tampering is detected
        with pytest.raises(CardError):
            verify(c)

    def test_relay_fields_validation(self, ident):
        base = dict(relays=["https://relay.example.org"], preview_sha="a" * 64, preview_t=int(time.time()))
        ok = card_of(ident, **base)
        for key, val in (("relays", ["http://relay.example.org"]), ("relays", ["https://r.example.org/path"]),
                         ("relays", ["https://a.example.org"] * 2), ("preview_sha", "xyz")):
            bad = dict(ok, **{key: val})
            bad["sig"] = base64.b64encode(ident.sign(canonical(bad))).decode()
            with pytest.raises(CardError):
                verify(bad)
        with_url = dict(ok, url="https://sky.example.org")
        with_url["sig"] = base64.b64encode(ident.sign(canonical(with_url))).decode()
        with pytest.raises(CardError):                      # a station with a public url never uses relays
            verify(with_url)
        assert "relays" not in card_of(ident, url="https://sky.example.org", **base)


def test_sender_uploads_once_and_card_names_the_relay(tmp_path):
    frame = jpeg(1600, 1200)
    mgr = CommunityManager(str(tmp_path), lambda: {"name": "S", "lat": 1.0, "lon": 2.0, "camera": None},
                           lambda: frame, lambda: frame)
    mgr.update_settings({"enabled": True, "seeds": ["https://relay.example.org"]})
    calls = []

    def fake(url, **kw):
        calls.append((url, kw))
        if url.endswith("/peers"):
            return {"cards": []}
        return {"ok": True, "relay": True}

    from src.community.gossip import GossipWorker
    w = GossipWorker(mgr, fetch=fake)
    assert "relays" not in mgr.own_card()
    w.run_round()
    up = [c for c in calls if "/api/community/relay/" in c[0]]
    assert len(up) == 1 and up[0][0].endswith(mgr.own_card()["id"]) and up[0][1]["allow_private"] is False
    assert len(up[0][1]["data"]) <= 200 * 1024
    hdr, pub = up[0][1]["headers"], mgr.own_card()["pubkey"]
    assert relay.verify_upload(pub, mgr.own_card()["id"], hdr["X-Zenivo-Time"], hdr["X-Zenivo-Sig"], up[0][1]["data"])
    card = verify(mgr.own_card())
    assert card["relays"] == ["https://relay.example.org"] and len(card["preview_sha"]) == 64
    assert w.last_targets[0]["relay"] == "preview relayed"
    w.run_round()   # within 30 minutes: not uploaded again
    assert len([c for c in calls if "/api/community/relay/" in c[0]]) == 1
    mgr.update_settings({"public_url": "https://sky.example.org"})   # with a public url nothing is carried or sent
    assert "relays" not in mgr.own_card()
