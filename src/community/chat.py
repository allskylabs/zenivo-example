"""Station-to-station chat: signed messages, end-to-end encrypted direct messages, the hub store and the local history.
See docs/COMMUNITY.md ("Chat").

Everything here is pure (no network, no manager): formats, signing, encryption and the on-disk stores. Only the
`cryptography` package is used."""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import tempfile
import threading
import time
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from src.community import identity as idmod
from src.community.identity import verify_signature

try:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
except Exception:  # missing library: idmod.availability() reports it, the chat is simply off
    pass

DOMAIN_MSG = b"zenivo-chat-v1\n"
DOMAIN_KEY = b"zenivo-chat-key-v1\n"
DOMAIN_FETCH = b"zenivo-chat-fetch-v1\n"
DM_INFO = b"zenivo-chat-dm-v1"
VERSION = 1
PUBLIC = "*"
ID_RE = re.compile(r"^[0-9a-f]{32}$")
MID_RE = re.compile(r"^[0-9a-f]{32}$")
MAX_TEXT = 500                    # characters of a message
MAX_BODY_BYTES = 4096             # canonical size of a message body
MAX_CT = 2400                     # raw bytes of an encrypted body (500 characters of UTF-8 is at most 2000)
SKEW_S = 300
TTL_S = 7 * 24 * 3600
PUBLIC_CAP = 2000
DM_CAP = 200                      # per recipient
KEYS_CAP = 500
KEY_MAX_AGE_S = 30 * 24 * 3600
MAX_TIME = 4_000_000_000          # unix seconds: refuses absurd values
SAVE_INTERVAL_S = 30
LOCAL_THREAD_CAP = 500
LOCAL_THREADS_MAX = 300
LOCAL_TTL_S = 30 * 24 * 3600
BLOCKED_MAX = 1000
_MSG_FIELDS = frozenset({"v", "mid", "from", "to", "t", "body", "sig"})
_KEY_FIELDS = frozenset({"id", "xpub", "t", "sig"})
_BODY_FIELDS = frozenset({"epk", "nonce", "ct"})


class ChatError(ValueError):
    """A message, key record or header is malformed, forged or not decryptable."""


class ChatRefused(Exception):
    """A request is not accepted; `status` is the HTTP status to answer with."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(text: Any, length: Optional[int] = None, max_len: int = 0) -> bytes:
    if not isinstance(text, str) or len(text) > 8192:
        raise ChatError("bad base64")
    try:
        raw = base64.b64decode(text, validate=True)
    except ValueError:
        raise ChatError("bad base64")
    if (length is not None and len(raw) != length) or (max_len and len(raw) > max_len):
        raise ChatError("bad length")
    return raw


def _time(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value < MAX_TIME:
        raise ChatError("bad time")
    return value


def clean_text(text: Any) -> str:
    """The text a person may send: control characters removed (a line break becomes a space), trimmed, at most 500 chars."""
    if not isinstance(text, str):
        raise ChatError("text must be a string")
    text = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    text = "".join(c for c in text if unicodedata.category(c)[0] != "C")
    text = text.strip()[:MAX_TEXT].strip()
    if not text:
        raise ChatError("empty message")
    return text


# -- chat key -----------------------------------------------------------------
class ChatKey:
    """X25519 private key in `path` (mode 0600), created on first use."""

    def __init__(self, path: str):
        self.path = path
        if os.path.isfile(path):
            with open(path, "rb") as f:
                self._key = serialization.load_pem_private_key(f.read(), password=None)
        else:
            self._key = X25519PrivateKey.generate()
            pem = self._key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption())
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(pem)
        self.xpub: bytes = self._key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)

    def exchange(self, peer_public: bytes) -> bytes:
        return self._key.exchange(X25519PublicKey.from_public_bytes(peer_public))


def _key_message(rec: Dict[str, Any]) -> bytes:
    return DOMAIN_KEY + canonical({"id": rec["id"], "t": rec["t"], "xpub": rec["xpub"]})


def make_key_record(identity, chatkey: ChatKey, t: Optional[int] = None) -> Dict[str, Any]:
    rec = {"id": identity.id, "xpub": _b64(chatkey.xpub), "t": int(time.time()) if t is None else int(t)}
    rec["sig"] = _b64(identity.sign(_key_message(rec)))
    return rec


def verify_key_record(rec: Any, pubkey_b64: str, now: Optional[float] = None) -> Dict[str, Any]:
    """The record when the card's key signed it, else ChatError."""
    if not isinstance(rec, dict) or set(rec) != _KEY_FIELDS:
        raise ChatError("bad key record")
    now = time.time() if now is None else now
    if not isinstance(rec["id"], str) or not ID_RE.match(rec["id"]):
        raise ChatError("bad id")
    _unb64(rec["xpub"], 32)
    if _time(rec["t"]) > now + SKEW_S:
        raise ChatError("key record is in the future")
    pub = _unb64(pubkey_b64, 32)
    if idmod.station_id(pub) != rec["id"] or not verify_signature(pub, _unb64(rec["sig"], 64), _key_message(rec)):
        raise ChatError("bad key signature")
    return rec


