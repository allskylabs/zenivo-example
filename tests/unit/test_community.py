"""Community: signed station cards, the verified-card registry and the SSRF guard (no real network)."""
import base64
import json
import socket
import time

import pytest

from src.community import card as cardmod
from src.community import netguard
from src.community.card import CardError, build_card, canonical, snap, verify
from src.community.identity import Identity
from src.community.registry import MAX_AGE_S, MAX_CARDS, Registry

pytest.importorskip("cryptography")


@pytest.fixture(scope="module")
def ident(tmp_path_factory):
    return Identity(str(tmp_path_factory.mktemp("key") / "community_key.pem"))


def make(ident, **over):
    args = dict(name="Test sky", lat=21.0285, lon=105.8542, precision_deg=0.1, url="https://sky.example.org",
                camera="Sony IMX335", software="Zenivo 1.1.0")
    args.update(over)
    return build_card(ident, **args)


def resign(ident, card):
    card["sig"] = base64.b64encode(ident.sign(canonical(card))).decode()
    return card


class TestCard:
    def test_sign_verify_roundtrip_and_grid(self, ident):
        card = make(ident)
        assert verify(card) is card
        assert (card["lat"], card["lon"]) == (21.0, 105.9)
        assert snap(21.0285, 1.0) == 21.0 and snap(105.8542, 0.01) == 105.85
        assert json.loads(json.dumps(card)) == card

    def test_key_file_is_private_and_reused(self, tmp_path):
        path = str(tmp_path / "k.pem")
        a, b = Identity(path), Identity(path)
        assert a.id == b.id and len(a.id) == 32
        assert (tmp_path / "k.pem").stat().st_mode & 0o777 == 0o600

    @pytest.mark.parametrize("change", [{"name": "Other"}, {"lat": 1.0}, {"url": "https://evil.example"}, {"updated": 1}])
    def test_tampered_card_rejected(self, ident, change):
        card = make(ident)
        card.update(change)
        with pytest.raises(CardError, match="signature"):
            verify(card)

    def test_wrong_id_rejected(self, ident):
        card = make(ident)
        card["id"] = "0" * 32
        with pytest.raises(CardError, match="id"):
            verify(resign(ident, card))

    @pytest.mark.parametrize("field,value", [
        ("name", ""), ("name", "x" * 61), ("name", "bad\x00name"), ("name", 5),
        ("lat", 91), ("lon", -181), ("lat", "21"), ("lat", True),
        ("url", "http://sky.example.org"), ("url", "https://u:p@sky.example.org"), ("url", "javascript:alert(1)"),
        ("url", "https://" + "a" * 200 + ".org"),
        ("camera", "c" * 61), ("software", None), ("v", 2), ("extra", 1),
        ("thumb", "data:text/html;base64,AAAA"), ("thumb", "data:image/jpeg;base64," + "A" * 20000),
        ("updated", time.time() + 3600), ("updated", -1),
    ])
    def test_invalid_fields_rejected(self, ident, field, value):
        card = make(ident)
        card[field] = value
        with pytest.raises(CardError):
            verify(resign(ident, card))

    def test_oversized_card_rejected(self, ident):
        card = make(ident)
        card["name"] = "n" * 17000
        with pytest.raises(CardError):
            verify(resign(ident, card))

    @pytest.mark.parametrize("bad", [None, [], "x", 1, {}])
    def test_non_card_rejected(self, bad):
        with pytest.raises(CardError):
            verify(bad)


def fake(cid, updated):
    return {"id": cid, "updated": updated, "name": cid}


class TestRegistry:
    def test_newer_wins_and_persists(self, tmp_path, ident):
        reg = Registry(str(tmp_path / "peers.json"), own_id="me")
        now = time.time()
        assert reg.merge(fake("a", now - 100), now)
        assert not reg.merge(fake("a", now - 200), now)          # older does not replace
        assert reg.merge(fake("a", now - 50), now)
        assert [c["updated"] for c in reg.cards(now)] == [now - 50]
        assert not reg.merge(fake("me", now), now)               # own card is never a peer
        card = make(ident)
        other = Registry(str(tmp_path / "peers.json"), own_id="me")
        reg.merge(card)
        reg.save()
        other.load()
        assert [c["id"] for c in other.cards()] == [card["id"]]  # reloaded cards are re-verified

    def test_expiry(self, tmp_path):
        reg = Registry(str(tmp_path / "p.json"))
        now = time.time()
        assert not reg.merge(fake("old", now - MAX_AGE_S - 1), now)
        reg.merge(fake("edge", now - MAX_AGE_S + 10), now)
        assert reg.count() == 1
        assert reg.cards(now + 60) == []                         # ages out later

    def test_cap_drops_oldest(self, tmp_path):
        reg = Registry(str(tmp_path / "p.json"))
        now = time.time()
        for i in range(MAX_CARDS + 5):
            reg.merge(fake(f"s{i}", now - 10000 + i), now)
        assert reg.count() == MAX_CARDS
        ids = {c["id"] for c in reg.cards(now)}
        assert "s0" not in ids and f"s{MAX_CARDS + 4}" in ids


