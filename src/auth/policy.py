"""Who may call what: ONE classification function used by the HTTP middleware, plus the small helpers around it
(CSRF origin check, local-process test for machine ingest, safe `next` redirects, session cookie parsing)."""
from __future__ import annotations

from http.cookies import SimpleCookie
from typing import Mapping, Optional
from urllib.parse import urlsplit

from src.auth.manager import COOKIE_NAME

PUBLIC, ADMIN, PAGE = "public", "admin", "page"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Mutating requests that need no session. Everything else that is not GET/HEAD/OPTIONS requires the admin.
OPEN_MUTATIONS = frozenset({"/api/auth/setup", "/api/auth/login", "/api/auth/logout", "/api/community/announce"})
# Other stations call this without a browser, so it has neither a session nor the CSRF origin check.
NO_ORIGIN_CHECK = frozenset({"/api/community/announce"})
# Machine-to-machine ingest routes a local process may POST without a session (loopback peer, no proxy headers).
# Nothing in Zenivo receives such pushes today (the ADS-B feeder only reads /api/adsb/aircraft), so the list is empty.
LOCAL_INGEST: frozenset = frozenset()
# GET routes that leak logs or settings: admin only. All other GETs are public.
ADMIN_GET = frozenset({"/api/logs", "/api/logs/download", "/api/community/settings"})
# Pages of the web UI that are only for the admin (guests are redirected to /login).
ADMIN_PAGES = frozenset({"/settings", "/calibration", "/focus", "/logs"})
# A request carrying any of these came through a proxy/tunnel: its peer address (127.0.0.1) says nothing about the client.
PROXY_HEADERS = ("x-forwarded-for", "x-real-ip", "forwarded", "cf-connecting-ip", "cf-ray")
_LOOPBACK = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})


def is_local_process(peer: Optional[str], headers: Mapping[str, str]) -> bool:
    """TCP peer is loopback AND no proxy header is present: a process on this host, not a tunnelled client."""
    return peer in _LOOPBACK and not any(h in headers for h in PROXY_HEADERS)


def classify(method: str, path: str, headers: Mapping[str, str], peer: Optional[str]) -> str:
    """PUBLIC, ADMIN or PAGE (an admin page: redirect guests to /login). `headers` keys are lower-case."""
    method = method.upper()
    if method in SAFE_METHODS:
        if path in ADMIN_GET:
            return ADMIN
        if path in ADMIN_PAGES:
            return PAGE
        return PUBLIC
    if path in OPEN_MUTATIONS:
        return PUBLIC
    if path in LOCAL_INGEST and is_local_process(peer, headers):
        return PUBLIC
    return ADMIN


def origin_ok(headers: Mapping[str, str]) -> bool:
    """CSRF guard: when an Origin header is present its host:port must equal the request Host."""
    origin = headers.get("origin")
    if origin is None:
        return True
    host = (headers.get("host") or "").lower()
    fwd = (headers.get("x-forwarded-host") or "").split(",")[0].strip().lower()
    try:
        netloc = urlsplit(origin).netloc.lower()
    except ValueError:
        return False
    return bool(netloc) and netloc in {h for h in (host, fwd) if h}


def needs_origin_check(method: str, path: str) -> bool:
    return method.upper() not in SAFE_METHODS and path not in NO_ORIGIN_CHECK


def session_token(headers: Mapping[str, str]) -> Optional[str]:
    raw = headers.get("cookie")
    if not raw:
        return None
    try:
        jar = SimpleCookie()
        jar.load(raw)
    except Exception:
        return None
    morsel = jar.get(COOKIE_NAME)
    return morsel.value if morsel else None


def safe_next(value: Optional[str]) -> str:
    """A same-site relative path or '/'. Rejects absolute URLs, //host, backslashes and control characters."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    if any(ord(c) < 32 or ord(c) == 127 for c in value) or value.startswith("/login"):
        return "/"
    return value


def client_key(headers: Mapping[str, str], peer: Optional[str]) -> str:
    """Throttling key only (never authorisation): CF-Connecting-IP, else first X-Forwarded-For hop, else the peer."""
    cf = (headers.get("cf-connecting-ip") or "").strip()
    if cf:
        return cf[:64]
    xff = (headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return (xff or peer or "unknown")[:64]
