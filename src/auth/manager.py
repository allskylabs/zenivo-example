"""Single-admin credentials, sessions, first-run setup code and login throttling (stdlib only).

Files in the data directory (all 0600, written atomically):
  admin.json               {salt, hash, params, changed}: scrypt hash of the admin password
  admin_sessions.json      sha256(token) -> {created, exp, epoch}: signed-in browsers (the token itself is never stored)
  admin_setup_code.txt     one-time code printed nowhere; exists only while no password is set
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, Optional

logger = logging.getLogger("zenivo.auth")

COOKIE_NAME = "zenivo_session"
SESSION_TTL_S = 30 * 86400
MAX_SESSIONS = 20
PASSWORD_MIN, PASSWORD_MAX = 10, 256
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}
_SCRYPT_MAXMEM = 64 * 1024 * 1024


class PasswordError(ValueError):
    """The password does not meet the length rules."""


def _atomic_write(path: str, text: str) -> None:
    """Write `text` to `path` with mode 0600 via a temp file + rename (never world-readable, never half-written)."""
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _scrypt(password: str, salt: bytes, params: Dict[str, int]) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=params["n"], r=params["r"], p=params["p"],
                          dklen=params["dklen"], maxmem=_SCRYPT_MAXMEM)


def check_password_rules(password: Any) -> str:
    if not isinstance(password, str) or not PASSWORD_MIN <= len(password) <= PASSWORD_MAX:
        raise PasswordError(f"Password must be {PASSWORD_MIN} to {PASSWORD_MAX} characters")
    return password


def hash_password(password: str) -> Dict[str, Any]:
    salt = secrets.token_bytes(16)
    return {"salt": base64.b64encode(salt).decode("ascii"),
            "hash": base64.b64encode(_scrypt(password, salt, SCRYPT)).decode("ascii"),
            "params": dict(SCRYPT), "changed": time.time()}


def verify_hash(record: Dict[str, Any], password: str) -> bool:
    try:
        params = {k: int(record["params"][k]) for k in ("n", "r", "p", "dklen")}
        salt = base64.b64decode(record["salt"])
        want = base64.b64decode(record["hash"])
        got = _scrypt(password, salt, params)
    except (KeyError, TypeError, ValueError):
        return False
    return hmac.compare_digest(got, want)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class Throttle:
    """Failed-attempt limiter: `limit` failures within `window_s` lock the key for 60 s, doubling per repeated lockout
    up to 15 min. One instance per kind of attempt (login, setup code, password change)."""

    def __init__(self, limit: int = 5, window_s: float = 300.0, base_s: float = 60.0, max_s: float = 900.0,
                 clock=time.time):
        self.limit, self.window_s, self.base_s, self.max_s, self._clock = limit, window_s, base_s, max_s, clock
        self._state: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def retry_after(self, key: str) -> int:
        """Seconds until `key` may try again (0 = allowed now)."""
        with self._lock:
            st = self._state.get(key)
            left = (st["until"] - self._clock()) if st else 0.0
            return int(left) + 1 if left > 0 else 0

    def fail(self, key: str) -> None:
        now = self._clock()
        with self._lock:
            if len(self._state) > 1000:  # bound memory against many source addresses
                for k in [k for k, s in self._state.items() if s["until"] < now and not s["fails"]]:
                    del self._state[k]
            st = self._state.setdefault(key, {"fails": deque(), "until": 0.0, "level": 0})
            fails: Deque[float] = st["fails"]
            while fails and fails[0] < now - self.window_s:
                fails.popleft()
            fails.append(now)
            if len(fails) >= self.limit:
                st["level"] += 1
                st["until"] = now + min(self.base_s * 2 ** (st["level"] - 1), self.max_s)
                fails.clear()

    def ok(self, key: str) -> None:
        with self._lock:
            self._state.pop(key, None)


class AuthManager:
    def __init__(self, data_dir: str, clock=time.time):
        self.data_dir = data_dir
        self._clock = clock
        self._lock = threading.RLock()
        self._admin: Optional[Dict[str, Any]] = None
        self._admin_stamp: Any = None
        self._sessions: Optional[Dict[str, Dict[str, Any]]] = None
        self._warned = False
        # per client key (login, setup code, password change) and global (a wider net against many addresses)
        self.login_throttle = Throttle(clock=clock)
        self.login_global = Throttle(limit=20, clock=clock)
        self.setup_throttle = Throttle(clock=clock)
        self.setup_global = Throttle(limit=20, clock=clock)

    # -- paths ---------------------------------------------------------
    def _path(self, name: str) -> str:
        return os.path.join(self.data_dir, name)

    @property
    def admin_path(self) -> str:
        return self._path("admin.json")

    @property
    def sessions_path(self) -> str:
        return self._path("admin_sessions.json")

    @property
    def setup_code_path(self) -> str:
        return self._path("admin_setup_code.txt")

    # -- admin record (re-read when the file changes, so the CLI works while the service runs) --------
    def _record(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            try:
                st = os.stat(self.admin_path)
                stamp = (st.st_mtime_ns, st.st_size)
            except OSError:
                self._admin, self._admin_stamp = None, None
                return None
            if stamp != self._admin_stamp:
                try:
                    with open(self.admin_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    ok = isinstance(data, dict) and all(k in data for k in ("salt", "hash", "params", "changed"))
                    self._admin = data if ok else None
                except (OSError, ValueError):
                    self._admin = None
                self._admin_stamp = stamp
            return self._admin

    @property
    def configured(self) -> bool:
        return self._record() is not None

    def set_password(self, password: str) -> None:
        """Store a new admin password and sign everybody out. Raises PasswordError."""
        check_password_rules(password)
        rec = hash_password(password)
        with self._lock:
            os.makedirs(self.data_dir, exist_ok=True)
            _atomic_write(self.admin_path, json.dumps(rec))
            self._admin, self._admin_stamp = None, None
            self.clear_sessions()

    def verify_password(self, password: Any) -> bool:
        rec = self._record()
        if rec is None or not isinstance(password, str) or len(password) > PASSWORD_MAX:
            return False
        return verify_hash(rec, password)

    def reset(self) -> None:
        """Forget the password and all sessions (CLI --reset-admin); a new setup code is issued at the next start."""
        for p in (self.admin_path, self.sessions_path, self.setup_code_path):
            try:
                os.remove(p)
            except FileNotFoundError:
                pass
        with self._lock:
            self._admin, self._admin_stamp, self._sessions = None, None, {}

    # -- setup code ----------------------------------------------------
    def ensure_setup_code(self) -> None:
        """While no password exists make sure a one-time code file is there and log ONE warning naming it (never the code)."""
        if self.configured:
            return
        with self._lock:
            if not os.path.isfile(self.setup_code_path):
                os.makedirs(self.data_dir, exist_ok=True)
                _atomic_write(self.setup_code_path, secrets.token_urlsafe(9) + "\n")
            if not self._warned:
                self._warned = True
                logger.warning("Zenivo admin password is not set: read the one-time setup code in %s and open /login "
                               "(or run: python3 run_allsky.py --set-admin-password)", self.setup_code_path)

    def check_setup_code(self, code: Any) -> bool:
        if not isinstance(code, str) or not code or len(code) > 100:
            return False
        try:
            with open(self.setup_code_path, "r", encoding="utf-8") as f:
                want = f.read().strip()
        except OSError:
            return False
        return bool(want) and hmac.compare_digest(code.strip().encode("utf-8"), want.encode("utf-8"))

    def finish_setup(self, password: str) -> str:
        """Set the first password, drop the code file and return a session token for the caller."""
        self.set_password(password)
        try:
            os.remove(self.setup_code_path)
        except FileNotFoundError:
            pass
        return self.create_session()

    # -- sessions ------------------------------------------------------
    def _load_sessions(self) -> Dict[str, Dict[str, Any]]:
        if self._sessions is None:
            try:
                with open(self.sessions_path, "r", encoding="utf-8") as f:
                    raw = json.load(f).get("sessions", {})
                self._sessions = {k: v for k, v in raw.items() if isinstance(v, dict) and "exp" in v} if isinstance(raw, dict) else {}
            except (OSError, ValueError, AttributeError):
                self._sessions = {}
        return self._sessions

    def _save_sessions(self) -> None:
        try:
            os.makedirs(self.data_dir, exist_ok=True)
            _atomic_write(self.sessions_path, json.dumps({"sessions": self._sessions or {}}))
        except OSError:
            logger.warning("Could not save %s", self.sessions_path)

    def create_session(self) -> str:
        rec = self._record()
        token = secrets.token_urlsafe(32)
        now = self._clock()
        with self._lock:
            sessions = self._load_sessions()
            sessions[hash_token(token)] = {"created": now, "exp": now + SESSION_TTL_S,
                                           "epoch": (rec or {}).get("changed")}
            while len(sessions) > MAX_SESSIONS:
                oldest = min(sessions, key=lambda k: sessions[k].get("created", 0))
                del sessions[oldest]
            self._save_sessions()
        return token

    def session_valid(self, token: Optional[str]) -> bool:
        """True for a live session of the current password; extends its expiry (sliding window)."""
        if not token or len(token) > 200:
            return False
        rec = self._record()
        if rec is None:
            return False
        key = hash_token(token)
        now = self._clock()
        with self._lock:
            sessions = self._load_sessions()
            s = sessions.get(key)
            if s is None:
                return False
            if s["exp"] < now or s.get("epoch") != rec.get("changed"):
                del sessions[key]
                self._save_sessions()
                return False
            if now + SESSION_TTL_S - s["exp"] > 3600:  # persist the slide at most hourly
                s["exp"] = now + SESSION_TTL_S
                self._save_sessions()
            return True

    def revoke_session(self, token: Optional[str]) -> None:
        if not token:
            return
        with self._lock:
            if self._load_sessions().pop(hash_token(token), None) is not None:
                self._save_sessions()

    def clear_sessions(self) -> None:
        with self._lock:
            self._sessions = {}
            self._save_sessions()