def resolves_to(monkeypatch, *addrs):
    def fake_getaddrinfo(host, port, *a, **k):
        return [(socket.AF_INET6 if ":" in x else socket.AF_INET, socket.SOCK_STREAM, 6, "", (x, port)) for x in addrs]
    monkeypatch.setattr(netguard.socket, "getaddrinfo", fake_getaddrinfo)


class TestNetguard:
    @pytest.mark.parametrize("url,addrs", [
        ("https://x.example", ["127.0.0.1"]), ("https://x.example", ["10.1.2.3"]), ("https://x.example", ["192.168.0.5"]),
        ("https://x.example", ["169.254.169.254"]), ("https://x.example", ["::1"]), ("https://x.example", ["fe80::1"]),
        ("https://x.example", ["fd00::1"]), ("https://x.example", ["::ffff:10.0.0.1"]), ("https://x.example", ["224.0.0.1"]),
        ("https://x.example", ["0.0.0.0"]), ("https://x.example", ["93.184.216.34", "127.0.0.1"]),  # one bad address is enough
        ("http://x.example", ["93.184.216.34"]), ("ftp://x.example", ["93.184.216.34"]), ("file:///etc/passwd", []),
        ("https://user:pw@x.example", ["93.184.216.34"]), ("https://x.example:99999", ["93.184.216.34"]),
        ("https://", []), ("", []), ("https://x.example/ a", ["93.184.216.34"]),
    ])
    def test_rejected(self, monkeypatch, url, addrs):
        resolves_to(monkeypatch, *addrs)
        with pytest.raises(netguard.UnsafeURL):
            netguard.safe_url(url)

    def test_accepts_public(self, monkeypatch):
        resolves_to(monkeypatch, "93.184.216.34", "2606:2800:220:1::1")
        assert netguard.safe_url("https://x.example:8443/path") == "https://x.example:8443/path"

    @pytest.mark.parametrize("url,addr", [("http://192.168.1.5:8000", "192.168.1.5"), ("https://localhost", "127.0.0.1")])
    def test_allow_private_for_owner_seeds(self, monkeypatch, url, addr):
        resolves_to(monkeypatch, addr)
        assert netguard.safe_url(url, allow_private=True) == url
        with pytest.raises(netguard.UnsafeURL):
            netguard.safe_url(url)

    def test_credentials_rejected_even_when_private_allowed(self, monkeypatch):
        resolves_to(monkeypatch, "192.168.1.5")
        with pytest.raises(netguard.UnsafeURL):
            netguard.safe_url("http://u:p@192.168.1.5", allow_private=True)

    def test_unresolvable_host_rejected(self, monkeypatch):
        def boom(*a, **k):
            raise socket.gaierror("nope")
        monkeypatch.setattr(netguard.socket, "getaddrinfo", boom)
        with pytest.raises(netguard.UnsafeURL):
            netguard.safe_url("https://nope.invalid")


def test_registry_remembers_contacts_past_card_expiry(tmp_path):
    """Addresses that answered are kept (newest first, capped) and survive a reload even when every card expired."""
    from src.community.registry import MAX_CONTACTS
    path = str(tmp_path / "peers.json")
    reg = Registry(path)
    for i in range(MAX_CONTACTS + 5):
        reg.note_contact(f"https://s{i}.example.org", now=1000 + i)
    reg.note_contact("", now=1)  # ignored
    got = reg.contacts()
    assert len(got) == MAX_CONTACTS and got[0] == f"https://s{MAX_CONTACTS + 4}.example.org"
    assert "https://s0.example.org" not in got  # the oldest fell off the cap
    reg.save()
    again = Registry(path)
    again.load(now=time.time() + 10 * MAX_AGE_S)  # long after any card would have expired
    assert again.contacts() == got and again.count() == 0


def test_builtin_seeds_are_listed_and_removable(tmp_path):
    """A fresh station lists the built-in seeds; once the owner saves a list it is used as is, even when empty."""
    from src.community import DEFAULT_SEEDS
    from src.community.manager import CommunityManager

    def manager():
        m = CommunityManager.__new__(CommunityManager)
        m.__dict__.update(data_dir=str(tmp_path), _settings=None, _card=None)
        import threading
        m._lock = threading.RLock()
        return m

    assert manager().settings()["seeds"] == list(DEFAULT_SEEDS)
    (tmp_path / "community.json").write_text(json.dumps({"seeds": ["https://mine.example.org"]}))
    assert manager().settings()["seeds"] == list(DEFAULT_SEEDS) + ["https://mine.example.org"]  # saved before this rule
    (tmp_path / "community.json").write_text(json.dumps({"seeds": [], "seeds_set": True}))
    assert manager().settings()["seeds"] == []
