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
        ("updated", time.time() + 3600), ("updated", time.time() + 300), ("updated", -1),
        ("url", "https://sky.example.org/gallery"), ("url", "https://sky.example.org?x=1"), ("url", "https://sky.example.org#f"),
    ])
    def test_invalid_fields_rejected(self, ident, field, value):
        card = make(ident)
        card[field] = value
        with pytest.raises(CardError):
            verify(resign(ident, card))

    @pytest.mark.parametrize("field", ["lat", "lon", "updated"])
    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_numbers_rejected(self, ident, field, bad):
        card = make(ident)
        card[field] = bad  # cannot even be signed (canonical forbids NaN); an attacker's unsigned card is refused too
        with pytest.raises(CardError):
            verify(card)

    @pytest.mark.parametrize("typed,origin", [
        ("https://Sky.Example.org/", "https://sky.example.org"), ("https://sky.example.org/a/b?x=1#f", "https://sky.example.org"),
        ("https://sky.example.org:8443", "https://sky.example.org:8443"), ("https://sky.example.org:443", "https://sky.example.org")])
    def test_url_is_an_origin(self, ident, typed, origin):
        assert make(ident, url=typed)["url"] == origin  # the owner's path/slash is stripped, not an error
        assert cardmod.seed_origin(typed.replace("https", "http")).startswith("http://sky.example.org")

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

    def test_cap_keeps_known_and_refreshed_ids(self, tmp_path):
        """A flood of new ids cannot push out stations that have been known longer or that refreshed their card."""
        reg = Registry(str(tmp_path / "p.json"))
        t0 = time.time() - 5000
        reg.merge(fake("old", t0 - 100), t0)
        reg.merge(fake("old", t0 - 50), t0 + 1)                  # refreshed once
        for i in range(MAX_CARDS + 5):
            reg.merge(fake(f"s{i}", t0), t0 + 10 + i)            # first seen later and later
        now = t0 + 1000
        assert reg.count() == MAX_CARDS
        ids = {c["id"] for c in reg.cards(now)}
        assert "old" in ids and "s0" in ids and f"s{MAX_CARDS + 4}" not in ids  # newest-first-seen are dropped first
        reg.save()
        assert json.loads((tmp_path / "p.json").read_text())["first_seen"]["old"] == int(t0)  # local metadata persisted

    def test_new_id_from_the_future_refused(self, tmp_path):
        reg = Registry(str(tmp_path / "p.json"))
        now = time.time()
        assert not reg.merge(fake("f", now + 90), now) and reg.merge(fake("f", now + 30), now)

    def test_save_is_rate_limited_and_rejects_nan(self, tmp_path):
        reg = Registry(str(tmp_path / "p.json"))
        reg.merge(fake("a", 100), 100.0)
        reg.save(force=False, now=1000.0)
        assert (tmp_path / "p.json").exists()
        (tmp_path / "p.json").unlink()
        reg.merge(fake("b", 200), 200.0)
        reg.save(force=False, now=1010.0)                        # less than 30 s since the last write: skipped
        assert not (tmp_path / "p.json").exists()
        reg.save(force=False, now=1031.0)
        assert (tmp_path / "p.json").exists()
        (tmp_path / "p.json").write_text('{"cards":[{"id":"x","updated":NaN}]}')
        loaded = Registry(str(tmp_path / "p.json"))
        loaded.load()
        assert loaded.count() == 0  # NaN token: the file is ignored, no crash


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
    """Origins that answered are kept (newest first, capped, 2 per host suffix, 180 days) and survive a reload."""
    from src.community.registry import CONTACT_MAX_AGE_S, MAX_CONTACTS
    path = str(tmp_path / "peers.json")
    reg = Registry(path)
    now = time.time()
    for i in range(MAX_CONTACTS + 5):
        reg.note_contact(f"https://s.site{i}.example/x/", now=now - 1000 + i)  # a path is reduced to the origin
    reg.note_contact("", now=now)  # ignored
    got = reg.contacts(now)
    assert len(got) == MAX_CONTACTS and got[0] == f"https://s.site{MAX_CONTACTS + 4}.example"
    assert "https://s.site0.example" not in got  # the oldest fell off the cap
    for i in range(4):  # one domain cannot fill the list: only 2 per registrable suffix
        reg.note_contact(f"https://n{i}.flood.test", now=now)
    assert sum(1 for u in reg.contacts(now) if u.endswith("flood.test")) == 2
    reg.save()
    again = Registry(path)
    again.load(now=now + 5 * MAX_AGE_S)  # 150 days on: every card expired, contacts last 180 days
    assert again.contacts(now + 5 * MAX_AGE_S) != [] and again.count() == 0
    assert again.contacts(now + CONTACT_MAX_AGE_S + 2000) == []  # then they expire too


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


