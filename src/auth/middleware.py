"""Default-deny enforcement for HTTP (and the WebSocket handshake) in one place: classify() decides, this applies it.

Sets scope["state"]["is_admin"] (valid session cookie) and ["origin_ok"] for every request, so routes and the WebSocket
handler can ask `is_admin(conn)` without re-parsing cookies."""
from __future__ import annotations

import json
from typing import Any, Callable, Dict
from urllib.parse import quote

from src.auth import policy

_AUTH_BODY = json.dumps({"detail": "Sign in as admin", "code": "auth"}).encode()
_ORIGIN_BODY = json.dumps({"detail": "Cross-site request refused", "code": "origin"}).encode()


def _headers(scope) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for k, v in scope.get("headers") or ():
        out.setdefault(k.decode("latin-1").lower(), v.decode("latin-1"))
    return out


def is_admin(conn) -> bool:
    """True when the request/WebSocket carries a valid admin session (decided by AuthMiddleware)."""
    return bool(conn.scope.get("state", {}).get("is_admin"))


def can_mutate(conn) -> bool:
    """Admin session AND same-site origin: the gate for state-changing WebSocket messages."""
    st = conn.scope.get("state", {})
    return bool(st.get("is_admin")) and bool(st.get("origin_ok", True))


class AuthMiddleware:
    def __init__(self, app, auth_fn: Callable[[], Any]):
        """auth_fn() returns the current AuthManager (the service singleton is replaced between tests)."""
        self.app = app
        self._auth_fn = auth_fn

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        headers = _headers(scope)
        peer = (scope.get("client") or (None, None))[0]
        state = scope.setdefault("state", {})
        auth = self._auth_fn()
        state["is_admin"] = bool(auth.session_valid(policy.session_token(headers)))
        state["origin_ok"] = policy.origin_ok(headers)
        if scope["type"] == "websocket":  # mutating messages are gated inside the handler
            return await self.app(scope, receive, send)

        method, path = scope["method"], scope["path"]
        access = policy.classify(method, path, headers, peer)
        if policy.needs_origin_check(method, path) and not state["origin_ok"]:
            return await self._json(send, 403, _ORIGIN_BODY)
        if access != policy.PUBLIC and not state["is_admin"]:
            if access == policy.PAGE:
                target = "/login?next=" + quote(path, safe="/")
                await send({"type": "http.response.start", "status": 302,
                            "headers": [(b"location", target.encode()), (b"cache-control", b"no-store"),
                                        (b"content-length", b"0")]})
                return await send({"type": "http.response.body", "body": b""})
            return await self._json(send, 401, _AUTH_BODY)
        return await self.app(scope, receive, send)

    @staticmethod
    async def _json(send, status: int, body: bytes):
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"), (b"cache-control", b"no-store"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