# -- messages -----------------------------------------------------------------
def _unsigned(msg: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in msg.items() if k != "sig"}


def _header(frm: str, to: str, t: int) -> bytes:
    return canonical({"from": frm, "t": t, "to": to, "v": VERSION})


def _finish(identity, to: str, t: int, body: Any) -> Dict[str, Any]:
    msg: Dict[str, Any] = {"v": VERSION, "from": identity.id, "to": to, "t": t, "body": body}
    msg["mid"] = hashlib.sha256(canonical(msg)).digest()[:16].hex()
    msg["sig"] = _b64(identity.sign(DOMAIN_MSG + canonical(_unsigned(msg))))
    return msg


def make_public(identity, text: str, t: Optional[int] = None) -> Dict[str, Any]:
    return _finish(identity, PUBLIC, int(time.time()) if t is None else int(t), clean_text(text))


def make_dm(identity, to: str, to_xpub_b64: str, text: str, t: Optional[int] = None) -> Dict[str, Any]:
    """Encrypt `text` for the holder of `to_xpub_b64`: ephemeral X25519 + HKDF-SHA256 + ChaCha20-Poly1305, the header
    (from, to, time) is the associated data."""
    if not isinstance(to, str) or not ID_RE.match(to) or to == identity.id:
        raise ChatError("bad recipient")
    t = int(time.time()) if t is None else int(t)
    peer = _unb64(to_xpub_b64, 32)
    eph = X25519PrivateKey.generate()
    shared = eph.exchange(X25519PublicKey.from_public_bytes(peer))
    epk = eph.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=DM_INFO).derive(shared)
    nonce = os.urandom(12)
    ct = ChaCha20Poly1305(key).encrypt(nonce, clean_text(text).encode("utf-8"), _header(identity.id, to, t))
    return _finish(identity, to, t, {"epk": _b64(epk), "nonce": _b64(nonce), "ct": _b64(ct)})


def open_dm(msg: Dict[str, Any], chatkey: ChatKey) -> str:
    """The text of a direct message addressed to us; ChatError when it is not for this key or was altered."""
    body = msg.get("body")
    if not isinstance(body, dict):
        raise ChatError("not a direct message")
    try:
        shared = chatkey.exchange(_unb64(body["epk"], 32))
        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=DM_INFO).derive(shared)
        plain = ChaCha20Poly1305(key).decrypt(_unb64(body["nonce"], 12), _unb64(body["ct"], max_len=MAX_CT),
                                              _header(msg["from"], msg["to"], msg["t"]))
        return plain.decode("utf-8")
    except (InvalidTag, UnicodeDecodeError, KeyError, TypeError, ValueError):
        raise ChatError("cannot decrypt")


