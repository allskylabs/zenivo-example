"""ADS-B contribution: payload shape, signature, receiver-only guarantee, run conditions (no network)."""
import base64
import hashlib
import json

import pytest

from src.analytics import adsb_contribute as ac
from src.analytics.adsb import AdsbFeed, FT
from src.analytics.adsb_public import PublicAdsbFeed
from src.community import identity as idmod

pytestmark = pytest.mark.skipif(not idmod.availability()[0], reason="cryptography not installed")

NOW = 1_000_000.0


def _receiver(delivering=True):
    feed = AdsbFeed("127.0.0.1", 30003)
    with feed._lock:
        a = feed._entry("ABC123", NOW)
        a.update(callsign="HVN1", speed_kt=400.0, track_deg=90.0, vrate_fpm=-640.0)
        feed._add_fix(a, NOW - 3, 21.1, 105.9, 10000 * FT)
        old = feed._entry("111111", NOW)
        feed._add_fix(old, NOW - 60, 21.0, 105.0, 5000 * FT)   # stale fix: not sent
    feed.status = lambda: {"connected": delivering, "last_message_age_s": 1.0}
    return feed


def _contributor(receiver, sent, enabled=True, base="https://net.example", tmp=None):
    ident = idmod.Identity(str(tmp / "k.pem"))
    c = ac.AdsbContributor(receiver=lambda: receiver, enabled=lambda: enabled, base=lambda: base,
                           identity=lambda: ident, post=lambda u, b, h: sent.append((u, b, h)) or {"accepted": 1},
                           clock=lambda: NOW)
    return c, ident


def test_payload_and_signature(tmp_path):
    sent = []
    c, ident = _contributor(_receiver(), sent, tmp=tmp_path)
    assert c.step() == ac.POST_S
    url, body, h = sent[0]
    assert url == "https://net.example/api/v1/adsb/ingest"
    doc = json.loads(body)
    assert doc["v"] == 1 and doc["now"] == NOW and len(doc["ac"]) == 1
    assert doc["ac"][0] == {"hex": "abc123", "lat": 21.1, "lon": 105.9, "alt_baro": 10000, "seen_pos": 3.0,
                            "flight": "HVN1", "gs": 400.0, "track": 90.0, "baro_rate": -640}
    msg = ac.DOMAIN + f"{h['X-Zenivo-Id']}\n{h['X-Zenivo-Time']}\n{hashlib.sha256(body).hexdigest()}".encode("ascii")
    pub = base64.b64decode(h["X-Zenivo-Key"])
    assert idmod.station_id(pub) == h["X-Zenivo-Id"]
    assert idmod.verify_signature(pub, base64.b64decode(h["X-Zenivo-Sig"]), msg)
    assert c.status()["posts"] == 1 and c.status()["last_sent"] == 1


def test_only_the_receiver_feed_is_ever_sent(tmp_path):
    sent = []
    public = PublicAdsbFeed(station=lambda: (21.0, 105.0))
    with public._lock:
        public._add_fix(public._entry("DEAD01", NOW), NOW - 1, 21.0, 105.0, 3000.0)
    public.status = lambda: {"connected": True, "last_message_age_s": 1.0}
    c, _ = _contributor(public, sent, tmp=tmp_path)
    assert not c.active() and c.step() == ac.POST_S and sent == []


def test_not_running_when_off_silent_or_without_network(tmp_path):
    for kw, rec in (({"enabled": False}, True), ({"base": ""}, True), ({}, False)):
        sent = []
        c, _ = _contributor(_receiver(rec), sent, tmp=tmp_path, **kw)
        c.step()
        assert sent == [] and not c.active()


def test_failure_backs_off_and_honours_retry_after(tmp_path):
    def post(u, b, h):
        raise ac.FetchError("HTTP 429", retry_after_s=120.0)

    c, _ = _contributor(_receiver(), [], tmp=tmp_path)
    c._post = post
    assert c.step() == 120.0 and c.last_error == "HTTP 429"