def test_card_visitor_permissions(ident):
    """open/preview flags are only published for a station with a URL, are signed, and must be booleans."""
    kw = dict(name="S", lat=1.0, lon=2.0, precision_deg=0.1, camera=None, software="Zenivo test")
    plain = build_card(ident, url="https://s.example.org", **kw)
    assert "open" not in plain and "preview" not in plain  # defaults: link yes, large preview no
    card = build_card(ident, url="https://s.example.org", allow_open=False, preview=True, **kw)
    assert card["open"] is False and card["preview"] is True and verify(card)
    assert "open" not in build_card(ident, url=None, allow_open=False, preview=True, **kw)
    with pytest.raises(CardError):
        verify({**card, "open": True})  # flipping a flag breaks the signature
    with pytest.raises(CardError):
        verify({**card, "preview": "yes"})


class TestNetguardAddresses:
    @pytest.mark.parametrize("ip,public", [
        ("93.184.216.34", True), ("2606:4700:4700::1111", True), ("::ffff:93.184.216.34", True),
        ("::ffff:10.0.0.1", False), ("::10.0.0.1", False), ("::127.0.0.1", False),
        ("64:ff9b::a00:1", False), ("64:ff9b::5db8:d822", False), ("64:ff9b:1::1", False),
        ("2002:c000:204::1", False), ("2001:0:4136:e378:8000:63bf:3fff:fdd2", False),
        ("fd00::1", False), ("fe80::1", False)])
    def test_public(self, ip, public):
        import ipaddress
        assert netguard._public(ipaddress.ip_address(ip)) is public

    def test_nan_tokens_refused(self):
        with pytest.raises(ValueError):
            json.loads('{"a":NaN}', parse_constant=netguard._no_constant)


class _Mgr:
    """Just enough manager for GossipWorker.run_round."""

    def __init__(self, own):
        self.own, self.merged, self.contacts_noted = own, [], []

    def own_card(self): return self.own
    def settings(self): return {"seeds": ["https://peer.example.org/ignored/path"], "public_url": ""}
    def registry_cards(self): return []
    def contacts(self): return []
    def merge_card(self, c): self.merged.append(c["id"]); return True
    def note_contact(self, u): self.contacts_noted.append(u)
    def save_registry(self): pass
    def relay_sweep(self): return 0
    def relay_wanted(self): return False


def test_round_counts_peers_that_know_us(tmp_path, ident):
    """Every peer that knows us returns our card too; only a reply that STARTS with our card is ourselves."""
    from src.community.gossip import GossipWorker
    other = Identity(str(tmp_path / "o.pem"))
    mine, theirs = make(ident), make(other, url="https://peer.example.org")
    urls = []

    def knows_us(url, **kw):
        urls.append(url)
        return {"cards": [theirs, mine]} if url.endswith("/peers") else {"ok": True}

    w = GossipWorker(_Mgr(mine), fetch=knows_us)
    r = w.run_round()
    assert r["others_ok"] == 1 and w.last_targets[0]["self"] is False
    assert urls[0] == "https://peer.example.org/api/community/peers"  # origin only, the typed path is gone
    w2 = GossipWorker(_Mgr(mine), fetch=lambda url, **kw: {"cards": [mine, theirs]})
    assert w2.run_round()["others_ok"] == 0 and w2.last_targets[0]["self"] is True  # we dialled ourselves
