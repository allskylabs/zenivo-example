"""Who may call what: ONE classification function used by the HTTP middleware, plus the small helpers around it
(CSRF origin check, local-process test for machine ingest, safe `next` redirects, session cookie parsing)."""
from __future__ import annotations

import re
from typing import Mapping, Optional
from urllib.parse import urlsplit

from src.auth.manager import COOKIE_NAME

PUBLIC, ADMIN, PAGE = "public", "admin", "page"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Mutating requests that need no session. Everything else that is not GET/HEAD/OPTIONS requires the admin.
OPEN_MUTATIONS = frozenset({"/api/auth/setup", "/api/auth/login", "/api/auth/logout", "/api/community/announce",
                            "/api/community/chat/post"})
# Dynamic: POST /api/community/relay/<station id> (a station without a public URL pushes its preview; signed per request)
_RELAY_POST = re.compile(r"^/api/community/relay/[0-9a-f]{32}$")
# Other stations call this without a browser, so it has neither a session nor the CSRF origin check.
NO_ORIGIN_CHECK = frozenset({"/api/community/announce", "/api/community/chat/post"})
# Machine-to-machine ingest routes a local process may POST without a session (loopback peer, no proxy headers).
# Nothing in Zenivo receives such pushes today (the ADS-B feeder only reads /api/adsb/aircraft), so the list is empty.
LOCAL_INGEST: frozenset = frozenset()
# GET routes that leak logs or settings: admin only. All other GETs are public.
# (/docs, /redoc and /openapi.json are FastAPI's built-in pages; they sit outside /api but are matched by exact path.)
ADMIN_GET = frozenset({"/api/logs", "/api/logs/download", "/api/community/settings", "/api/community/chat/local", "/api/community/chat/events", "/api/community/chat/stats", "/api/focus", "/api/focus/frame",
                       "/api/focus/overview.jpg", "/api/storage", "/api/camera/devices", "/api/satellites/config", "/api/data-sources", "/api/system/resources", "/docs", "/redoc", "/openapi.json"})
# WebSocket text actions a guest may send (they only read). Everything else needs the admin session + same-site origin.
WS_GUEST_ACTIONS = frozenset({"ping", "get_telemetry", "get_status", "subscribe", "idle"})
# Pages of the web UI that are only for the admin (guests are redirected to /login).
ADMIN_PAGES = frozenset({"/settings", "/calibration", "/focus", "/logs", "/obs/dock", "/obs/control", "/dock"})
# A request carrying any of these came through a proxy/tunnel: its peer address (127.0.0.1) says nothing about the client.
PROXY_HEADERS = ("x-forwarded-for", "x-real-ip", "forwarded", "cf-connecting-ip", "cf-ray")
_LOOPBACK = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})


def is_loopback(peer: Optional[str]) -> bool:
    """The TCP peer is this host: only then are proxy headers (X-Forwarded-*, CF-Connecting-IP) believable, because
    they were set by a local proxy/tunnel; from any other peer they are written by the client itself."""
    return peer in _LOOPBACK


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
    if path in OPEN_MUTATIONS or _RELAY_POST.match(path):
        return PUBLIC
    if path in LOCAL_INGEST and is_local_process(peer, headers):
        return PUBLIC
    return ADMIN


def origin_ok(headers: Mapping[str, str], peer: Optional[str] = None) -> bool:
    """CSRF guard: when an Origin header is present its host:port must equal the request Host
    (or X-Forwarded-Host, but only when the TCP peer is a local proxy)."""
    origin = headers.get("origin")
    if origin is None:
        return True
    host = (headers.get("host") or "").lower()
    fwd = (headers.get("x-forwarded-host") or "").split(",")[-1].strip().lower() if is_loopback(peer) else ""
    try:
        netloc = urlsplit(origin).netloc.lower()
    except ValueError:
        return False
    return bool(netloc) and netloc in {h for h in (host, fwd) if h}


def forwarded_https(headers: Mapping[str, str], peer: Optional[str]) -> bool:
    """The client used https to reach a local proxy/tunnel (X-Forwarded-Proto, trusted from a loopback peer only)."""
    return is_loopback(peer) and (headers.get("x-forwarded-proto") or "").split(",")[-1].strip().lower() == "https"


def ws_action_allowed(action: object, is_admin_and_origin_ok: bool) -> bool:
    """Gate for a WebSocket text command: guests may send the read-only actions only (allow-list, default deny);
    the admin session with a same-site origin may send any string action. A non-string action is never allowed."""
    if not isinstance(action, str):
        return False
    return bool(is_admin_and_origin_ok) or action in WS_GUEST_ACTIONS


def needs_origin_check(method: str, path: str) -> bool:
    return method.upper() not in SAFE_METHODS and path not in NO_ORIGIN_CHECK and not _RELAY_POST.match(path)


def session_token(headers: Mapping[str, str]) -> Optional[str]:
    """The session cookie value. Parsed by hand: http.cookies.SimpleCookie drops EVERY cookie after one it cannot
    parse (e.g. a Google `g_state={"i_l":0,...}` JSON cookie left on localhost), which silently signed the admin out."""
    raw = headers.get("cookie")
    if not raw:
        return None
    for part in raw.split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name == COOKIE_NAME:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] == '"':
                value = value[1:-1]
            return value or None
    return None


def safe_next(value: Optional[str]) -> str:
    """A same-site relative path or '/'. Rejects absolute URLs, //host, backslashes and control characters."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    if any(ord(c) < 32 or ord(c) == 127 for c in value) or value.startswith("/login"):
        return "/"
    return value


def client_key(headers: Mapping[str, str], peer: Optional[str]) -> str:
    """Throttling key only (never authorisation). From a loopback peer (a local proxy/tunnel) the real client is
    CF-Connecting-IP, else the RIGHT-most X-Forwarded-For hop (the one the local proxy appended); from any other peer
    those headers are client-written and ignored, so the key is the peer address."""
    if is_loopback(peer):
        cf = (headers.get("cf-connecting-ip") or "").strip()
        if cf:
            return cf[:64]
        xff = (headers.get("x-forwarded-for") or "").split(",")[-1].strip()
        if xff:
            return xff[:64]
    return (peer or "unknown")[:64]
