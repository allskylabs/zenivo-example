"""New data endpoints through the real app: who may call them and one happy path each."""
import json

import pytest

from src.auth.manager import AuthManager

PW = "a long enough password"
EID = "meteor_20261116_021408_001"


@pytest.fixture(autouse=True)
def fresh_auth(guest_client, tmp_path):
    guest_client.service.auth = AuthManager(str(tmp_path / "auth"))
    guest_client.service.auth.set_password(PW)
    guest_client.cookies.clear()
    guest_client.service.events_dir = str(tmp_path / "events")
    day = tmp_path / "events" / "2026-11-16"
    day.mkdir(parents=True)
    (day / "events.jsonl").write_text(json.dumps({"event_id": EID, "type": "meteor", "timestamp": "2026-11-16T02:14:08+07:00"}) + "\n")
    (day / f"{EID}.jpg").write_bytes(b"x")
    guest_client.service._events_cache = None
    yield guest_client


def sign_in(c):
    assert c.post("/api/auth/login", json={"password": PW}).status_code == 200


def test_delete_event_is_admin_only(guest_client):
    c = guest_client
    assert c.get(f"/api/events/{EID}").json()["type"] == "meteor"          # a shared link works for guests
    assert c.delete(f"/api/events/{EID}").status_code == 401
    sign_in(c)
    assert c.delete("/api/events/meteor_20261116_021408_999").status_code == 404
    assert c.delete(f"/api/events/{EID}").json() == {"deleted": EID}
    assert c.get(f"/api/events/{EID}").status_code == 404


def test_system_resources_admin_only(guest_client):
    c = guest_client
    assert c.get("/api/system/resources").status_code == 401
    sign_in(c)
    d = c.get("/api/system/resources").json()
    assert set(d) >= {"cpu_pct", "ram_pct", "disk_pct", "temp_c"} and 0 <= d["disk_pct"] <= 100


def test_sky_riseset_public_by_body_and_by_position(guest_client):
    c = guest_client
    sun = c.get("/api/sky/riseset?body=sun").json()
    assert sun["name"] == "Sun" and sun["state"] in ("up", "down", "always_up", "never_rises")
    fixed = c.get("/api/sky/riseset?ra=101.29&dec=-16.72").json()
    assert "alt" in fixed and "state" in fixed
    assert c.get("/api/sky/riseset").status_code == 400
    assert c.get("/api/sky/riseset?body=pluto").status_code == 404
    assert [b["name"] for b in c.get("/api/sky/bodies").json()["bodies"]][:2] == ["Sun", "Moon"]


class _FakeInfo:
    def stop(self):
        pass

    def describe(self, icao, callsign):
        return {"type": "A321", "route": "VVNB-VVTS"}


class _FakeAirports:   # the real ones download 17 MB in the background
    def get(self, lat, lon):
        return {"ready": True, "airports": [{"ident": "VVNB", "iata": "HAN", "name": "Noi Bai", "type": "large", "lat": lat, "lon": lon, "elev_m": 0}], "runways": []}


def test_aircraft_info_and_airports_frame_are_public_and_validated(guest_client):
    c = guest_client
    c.service.aircraft_info, c.service.airports = _FakeInfo(), _FakeAirports()
    c.service.get_aircraft_now = lambda **kw: {"aircraft": [{"icao": "48d3a1"}]}
    assert c.get("/api/adsb/info/48d3a1?callsign=VJC123").json() == {"type": "A321", "route": "VVNB-VVTS"}
    assert c.get("/api/adsb/info/aaaaaa").json() == {}   # not heard now: no outside lookup
    assert c.get("/api/adsb/info/zzzz").status_code == 422
    assert "airports" in c.get("/api/map/airports/frame").json()
