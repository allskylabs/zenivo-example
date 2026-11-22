"""Chat primitives: signed messages, end-to-end encrypted DMs, key records, signed fetch, hub store caps."""
import copy
import os

import pytest

pytest.importorskip("cryptography")

from src.community import chat
from src.community.identity import Identity


@pytest.fixture()
def alice(tmp_path):
    return Identity(str(tmp_path / "alice.pem"))


@pytest.fixture()
def bob(tmp_path):
    return Identity(str(tmp_path / "bob.pem"))


def pub(ident):
    return ident.pubkey if isinstance(ident.pubkey, str) else __import__("base64").b64encode(ident.pubkey).decode()


class TestMessages:
    def test_public_message_verifies_and_tamper_fails(self, alice, bob):
        msg = chat.make_public(alice, "hello\nworld\x07")
        assert msg["body"] == "hello world"                      # control characters stripped
        assert chat.verify_message(msg, pub(alice)) is msg
        forged = copy.deepcopy(msg)
        forged["body"] = "hello?"
        with pytest.raises(chat.ChatError):
            chat.verify_message(forged, pub(alice))              # body changed: mid no longer matches
        with pytest.raises(chat.ChatError):
            chat.verify_message(msg, pub(bob))                   # key of another station

    def test_validation_rejects_bad_fields(self, alice):
        msg = chat.make_public(alice, "hi")
        for mutate in (lambda m: m.update(extra=1), lambda m: m.update(t=float("nan")), lambda m: m.update(t=10 ** 15),
                       lambda m: m.update(to="not-an-id"), lambda m: m.update(to=m["from"]), lambda m: m.update(v=2),
                       lambda m: m.update(body="x" * 501)):
            bad = copy.deepcopy(msg)
            mutate(bad)
            with pytest.raises(chat.ChatError):
                chat.check_message(bad)
        with pytest.raises(chat.ChatError):
            chat.clean_text("   \n ")
        assert len(chat.clean_text("a" * 900)) == 500
        old = chat.make_public(alice, "old", t=1_000_000)
        with pytest.raises(chat.ChatError):
            chat.check_skew(old)

    def test_direct_message_is_private_to_the_recipient(self, alice, bob, tmp_path):
        key_b = chat.ChatKey(str(tmp_path / "b.key"))
        assert oct(os.stat(key_b.path).st_mode & 0o777) == "0o600"
        rec = chat.make_key_record(bob, key_b)
        assert chat.verify_key_record(rec, pub(bob)) is rec
        msg = chat.make_dm(alice, bob.id, rec["xpub"], "secret plan")
        chat.verify_message(msg, pub(alice))
        assert "secret" not in str(msg)
        assert chat.open_dm(msg, key_b) == "secret plan"
        with pytest.raises(chat.ChatError):
            chat.open_dm(msg, chat.ChatKey(str(tmp_path / "other.key")))          # wrong key
        moved = copy.deepcopy(msg)
        moved["t"] += 1                                                           # header is the AEAD associated data
        with pytest.raises(chat.ChatError):
            chat.open_dm(moved, key_b)
        forged = copy.deepcopy(rec)
        forged["xpub"] = chat._b64(bytes(32))
        with pytest.raises(chat.ChatError):
            chat.verify_key_record(forged, pub(bob))

    def test_signed_fetch_headers(self, alice, bob):
        h = chat.sign_fetch(alice)
        assert chat.verify_fetch(pub(alice), alice.id, h["X-Zenivo-Time"], h["X-Zenivo-Sig"])
        assert not chat.verify_fetch(pub(bob), alice.id, h["X-Zenivo-Time"], h["X-Zenivo-Sig"])
        assert not chat.verify_fetch(pub(alice), alice.id, h["X-Zenivo-Time"], h["X-Zenivo-Sig"], now=10 ** 9)  # stale
        assert not chat.verify_fetch(pub(alice), alice.id, None, None)


