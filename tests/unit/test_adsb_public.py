"""Public ADS-B fallback: readsb conversion, source selection, provider failover and backoff (no network)."""

from src.analytics import adsb_public as ap
from src.analytics.adsb import FT


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def _feed(fetch, clock, station=(21.0285, 105.8542)):
    return ap.PublicAdsbFeed(station=lambda: station, fetch=fetch, clock=clock)


def test_readsb_conversion_handles_ground_and_missing_fields():
    payload = {"ac": [
        {"hex": "abc123", "flight": "HVN123  ", "lat": 21.1, "lon": 105.9, "alt_baro": 10000, "gs": 400.0,
         "track": 90.0, "baro_rate": -640, "seen_pos": 2.5},
        {"hex": "4840d6", "lat": 21.2, "lon": 105.8, "alt_baro": "ground"},
        {"hex": "~ffffff", "lat": 21.0, "lon": 105.0, "alt_baro": 100},   # non-ICAO address
        {"hex": "123456", "alt_baro": 5000},                              # no position
        {"hex": "654321", "lat": 21.3, "lon": 105.7, "alt_geom": 2000},   # geometric altitude only
    ]}
    recs = {r["icao"]: r for r in ap.parse_readsb(payload, 100.0)}
    assert set(recs) == {"ABC123", "4840D6", "654321"}
    a = recs["ABC123"]
    assert a["callsign"] == "HVN123" and abs(a["alt_m"] - 10000 * FT) < 1e-6 and a["t"] == 97.5
    assert (a["speed_kt"], a["track_deg"], a["vrate_fpm"]) == (400.0, 90.0, -640.0)
    assert recs["4840D6"]["alt_m"] == 0.0 and "speed_kt" not in recs["4840D6"]
    assert abs(recs["654321"]["alt_m"] - 2000 * FT) < 1e-6
    assert ap.parse_readsb("garbage", 0.0) == [] and ap.parse_readsb({"ac": None}, 0.0) == []


def test_source_selection():
    ok = {"connected": True, "last_message_age_s": 5.0}
    assert ap.choose_source(ok, True) == "receiver"
    assert ap.choose_source({"connected": True, "last_message_age_s": 91.0}, True) == "public"   # silent
    assert ap.choose_source({"connected": False, "last_message_age_s": 1.0}, True) == "public"   # not connected
    assert ap.choose_source({"connected": True, "last_message_age_s": None}, True) == "public"    # nothing yet
    assert ap.choose_source(None, True) == "public"                                              # no receiver configured
    assert ap.choose_source(None, False) == "none" and ap.choose_source({"connected": False}, False) == "none"
    assert ap.choose_source(ok, False) == "receiver"


def test_step_ingests_with_snapped_position_and_no_duplicate_fixes():
    urls, clock = [], Clock()
    body = {"ac": [{"hex": "ABC123", "flight": "X1", "lat": 21.1, "lon": 105.9, "alt_baro": 3000, "gs": 300,
                    "track": 0, "seen_pos": 1.0}]}
    feed = _feed(lambda u: urls.append(u) or body, clock)
    assert feed.step() == ap.POLL_S
    assert urls[0] == "https://opendata.adsb.fi/api/v3/lat/21.0/lon/106.0/dist/150"
    feed.step()   # same fix again (seen_pos unchanged relative to a later clock would move; same clock = same t)
    assert len(feed.tracks(0, 2000)[0]["pos"]) == 1
    cur = feed.current(at=clock.t)
    assert cur[0]["callsign"] == "X1" and feed.status()["connected"] and feed.status()["aircraft"] == 1


def test_failover_to_adsb_lol_return_and_backoff():
    clock, calls = Clock(), []
    state = {"fi_down": True}

    def fetch(url):
        calls.append(url)
        if "adsb.fi" in url and state["fi_down"]:
            raise ap.FetchError("HTTP 503")
        return {"ac": []}

    feed = _feed(fetch, clock)
    assert feed.step() == 10.0 and feed.step() == 20.0           # exponential backoff
    assert feed.step() == 0.0 and feed.provider["name"] == "adsb.lol"   # third failure: switch at once
    assert feed.step() == ap.LOL_POLL_S and calls[-1].startswith("https://api.adsb.lol/v2/point/21.0/106.0/150")
    clock.t += ap.RETURN_AFTER_S
    state["fi_down"] = False
    assert feed.step() == ap.POLL_S and feed.provider["name"] == "adsb.fi" and feed.last_error is None


def test_retry_after_and_backoff_cap():
    clock = Clock()

    def limited(url):
        raise ap.FetchError("HTTP 429", retry_after_s=120.0)

    feed = _feed(limited, clock)
    assert feed.step() == 120.0   # Retry-After wins over the 10 s backoff
    feed2 = _feed(lambda u: (_ for _ in ()).throw(OSError("down")), clock)
    feed2._provider, feed2._provider_since, feed2._fails = 1, clock.t, 6
    assert feed2.step() == ap.BACKOFF_MAX_S


def test_inactive_without_station_or_need():
    clock = Clock()
    assert not _feed(lambda u: {}, clock, station=(0.0, 0.0)).active()
    f = ap.PublicAdsbFeed(station=lambda: (21.0, 105.0), needed=lambda: False)
    assert not f.active()


def test_zenivo_network_is_first_and_fails_over_on_errors_or_emptiness():
    clock, calls, mode = Clock(), [], {"zen": "ok"}
    row = {"hex": "ABC123", "lat": 21.1, "lon": 105.9, "alt_baro": 3000}

    def fetch(url):
        calls.append(url)
        if "/api/v1/adsb/point/" in url:
            if mode["zen"] == "down":
                raise ap.FetchError("HTTP 503")
            return {"ac": [row] if mode["zen"] == "ok" else []}
        return {"ac": [row]}

    feed = ap.PublicAdsbFeed(station=lambda: (21.0285, 105.8542), fetch=fetch, clock=clock,
                             network=lambda: "https://net.example")
    assert feed.step() == ap.POLL_S
    assert calls[0] == "https://net.example/api/v1/adsb/point/21.0/106.0/150"
    assert feed.provider == {"name": "Zenivo network", "link": "https://net.example"}
    # trigger 1: three failures in a row, then adsb.fi at once
    mode["zen"] = "down"
    assert feed.step() == 10.0 and feed.step() == 20.0 and feed.step() == 0.0
    assert feed.provider["name"] == "adsb.fi"
    # the first provider is tried again after RETURN_AFTER_S
    mode["zen"] = "ok"
    clock.t += ap.RETURN_AFTER_S
    feed.step()
    assert feed.provider["name"] == "Zenivo network"
    # trigger 2: an empty network for 2 minutes
    mode["zen"] = "empty"
    feed.step()
    clock.t += ap.EMPTY_FAILOVER_S - 1
    feed.step()
    assert feed.provider["name"] == "Zenivo network"
    clock.t += 1
    assert feed.step() == 0.0 and feed.provider["name"] == "adsb.fi"
    # without a network base the chain is adsb.fi, adsb.lol only
    assert ap.network_base({"remote_solver_url": "https://net.example/solve/x"}) == "https://net.example"
    assert ap.network_base({"remote_solver_url": ""}) == "" and ap.network_base({}) == ""
    assert _feed(lambda u: {"ac": []}, Clock()).provider["name"] == "adsb.fi"
