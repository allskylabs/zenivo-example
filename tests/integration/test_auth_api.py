"""Admin login through the real app: locked before setup, sign in/out, default deny, rounding for guests, WebSocket gate."""
import pytest

from src.auth.manager import AuthManager
from src.auth.policy import safe_next

PW = "a long enough password"


@pytest.fixture(autouse=True)
def fresh_auth(guest_client, tmp_path):
    """Every test starts with no password and no session on a private data dir."""
    guest_client.service.auth = AuthManager(str(tmp_path))
    guest_client.service.auth.ensure_setup_code()
    guest_client.cookies.clear()
    yield guest_client.service.auth


def setup_admin(c, auth):
    code = open(auth.setup_code_path).read().strip()
    r = c.post("/api/auth/setup", json={"code": code, "password": PW})
    assert r.status_code == 200
    return r


class TestSetupAndLogin:
    def test_locked_before_setup_then_setup_with_code(self, guest_client, fresh_auth):
        c = guest_client
        assert c.get("/api/auth/status").json() == {"configured": False, "admin": False, "setup_pending": True}
        assert c.post("/api/camera/exposure", json={"exposure_s": 1.0}).status_code == 401
        assert c.post("/api/auth/setup", json={"code": "wrong-code", "password": PW}).status_code == 403
        assert c.post("/api/auth/login", json={"password": PW}).status_code == 409
        r = setup_admin(c, fresh_auth)
        assert "HttpOnly" in r.headers["set-cookie"] and "samesite=lax" in r.headers["set-cookie"].lower()
        assert c.get("/api/auth/status").json()["admin"] is True
        assert c.post("/api/auth/setup", json={"code": "x", "password": PW}).status_code == 409

    def test_login_logout_and_wrong_password(self, guest_client, fresh_auth):
        c = guest_client
        setup_admin(c, fresh_auth)
        c.post("/api/auth/logout")
        assert c.get("/api/auth/status").json()["admin"] is False
        assert c.post("/api/auth/login", json={"password": "nope nope nope"}).status_code == 403
        assert c.post("/api/auth/login", json={"password": PW}).status_code == 200
        assert c.get("/api/auth/status").json()["admin"] is True

    def test_throttle_returns_429_with_retry_after(self, guest_client, fresh_auth):
        c = guest_client
        fresh_auth.set_password(PW)
        codes = [c.post("/api/auth/login", json={"password": "bad bad bad bad"}).status_code for _ in range(6)]
        assert codes[:5] == [403] * 5 and codes[5] == 429
        assert int(c.post("/api/auth/login", json={"password": PW}).headers["retry-after"]) > 0

    def test_password_change_signs_out_others(self, guest_client, fresh_auth):
        c = guest_client
        setup_admin(c, fresh_auth)
        other = fresh_auth.create_session()
        assert c.post("/api/auth/password", json={"current": "wrong wrong", "new": PW + "2"}).status_code == 403
        assert c.post("/api/auth/password", json={"current": PW, "new": PW + "2"}).status_code == 200
        assert not fresh_auth.session_valid(other) and c.get("/api/auth/status").json()["admin"] is True