def check_message(msg: Any) -> Dict[str, Any]:
    """Format only (no signature): exact fields, sane values, body limits. Returns the message or raises ChatError."""
    if not isinstance(msg, dict) or set(msg) != _MSG_FIELDS:
        raise ChatError("bad message fields")
    if msg["v"] != VERSION or isinstance(msg["v"], bool):
        raise ChatError("unsupported version")
    for k in ("mid", "from"):
        if not isinstance(msg[k], str) or not ID_RE.match(msg[k]):
            raise ChatError(f"bad {k}")
    to = msg["to"]
    if not isinstance(to, str) or not (to == PUBLIC or ID_RE.match(to)) or to == msg["from"]:
        raise ChatError("bad recipient")
    _time(msg["t"])
    _unb64(msg["sig"], 64)
    body = msg["body"]
    try:
        size = len(canonical(body))
    except (TypeError, ValueError):
        raise ChatError("body is not JSON")
    if size > MAX_BODY_BYTES:
        raise ChatError("body too large")
    if to == PUBLIC:
        if not isinstance(body, str) or not body or len(body) > MAX_TEXT or any(unicodedata.category(c)[0] == "C" for c in body):
            raise ChatError("bad public text")
    else:
        if not isinstance(body, dict) or set(body) != _BODY_FIELDS:
            raise ChatError("bad direct body")
        _unb64(body["epk"], 32)
        _unb64(body["nonce"], 12)
        _unb64(body["ct"], max_len=MAX_CT)
    if hashlib.sha256(canonical({k: v for k, v in msg.items() if k not in ("sig", "mid")})).digest()[:16].hex() != msg["mid"]:
        raise ChatError("mid does not match")
    return msg


def verify_message(msg: Any, pubkey_b64: str) -> Dict[str, Any]:
    """Format plus the Ed25519 signature of the sender (`pubkey_b64` = key on the sender's card; the id must match)."""
    check_message(msg)
    pub = _unb64(pubkey_b64, 32)
    if idmod.station_id(pub) != msg["from"]:
        raise ChatError("key does not belong to the sender")
    if not verify_signature(pub, _unb64(msg["sig"], 64), DOMAIN_MSG + canonical(_unsigned(msg))):
        raise ChatError("bad signature")
    return msg


def check_skew(msg: Dict[str, Any], now: Optional[float] = None) -> None:
    """A message being posted must carry the current time (clock skew up to 300 s)."""
    now = time.time() if now is None else now
    if abs(now - msg["t"]) > SKEW_S:
        raise ChatError("message time is off")


# -- signed fetch (a station asks a hub for its own direct messages) ---------------
def fetch_message(station_id: str, t: int) -> bytes:
    return DOMAIN_FETCH + f"{station_id}\n{int(t)}".encode("ascii")


def sign_fetch(identity, t: Optional[int] = None) -> Dict[str, str]:
    t = int(time.time()) if t is None else int(t)
    return {"X-Zenivo-Id": identity.id, "X-Zenivo-Time": str(t),
            "X-Zenivo-Sig": _b64(identity.sign(fetch_message(identity.id, t)))}


def verify_fetch(pubkey_b64: str, station_id: str, t_header: Optional[str], sig_header: Optional[str],
                 now: Optional[float] = None) -> bool:
    now = time.time() if now is None else now
    try:
        t = int(t_header or "")
        pub = _unb64(pubkey_b64, 32)
        sig = _unb64(sig_header, 64)
    except (ValueError, TypeError, ChatError):
        return False
    return abs(now - t) <= SKEW_S and verify_signature(pub, sig, fetch_message(station_id, t))


# -- stores ---------------------------------------------------------------------
def _write_json(path: str, payload: Any) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, separators=(",", ":"), allow_nan=False)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _no_constant(name: str) -> Any:
    raise ValueError(name)


def _read_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f, parse_constant=_no_constant)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, RecursionError):
        return {}


