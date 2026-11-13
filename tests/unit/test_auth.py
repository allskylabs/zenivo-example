"""Auth building blocks: password hash, sessions, throttle, who-may-call-what table, guest rounding."""
import os
import stat

import pytest

from src.auth import policy
from src.auth.manager import AuthManager, PasswordError, Throttle, MAX_SESSIONS, SESSION_TTL_S
from src.auth.rounding import guest_view, snap

PW = "correct horse battery"


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def auth(tmp_path):
    clock = Clock()
    a = AuthManager(str(tmp_path), clock=clock)
    a.clock = clock
    return a


class TestPassword:
    def test_roundtrip_and_wrong_password(self, auth):
        assert not auth.configured and not auth.verify_password(PW)
        auth.set_password(PW)
        assert auth.configured and auth.verify_password(PW)
        assert not auth.verify_password(PW + "x") and not auth.verify_password("")
        assert stat.S_IMODE(os.stat(auth.admin_path).st_mode) == 0o600

    @pytest.mark.parametrize("bad", ["short", "x" * 257, None])
    def test_length_rules(self, auth, bad):
        with pytest.raises(PasswordError):
            auth.set_password(bad)

    def test_setup_code_is_one_time(self, auth):
        auth.ensure_setup_code()
        code = open(auth.setup_code_path).read().strip()
        assert auth.check_setup_code(code) and not auth.check_setup_code("nope")
        token = auth.finish_setup(PW)
        assert not os.path.exists(auth.setup_code_path) and auth.session_valid(token)

    def test_reset_forgets_everything(self, auth):
        token = auth.finish_setup(PW)
        auth.reset()
        assert not auth.configured and not auth.session_valid(token)


class TestSessions:
    def test_create_verify_expire_sliding(self, auth):
        auth.set_password(PW)
        token = auth.create_session()
        assert auth.session_valid(token) and not auth.session_valid("other")
        assert PW not in open(auth.sessions_path).read() and token not in open(auth.sessions_path).read()
        auth.clock.t += SESSION_TTL_S - 10
        assert auth.session_valid(token)          # slides the expiry forward
        auth.clock.t += SESSION_TTL_S - 10
        assert auth.session_valid(token)
        auth.clock.t += SESSION_TTL_S + 10
        assert not auth.session_valid(token)

    def test_survives_restart_and_password_change_clears(self, auth, tmp_path):
        auth.set_password(PW)
        token = auth.create_session()
        assert AuthManager(str(tmp_path), clock=auth.clock).session_valid(token)
        auth.set_password(PW + "2")
        assert not auth.session_valid(token)

    def test_max_sessions_and_logout(self, auth):
        auth.set_password(PW)
        tokens = []
        for _ in range(MAX_SESSIONS + 3):
            auth.clock.t += 1
            tokens.append(auth.create_session())
        assert not auth.session_valid(tokens[0]) and auth.session_valid(tokens[-1])
        auth.revoke_session(tokens[-1])
        assert not auth.session_valid(tokens[-1])
        auth.clear_sessions()
        assert not auth.session_valid(tokens[-2])


class TestThrottle:
    def test_locks_after_five_and_doubles(self):
        clock = Clock()
        th = Throttle(clock=clock)
        for _ in range(4):
            th.fail("a")
        assert th.retry_after("a") == 0
        th.fail("a")
        assert 59 <= th.retry_after("a") <= 61 and th.retry_after("b") == 0
        clock.t += 61
        assert th.retry_after("a") == 0
        for _ in range(5):
            th.fail("a")
        assert 119 <= th.retry_after("a") <= 121       # second lockout: 120 s
        th.ok("a")
        assert th.retry_after("a") == 0