class TestDefaultDeny:
    def test_write_guest_401_admin_200(self, guest_client, fresh_auth):
        c = guest_client
        fresh_auth.set_password(PW)
        r = c.post("/api/obs/reload")
        assert r.status_code == 401 and r.json() == {"detail": "Sign in as admin", "code": "auth"}
        c.post("/api/auth/login", json={"password": PW})
        assert c.post("/api/obs/reload").status_code == 200

    def test_admin_only_reads(self, guest_client, fresh_auth):
        c = guest_client
        for path in ("/api/logs", "/api/logs/download", "/api/community/settings", "/api/community/chat/stats"):
            assert c.get(path).status_code == 401
        assert c.get("/api/status").status_code == 200
        assert c.get("/api/community/peers").status_code == 200

    def test_cross_origin_write_refused(self, guest_client, fresh_auth):
        c = guest_client
        setup_admin(c, fresh_auth)
        r = c.post("/api/obs/reload", headers={"Origin": "https://evil.example"})
        assert r.status_code == 403
        assert c.post("/api/obs/reload", headers={"Origin": "http://testserver"}).status_code == 200

    def test_admin_pages_redirect_guests(self, guest_client):
        r = guest_client.get("/settings", follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == "/login?next=/settings"
        assert guest_client.get("/live", follow_redirects=False).status_code == 200
        assert guest_client.get("/login", follow_redirects=False).status_code == 200

    @pytest.mark.parametrize("path", ["/api/focus", "/api/focus/frame", "/api/storage", "/docs", "/openapi.json"])
    def test_newly_admin_only_gets_refuse_guests(self, guest_client, path):
        assert guest_client.get(path).status_code == 401

    def test_community_announce_stays_open(self, guest_client):
        # reaches the route (sharing is off -> 403 from the route itself, not 401 from the gate)
        r = guest_client.post("/api/community/announce", json={})
        assert r.status_code == 403 and r.json()["detail"] == "Community sharing is off"


class TestGuestLocation:
    def test_rounded_for_guest_exact_for_admin(self, guest_client, fresh_auth):
        c = guest_client
        svc = c.service
        svc.station_config["latitude_deg"], svc.station_config["longitude_deg"] = 21.02851, 105.85421
        g = c.get("/api/station/config").json()
        assert (g["latitude_deg"], g["longitude_deg"], g["location_rounded"]) == (21.0, 105.9, True)
        st = c.get("/api/status").json()["station"]
        assert st["latitude_deg"] == 21.0 and st["location_rounded"] is True
        setup_admin(c, fresh_auth)
        a = c.get("/api/station/config").json()
        assert (a["latitude_deg"], a["longitude_deg"]) == (21.02851, 105.85421) and "location_rounded" not in a
        c.post("/api/community/settings", json={"public_precision_deg": 0.01})
        c.cookies.clear()
        assert c.get("/api/station/config").json()["latitude_deg"] == 21.03

    def test_old_precision_key_is_migrated(self, guest_client):
        mgr = guest_client.service.community
        mgr._settings = None
        import json, os
        with open(os.path.join(mgr.data_dir, "community.json"), "w") as f:
            json.dump({"precision_deg": 1.0}, f)
        assert mgr.settings()["public_precision_deg"] == 1.0
        mgr._settings = None


class TestWebSocketGate:
    def test_guest_cannot_change_state_admin_can(self, guest_client, fresh_auth):
        c = guest_client
        with c.websocket_connect("/ws/live") as ws:
            ws.receive_json()  # welcome
            ws.send_json({"action": "set_exposure", "exposure_s": 1.0})
            assert ws.receive_json() == {"type": "error", "code": "auth", "message": "Sign in as admin"}
            ws.send_json({"action": "ping"})
            assert ws.receive_json()["type"] == "pong"
        setup_admin(c, fresh_auth)
        with c.websocket_connect("/ws/live") as ws:
            ws.receive_json()
            ws.send_json({"action": "set_mode", "mode": "auto"})
            assert ws.receive_json()["type"] == "mode_result"


def test_safe_next_exported():
    assert safe_next("/settings") == "/settings"


class TestDayObjectsConfig:
    """POST /api/day-objects/config: admin only, bounds enforced, persisted and applied live."""

    @pytest.fixture(autouse=True)
    def private_station_config(self, guest_client, tmp_path, monkeypatch):
        """These tests save settings: write them to a temp file, never to the real data/station_config.json."""
        monkeypatch.setattr(guest_client.service, "station_config_path", str(tmp_path / "station_config.json"))

    def test_guest_401_admin_ok_and_bounds(self, guest_client, fresh_auth):
        c = guest_client
        assert c.post("/api/day-objects/config", json={"sensitivity": "high"}).status_code == 401
        setup_admin(c, fresh_auth)
        r = c.post("/api/day-objects/config", json={"sensitivity": "high", "classes": ["bird"], "retention_days": 30})
        assert r.status_code == 200
        cfg = r.json()["config"]
        assert (cfg["sensitivity"], cfg["classes"], cfg["retention_days"]) == ("high", ["bird"], 30)
        assert c.get("/api/day-objects/status").json()["config"]["sensitivity"] == "high"
        assert c.post("/api/day-objects/config", json={"retention_days": 500}).status_code == 422
        assert c.post("/api/day-objects/config", json={"classes": ["dragon"]}).status_code == 422
        assert c.post("/api/day-objects/config", json={"min_contrast_dn": None, "sensitivity": "normal",
                                                       "classes": ["plane", "bird", "insect", "object"]}).status_code == 200

    def test_radiosonde_switch_is_admin_only(self, guest_client, fresh_auth):
        c = guest_client
        assert c.post("/api/adsb", json={"radiosondes_enabled": False}).status_code == 401
        setup_admin(c, fresh_auth)
        assert c.post("/api/adsb", json={"radiosondes_enabled": False}).status_code == 200
        assert c.get("/api/adsb").json()["radiosondes_enabled"] == 0
        assert c.get("/api/radiosondes").json()["enabled"] is False
        c.post("/api/adsb", json={"radiosondes_enabled": True})

    def test_public_fallback_defaults_off_round_trips_and_is_admin_only(self, guest_client, fresh_auth):
        c = guest_client
        assert c.get("/api/adsb").json()["public_fallback"] == 0
        assert c.post("/api/adsb", json={"public_fallback": True}).status_code == 401
        setup_admin(c, fresh_auth)
        r = c.post("/api/adsb", json={"public_fallback": True})
        assert r.status_code == 200 and r.json()["public_fallback"] == 1
        assert c.get("/api/adsb").json()["source_in_use"] in ("receiver", "public", "none")
        assert c.post("/api/adsb", json={"public_fallback": False}).json()["public_fallback"] == 0

    def test_adsb_contribute_defaults_off_round_trips_and_is_admin_only(self, guest_client, fresh_auth):
        c = guest_client
        d = c.get("/api/adsb").json()
        assert d["contribute"] == 0 and d["contribute_status"]["posts"] == 0
        assert c.post("/api/adsb", json={"contribute": True}).status_code == 401
        setup_admin(c, fresh_auth)
        assert c.post("/api/adsb", json={"contribute": True}).json()["contribute"] == 1
        assert c.post("/api/adsb", json={"contribute": False}).json()["contribute"] == 0

    def test_fr24_secrets_are_never_returned(self, guest_client, fresh_auth):
        c = guest_client
        setup_admin(c, fresh_auth)
        assert c.post("/api/adsb", json={"fr24_enabled": True, "fr24_email": "me@example.org", "fr24_token": "SECRET-TOKEN"}).status_code == 200
        body = c.get("/api/adsb").text + c.get("/api/station/config").text
        assert "SECRET-TOKEN" not in body
        assert c.get("/api/adsb").json()["fr24_has_token"] is True
        c.post("/api/auth/logout")
        guest = c.get("/api/adsb").text
        assert "me@example.org" not in guest and "SECRET-TOKEN" not in guest
        c.post("/api/auth/login", json={"password": PW})
        c.post("/api/adsb", json={"fr24_enabled": False, "fr24_token": ""})


def test_fr24_email_must_be_an_email_address(guest_client, fresh_auth):
    """A browser autofilling "admin" into the account field must never be stored or used for a sign-in."""
    setup_admin(guest_client, fresh_auth)
    assert guest_client.post("/api/adsb", json={"fr24_email": "admin", "fr24_password": "x" * 12}).status_code == 422