class TestStore:
    def test_dedupe_expiry_caps_and_cursor(self, alice, bob, tmp_path, monkeypatch):
        store = chat.ChatStore(str(tmp_path / "c.json"))
        m1 = chat.make_public(alice, "one")
        assert store.add(m1) and not store.add(m1)                                # duplicate mid
        assert not store.add(chat.make_public(alice, "stale", t=1_000_000))       # older than 7 days
        monkeypatch.setattr(chat, "PUBLIC_CAP", 3)
        for i in range(5):
            store.add(chat.make_public(alice, f"m{i}"))
        assert store.counts()["public"] == 3
        monkeypatch.setattr(chat, "DM_CAP", 2)
        for i in range(4):
            store.add(chat.make_dm(alice, bob.id, chat._b64(bytes(range(32))), f"dm{i}"))
        assert store.counts()["dm"] == 2
        msgs, nxt, more = store.fetch(0.0)
        assert len(msgs) == 3 and not more and all(m["to"] == "*" for m in msgs)  # no DM without the recipient
        assert len(store.fetch(0.0, dm_for=bob.id)[0]) == 5
        assert store.fetch(nxt)[0] == []                                          # cursor: nothing new
        assert len(store.fetch(0.0, limit=2)[0]) == 2 and store.fetch(0.0, limit=2)[2]
        assert store.fetch(0.0, hidden=lambda i: i == alice.id)[0] == []
        store.save()
        assert chat.ChatStore(str(tmp_path / "c.json")).counts()["public"] == 3   # survives a restart
        store.clear()
        assert not os.path.exists(str(tmp_path / "c.json")) and store.counts()["public"] == 0


class TestForwarding:
    def test_hops_limit_and_signature_survives(self, alice, bob, tmp_path):
        store = chat.ChatStore(str(tmp_path / "h.json"))
        xp = chat._b64(bytes(range(32)))
        ok = chat.make_public(alice, "hop")
        assert store.add(ok, hops=3) and not store.add(chat.make_public(alice, "x"), hops=chat.MAX_HOPS + 1)
        assert store.add(chat.make_dm(alice, bob.id, xp, "fwd"), hops=chat.MAX_HOPS)   # kept, never handed on
        out = []
        msgs, _, _ = store.fetch(0.0, all_dm=True, hops_out=out)
        assert [m["mid"] for m in msgs] == [ok["mid"]] and out == [3]
        chat.verify_message(msgs[0], pub(alice))                       # the hop counter is not signed content
        assert "h" not in msgs[0]
        assert len(store.fetch(0.0, dm_for=bob.id)[0]) == 2            # the recipient itself still gets it

    def test_global_dm_cap_and_hub_pull_signature(self, alice, bob, tmp_path, monkeypatch):
        monkeypatch.setattr(chat, "DM_GLOBAL_CAP", 3)
        store = chat.ChatStore(str(tmp_path / "g.json"))
        xp = chat._b64(bytes(range(32)))
        for i in range(5):
            store.add(chat.make_dm(alice, bob.id, xp, f"d{i}"))
        assert store.dm_stats()[0] == 3
        h = chat.sign_fetch(alice, hub=True)
        t = h["X-Zenivo-Time"]
        assert chat.verify_fetch(pub(alice), alice.id, t, h["X-Zenivo-Sig"], hub=True)
        assert not chat.verify_fetch(pub(alice), alice.id, t, h["X-Zenivo-Sig"])   # the domains do not mix


class TestStats:
    def test_counters_days_peers_and_persistence(self, tmp_path):
        from src.community.chatstats import ChatStats
        p = str(tmp_path / "s.json")
        s = ChatStats(p)
        s.inc("sent_dm", 2, now=86400 * 100)
        s.inc("bogus")
        s.inc("recv_public", now=86400 * 200)
        s.peer("https://h.example", "pulled", 3, ok=True, now=5.0)
        snap = s.snapshot()
        assert snap["totals"]["sent_dm"] == 2 and snap["totals"]["recv_public"] == 1
        assert len(snap["days"]) == 2 and snap["peers"]["https://h.example"]["pulled"] == 3
        for d in range(40):
            s.inc("send_fail", now=86400 * (300 + d))
        assert len(s.snapshot()["days"]) == 30
        s.save(force=True)
        assert ChatStats(p).snapshot()["totals"]["send_fail"] == 40