class TestPolicy:
    @pytest.mark.parametrize("method,path,expect", [
        ("GET", "/api/status", policy.PUBLIC),
        ("GET", "/api/station/config", policy.PUBLIC),
        ("OPTIONS", "/api/camera/exposure", policy.PUBLIC),
        ("GET", "/api/logs", policy.ADMIN),
        ("GET", "/api/logs/download", policy.ADMIN),
        ("GET", "/api/community/settings", policy.ADMIN),
        ("GET", "/settings", policy.PAGE),
        ("GET", "/logs", policy.PAGE),
        ("GET", "/live", policy.PUBLIC),
        ("POST", "/api/camera/exposure", policy.ADMIN),
        ("PUT", "/api/storage/policy", policy.ADMIN),
        ("DELETE", "/api/anything", policy.ADMIN),
        ("POST", "/api/auth/login", policy.PUBLIC),
        ("POST", "/api/auth/setup", policy.PUBLIC),
        ("POST", "/api/auth/logout", policy.PUBLIC),
        ("POST", "/api/auth/password", policy.ADMIN),
        ("POST", "/api/auth/logout-all", policy.ADMIN),
        ("POST", "/api/community/announce", policy.PUBLIC),
        ("POST", "/api/adsb", policy.ADMIN),
    ])
    def test_classify(self, method, path, expect):
        assert policy.classify(method, path, {}, "203.0.113.5") == expect

    def test_ingest_only_from_a_local_process(self, monkeypatch):
        monkeypatch.setattr(policy, "LOCAL_INGEST", frozenset({"/api/ingest/x"}))
        assert policy.classify("POST", "/api/ingest/x", {}, "127.0.0.1") == policy.PUBLIC
        assert policy.classify("POST", "/api/ingest/x", {}, "192.168.1.9") == policy.ADMIN
        for h in policy.PROXY_HEADERS:  # tunnelled request: peer is loopback but the client is not
            assert policy.classify("POST", "/api/ingest/x", {h: "1.2.3.4"}, "127.0.0.1") == policy.ADMIN

    @pytest.mark.parametrize("headers,ok", [
        ({}, True),
        ({"origin": "https://a.example", "host": "a.example"}, True),
        ({"origin": "http://a.example:8080", "host": "a.example:8080"}, True),
        ({"origin": "https://evil.example", "host": "a.example"}, False),
        ({"origin": "null", "host": "a.example"}, False),
    ])
    def test_origin_check(self, headers, ok):
        assert policy.origin_ok(headers) is ok

    @pytest.mark.parametrize("value,expect", [
        ("/settings", "/settings"), ("/gallery?x=1", "/gallery?x=1"), ("//evil.example", "/"), ("https://evil.example", "/"),
        ("/\\evil", "/"), ("", "/"), (None, "/"), ("/login", "/"), ("/a\nb", "/")])
    def test_safe_next(self, value, expect):
        assert policy.safe_next(value) == expect

    def test_client_key_and_cookie(self):
        assert policy.client_key({"cf-connecting-ip": "1.1.1.1", "x-forwarded-for": "2.2.2.2"}, "127.0.0.1") == "1.1.1.1"
        assert policy.client_key({"x-forwarded-for": "2.2.2.2, 3.3.3.3"}, "127.0.0.1") == "2.2.2.2"
        assert policy.client_key({}, "10.0.0.5") == "10.0.0.5"
        assert policy.session_token({"cookie": "a=b; zenivo_session=tok"}) == "tok"
        assert policy.session_token({}) is None


class TestGuestRounding:
    @pytest.mark.parametrize("value,prec,expect", [
        (21.0285, 0.1, 21.0), (105.8542, 0.1, 105.9), (21.0285, 0.01, 21.03), (105.8542, 1.0, 106.0),
        (21.02851, 0, 21.02851), (-33.8688, 0.1, -33.9)])
    def test_snap(self, value, prec, expect):
        assert snap(value, prec) == pytest.approx(expect)

    def test_guest_view_rounds_station_and_strips_secrets(self):
        payload = {"station": {"latitude_deg": 21.0285, "longitude_deg": 105.8542, "elevation_m": 20,
                               "adsb": {"host": "192.168.2.48", "fr24_token": "t", "az_offset_deg": 1.5}},
                   "satellites": {"station": {"lat": 21.0285, "lon": 105.8542}},
                   "aircraft": [{"lat": 21.0285, "lon": 105.8542}]}
        out = guest_view(payload, 0.1)
        assert out["station"]["latitude_deg"] == 21.0 and out["station"]["longitude_deg"] == 105.9
        assert out["station"]["location_rounded"] is True and out["station"]["elevation_m"] == 20
        assert out["station"]["adsb"] == {"az_offset_deg": 1.5}
        assert out["satellites"]["station"]["lat"] == 21.0 and out["satellites"]["station"]["location_rounded"] is True
        assert out["aircraft"][0]["lat"] == 21.0285          # other objects' positions are not the station's
        assert payload["station"]["latitude_deg"] == 21.0285  # the original is untouched

    def test_exact_precision_adds_no_flag(self):
        out = guest_view({"latitude_deg": 21.0285, "longitude_deg": 105.8542}, 0)
        assert out == {"latitude_deg": 21.0285, "longitude_deg": 105.8542}