class ChatStore:
    """What a hub holds for others (data/community_chat.json): the public room, direct messages per recipient and the
    chat key records. Entries are {"r": time received, "m": message}; the file is written at most every 30 s."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._pub: List[Dict[str, Any]] = []
        self._dm: Dict[str, List[Dict[str, Any]]] = {}
        self._keys: Dict[str, Dict[str, Any]] = {}
        self._mids: set = set()
        self._dirty = False
        self._last_save = 0.0
        self._last_r = 0.0
        self.version = 0
        self._load()

    def _load(self) -> None:
        data = _read_json(self.path)
        try:
            for e in data.get("public") or []:
                if isinstance(e, dict) and isinstance(e.get("r"), (int, float)):
                    self._pub.append({"r": float(e["r"]), "m": check_message(e["m"])})
            for to, lst in (data.get("dm") or {}).items():
                if ID_RE.match(to):
                    self._dm[to] = [{"r": float(e["r"]), "m": check_message(e["m"])} for e in lst]
            for i, rec in (data.get("keys") or {}).items():
                if ID_RE.match(i) and isinstance(rec, dict) and set(rec) == _KEY_FIELDS:
                    self._keys[i] = rec
        except (ChatError, KeyError, TypeError, ValueError, AttributeError):
            self._pub, self._dm, self._keys = [], {}, {}   # a damaged file is dropped, not repaired
        self._mids = {e["m"]["mid"] for e in self._pub} | {e["m"]["mid"] for lst in self._dm.values() for e in lst}
        self._last_r = max([e["r"] for e in self._pub] + [e["r"] for l in self._dm.values() for e in l] + [0.0])

    def _next_r(self, now: float) -> float:
        self._last_r = max(now, self._last_r + 1e-6)   # strictly increasing: a cursor never skips or repeats a message
        return self._last_r

    def add(self, msg: Dict[str, Any], now: Optional[float] = None) -> bool:
        """Store a checked message. False for a duplicate or an expired one."""
        now = time.time() if now is None else now
        with self._lock:
            if msg["mid"] in self._mids or now - msg["t"] > TTL_S or msg["t"] > now + SKEW_S:
                return False
            entry = {"r": self._next_r(now), "m": msg}
            if msg["to"] == PUBLIC:
                self._pub.append(entry)
                for old in self._pub[:-PUBLIC_CAP]:
                    self._mids.discard(old["m"]["mid"])
                self._pub = self._pub[-PUBLIC_CAP:]
            else:
                lst = self._dm.setdefault(msg["to"], [])
                lst.append(entry)
                for old in lst[:-DM_CAP]:
                    self._mids.discard(old["m"]["mid"])
                self._dm[msg["to"]] = lst[-DM_CAP:]
            self._mids.add(msg["mid"])
            self._dirty = True
            self.version += 1
        return True

    def put_key(self, rec: Dict[str, Any]) -> bool:
        with self._lock:
            old = self._keys.get(rec["id"])
            if old is not None and old["t"] >= rec["t"]:
                return False
            self._keys[rec["id"]] = rec
            if len(self._keys) > KEYS_CAP:
                for i in sorted(self._keys, key=lambda k: self._keys[k]["t"])[:len(self._keys) - KEYS_CAP]:
                    del self._keys[i]
            self._dirty = True
            self.version += 1
            return True

    def keys(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._keys.values())

    def get_key(self, station_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._keys.get(station_id)

    def fetch(self, since: float, dm_for: Optional[str] = None, limit: int = 500, max_bytes: int = 400 * 1024,
              hidden: Optional[Any] = None) -> Tuple[List[Dict[str, Any]], float, bool]:
        """(messages, next cursor, more). Public room plus the direct messages for `dm_for`, oldest first, received after
        `since`; `hidden(sender id)` True drops that sender. The size and count caps cut the list, `more` says so."""
        with self._lock:
            entries = [e for e in self._pub if e["r"] > since]
            if dm_for:
                entries += [e for e in self._dm.get(dm_for, []) if e["r"] > since]
        entries.sort(key=lambda e: e["r"])
        out: List[Dict[str, Any]] = []
        used = 0
        cursor = since
        more = False
        for e in entries:
            size = len(canonical(e["m"])) + 2
            if len(out) >= limit or used + size > max_bytes:
                more = True
                break
            cursor = e["r"]
            if hidden is not None and hidden(e["m"]["from"]):
                continue
            used += size
            out.append(e["m"])
        return out, cursor, more

    def sweep(self, now: Optional[float] = None, known: Optional[Any] = None) -> int:
        """Drop messages older than 7 days and key records older than 30 days or of stations `known(id)` refuses."""
        now = time.time() if now is None else now
        removed = 0
        with self._lock:
            before = len(self._pub) + sum(len(v) for v in self._dm.values()) + len(self._keys)
            self._pub = [e for e in self._pub if now - e["m"]["t"] <= TTL_S]
            self._dm = {k: [e for e in v if now - e["m"]["t"] <= TTL_S] for k, v in self._dm.items()}
            self._dm = {k: v for k, v in self._dm.items() if v}
            self._keys = {i: r for i, r in self._keys.items()
                          if now - r["t"] <= KEY_MAX_AGE_S and (known is None or known(i))}
            self._mids = {e["m"]["mid"] for e in self._pub} | {e["m"]["mid"] for v in self._dm.values() for e in v}
            removed = before - (len(self._pub) + sum(len(v) for v in self._dm.values()) + len(self._keys))
            if removed:
                self._dirty = True
                self.version += 1
        return removed

    def counts(self) -> Dict[str, int]:
        with self._lock:
            return {"public": len(self._pub), "dm": sum(len(v) for v in self._dm.values()), "keys": len(self._keys)}

    def save(self, force: bool = True, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            if not self._dirty or (not force and now - self._last_save < SAVE_INTERVAL_S):
                return
            payload = {"public": self._pub, "dm": self._dm, "keys": self._keys}
            self._dirty = False
            self._last_save = now
            try:
                _write_json(self.path, payload)
            except OSError:
                self._dirty = True

    def clear(self) -> None:
        """Forget everything held for others and delete the file."""
        with self._lock:
            self._pub, self._dm, self._keys, self._mids = [], {}, {}, set()
            self._dirty = False
            self.version += 1
            try:
                os.unlink(self.path)
            except OSError:
                pass


class LocalChat:
    """This station's own conversations in plain text (data/community_chat_local.json): threads per peer id ("*" = the
    public room), unread marks, the block list, the chat keys of other stations and the poll cursor per hub. Kept when
    chat is turned off."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        data = _read_json(path)
        self.threads: Dict[str, List[Dict[str, Any]]] = {}
        for peer, lst in (data.get("threads") or {}).items() if isinstance(data.get("threads"), dict) else []:
            if (peer == PUBLIC or ID_RE.match(peer)) and isinstance(lst, list):
                self.threads[peer] = [e for e in lst if isinstance(e, dict) and isinstance(e.get("mid"), str)
                                      and isinstance(e.get("text"), str) and isinstance(e.get("t"), int)]
        self.blocked = {i for i in (data.get("blocked") or []) if isinstance(i, str) and ID_RE.match(i)} \
            if isinstance(data.get("blocked"), list) else set()
        self.keys: Dict[str, Dict[str, Any]] = {i: r for i, r in (data.get("keys") or {}).items()
                                                if isinstance(r, dict) and set(r) == _KEY_FIELDS and ID_RE.match(i)} \
            if isinstance(data.get("keys"), dict) else {}
        self.cursors: Dict[str, float] = {k: float(v) for k, v in (data.get("cursors") or {}).items()
                                          if isinstance(v, (int, float)) and math.isfinite(v)} \
            if isinstance(data.get("cursors"), dict) else {}
        self._mids = {e["mid"] for lst in self.threads.values() for e in lst}
        self._dirty = False
        self._last_save = 0.0

    def add(self, peer: str, msg: Dict[str, Any], text: str, incoming: bool, now: Optional[float] = None) -> bool:
        now = time.time() if now is None else now
        with self._lock:
            if msg["mid"] in self._mids or (incoming and peer != PUBLIC and msg["from"] in self.blocked):
                return False
            lst = self.threads.setdefault(peer, [])
            lst.append({"mid": msg["mid"], "from": msg["from"], "to": msg["to"], "t": msg["t"], "text": text,
                        "in": incoming, "seen": not incoming})
            lst.sort(key=lambda e: e["t"])
            for old in lst[:-LOCAL_THREAD_CAP]:
                self._mids.discard(old["mid"])
            self.threads[peer] = lst[-LOCAL_THREAD_CAP:]
            self._mids.add(msg["mid"])
            if len(self.threads) > LOCAL_THREADS_MAX:
                for p in sorted(self.threads, key=lambda k: self.threads[k][-1]["t"])[:len(self.threads) - LOCAL_THREADS_MAX]:
                    for e in self.threads.pop(p):
                        self._mids.discard(e["mid"])
            self._dirty = True
            return True

    def messages(self, peer: str) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(e) for e in self.threads.get(peer, []) if not (e["in"] and e["from"] in self.blocked)]

    def conversations(self) -> List[Dict[str, Any]]:
        with self._lock:
            out = []
            for peer, lst in self.threads.items():
                vis = [e for e in lst if not (e["in"] and e["from"] in self.blocked)]
                if vis or peer == PUBLIC:
                    out.append({"peer": peer, "unread": sum(1 for e in vis if e["in"] and not e["seen"]),
                                "last_t": vis[-1]["t"] if vis else 0, "last_text": vis[-1]["text"][:80] if vis else ""})
            return out

    def mark_read(self, peer: str) -> None:
        with self._lock:
            changed = False
            for e in self.threads.get(peer, []):
                if not e["seen"]:
                    e["seen"] = True
                    changed = True
            self._dirty = self._dirty or changed

    def set_blocked(self, station_id: str, blocked: bool) -> None:
        with self._lock:
            if blocked and len(self.blocked) < BLOCKED_MAX:
                self.blocked.add(station_id)
            elif not blocked:
                self.blocked.discard(station_id)
            self._dirty = True

    def is_blocked(self, station_id: str) -> bool:
        return station_id in self.blocked

    def put_key(self, rec: Dict[str, Any]) -> None:
        with self._lock:
            old = self.keys.get(rec["id"])
            if old is None or old["t"] < rec["t"]:
                self.keys[rec["id"]] = rec
                if len(self.keys) > KEYS_CAP:
                    for i in sorted(self.keys, key=lambda k: self.keys[k]["t"])[:len(self.keys) - KEYS_CAP]:
                        del self.keys[i]
                self._dirty = True

    def set_cursor(self, hub: str, value: float) -> None:
        with self._lock:
            self.cursors[hub] = value
            if len(self.cursors) > 50:
                for k in list(self.cursors)[:len(self.cursors) - 50]:
                    del self.cursors[k]
            self._dirty = True

    def sweep(self, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            for peer in list(self.threads):
                self.threads[peer] = [e for e in self.threads[peer] if now - e["t"] <= LOCAL_TTL_S]
                if not self.threads[peer]:
                    del self.threads[peer]
            self._mids = {e["mid"] for lst in self.threads.values() for e in lst}
            self._dirty = True

    def save(self, force: bool = True, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            if not self._dirty or (not force and now - self._last_save < SAVE_INTERVAL_S):
                return
            payload = {"threads": self.threads, "blocked": sorted(self.blocked), "keys": self.keys, "cursors": self.cursors}
            self._dirty = False
            self._last_save = now
            try:
                _write_json(self.path, payload)
            except OSError:
                self._dirty = True
