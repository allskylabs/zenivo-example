"""Community discovery: seeds + auto_discovery switch + seed status (no network, no worker thread)."""
import pytest

pytest.importorskip("cryptography")

from src.community import DEFAULT_SEEDS
from src.community.manager import CommunityManager


@pytest.fixture
def mgr(tmp_path):
    return CommunityManager(str(tmp_path), lambda: {"name": "S", "lat": 1.0, "lon": 2.0, "camera": None},
                            lambda: None, lambda: b"")


def _known(mgr, urls):
    mgr._ensure()
    for u in urls:
        mgr.note_contact(u)


def test_auto_discovery_defaults_on(mgr):
    assert mgr.settings()["auto_discovery"] is True
    assert mgr.status()["auto_discovery"] is True


def test_off_asks_only_the_seed_list(mgr):
    mgr.settings()
    _known(mgr, ["https://a.example.org", "https://b.example.org", "https://c.example.org"])
    mgr._settings = dict(mgr.settings(), seeds=["https://seed.example.org"])
    urls = [u for u, _ in mgr.worker.targets()]
    assert urls[0] == "https://seed.example.org" and len(urls) > 1       # on: seeds + random known peers
    mgr._settings["auto_discovery"] = False
    assert [u for u, _ in mgr.worker.targets()] == ["https://seed.example.org"]


def test_seed_status_ok_down_pending(mgr):
    mgr._ensure()
    mgr._settings = dict(mgr.settings(), seeds=list(DEFAULT_SEEDS) + ["https://mine.example.org"])
    mgr.worker.health[DEFAULT_SEEDS[0]] = {"ok": 3, "failed": 0, "streak": 0, "last_ok": 1000}
    mgr.worker.health[DEFAULT_SEEDS[1]] = {"ok": 1, "failed": 4, "streak": 4, "last_ok": 500}
    rows = {r["url"]: r for r in mgr.seed_status()}
    assert rows[DEFAULT_SEEDS[0]] == {"url": DEFAULT_SEEDS[0], "default": True, "state": "ok", "last_ok": 1000}
    assert rows[DEFAULT_SEEDS[1]]["state"] == "down"
    assert rows["https://mine.example.org"]["state"] == "pending" and not rows["https://mine.example.org"]["default"]


def test_registry_cap_is_200():
    from src.community.registry import MAX_CARDS
    assert MAX_CARDS == 200
