"""/api/auth/*: status, first-time setup, login, logout, password change, sign out everywhere."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.api.service import AllSkyService
from src.auth import policy
from src.auth.manager import COOKIE_NAME, SESSION_TTL_S, PasswordError, check_password_rules
from src.auth.middleware import is_admin

auth_router = APIRouter(prefix="/api/auth", tags=["auth"])


class SetupRequest(BaseModel):
    code: str = Field(..., max_length=100)
    password: str = Field(..., max_length=300)


class LoginRequest(BaseModel):
    password: str = Field(..., max_length=300)


class PasswordChangeRequest(BaseModel):
    current: str = Field(..., max_length=300)
    new: str = Field(..., max_length=300)


def _auth():
    return AllSkyService.get_instance().auth


def _secure(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"


def _reply(request: Request, body: dict, token: str = None, clear: bool = False, status_code: int = 200) -> JSONResponse:
    resp = JSONResponse(body, status_code=status_code, headers={"Cache-Control": "no-store"})
    if token:
        resp.set_cookie(COOKIE_NAME, token, max_age=SESSION_TTL_S, httponly=True, samesite="lax", path="/",
                        secure=_secure(request))
    if clear:
        resp.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="lax", secure=_secure(request))
    return resp


def _throttled(request: Request, per_key, glob) -> None:
    key = policy.client_key({k.lower(): v for k, v in request.headers.items()}, request.client.host if request.client else None)
    wait = max(per_key.retry_after(key), glob.retry_after("*"))
    if wait:
        raise HTTPException(status_code=429, detail=f"Too many attempts, try again in {wait} s",
                            headers={"Retry-After": str(wait)})


def _client(request: Request) -> str:
    return policy.client_key({k.lower(): v for k, v in request.headers.items()}, request.client.host if request.client else None)


@auth_router.get("/status")
def auth_status(request: Request):
    a = _auth()
    configured = a.configured
    return _reply(request, {"configured": configured, "admin": is_admin(request), "setup_pending": not configured})


@auth_router.post("/setup")
def auth_setup(req: SetupRequest, request: Request):
    a = _auth()
    if a.configured:
        raise HTTPException(status_code=409, detail="Already set up; sign in instead")
    _throttled(request, a.setup_throttle, a.setup_global)
    a.ensure_setup_code()
    key = _client(request)
    if not a.check_setup_code(req.code):
        a.setup_throttle.fail(key)
        a.setup_global.fail("*")
        raise HTTPException(status_code=403, detail="Wrong setup code")
    try:
        check_password_rules(req.password)
    except PasswordError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    a.setup_throttle.ok(key)
    token = a.finish_setup(req.password)
    return _reply(request, {"ok": True, "admin": True}, token=token)


@auth_router.post("/login")
def auth_login(req: LoginRequest, request: Request):
    a = _auth()
    if not a.configured:
        raise HTTPException(status_code=409, detail="Not set up yet: use the setup code")
    _throttled(request, a.login_throttle, a.login_global)
    key = _client(request)
    if not a.verify_password(req.password):
        a.login_throttle.fail(key)
        a.login_global.fail("*")
        raise HTTPException(status_code=403, detail="Wrong password")
    a.login_throttle.ok(key)
    return _reply(request, {"ok": True, "admin": True}, token=a.create_session())


@auth_router.post("/logout")
def auth_logout(request: Request):
    _auth().revoke_session(policy.session_token({k.lower(): v for k, v in request.headers.items()}))
    return _reply(request, {"ok": True, "admin": False}, clear=True)


@auth_router.post("/password")
def auth_password(req: PasswordChangeRequest, request: Request):
    """Admin only (default deny). Every session is cleared; this browser gets a fresh one."""
    a = _auth()
    _throttled(request, a.login_throttle, a.login_global)
    key = _client(request)
    if not a.verify_password(req.current):
        a.login_throttle.fail(key)
        a.login_global.fail("*")
        raise HTTPException(status_code=403, detail="Current password is wrong")
    try:
        check_password_rules(req.new)
    except PasswordError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    a.login_throttle.ok(key)
    a.set_password(req.new)
    return _reply(request, {"ok": True}, token=a.create_session())


@auth_router.post("/logout-all")
def auth_logout_all(request: Request):
    _auth().clear_sessions()
    return _reply(request, {"ok": True}, clear=True)
