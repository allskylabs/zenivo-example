"""Station card: the small signed JSON object stations exchange. Verification is strict because every card
arrives from an untrusted station and is later rendered in other people's browsers."""
from __future__ import annotations

import base64
import binascii
import json
import math
import re
import time
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

from src.community import CARD_VERSION
from src.community.identity import Identity, station_id, verify_signature

MAX_CARD_BYTES = 16 * 1024
MAX_NAME = 60
MAX_URL = 200
MAX_SHORT = 60          # camera, software
MAX_THUMB_BYTES = 8 * 1024
FUTURE_SKEW_S = 120     # `updated` may be at most 2 minutes ahead of our clock
PRECISIONS = (0.0, 0.01, 0.1, 1.0)  # 0 = exact
THUMB_PREFIXES = ("data:image/jpeg;base64,", "data:image/webp;base64,")
_FIELDS = {"v", "id", "pubkey", "name", "lat", "lon", "url", "camera", "software", "updated", "thumb", "open", "preview", "sig"}
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_B64 = re.compile(r"^[A-Za-z0-9+/]*={0,2}$")


class CardError(ValueError):
    """The card is malformed, too big, not self-consistent or badly signed."""


def canonical(card: Dict[str, Any]) -> bytes:
    """Bytes that are signed: the card without `sig`, sorted keys, no whitespace, UTF-8."""
    body = {k: v for k, v in card.items() if k != "sig"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def snap(value: float, precision_deg: float) -> float:
    """Round a coordinate to the sharing grid (0.1 deg is about 10 km); 0 keeps it exact."""
    if not precision_deg:
        return round(float(value), 5)
    return round(round(float(value) / precision_deg) * precision_deg, 4)


def build_card(identity: Identity, *, name: str, lat: float, lon: float, precision_deg: float, url: Optional[str],
               camera: Optional[str], software: str, thumb: Optional[str] = None,
               updated: Optional[float] = None, allow_open: bool = True, preview: bool = False) -> Dict[str, Any]:
    if precision_deg not in PRECISIONS:
        raise CardError("precision_deg must be 0, 0.01, 0.1 or 1.0")
    card: Dict[str, Any] = {
        "v": CARD_VERSION,
        "id": identity.id,
        "pubkey": base64.b64encode(identity.pubkey).decode("ascii"),
        "name": name,
        "lat": snap(lat, precision_deg),
        "lon": snap(lon, precision_deg),
        "url": to_origin(url) if url else None,
        "camera": camera or None,
        "software": software,
        "updated": int(updated if updated is not None else time.time()),
    }
    if thumb:
        card["thumb"] = thumb
    # What the owner allows map visitors to do with a station that has a public URL (absent = open yes, preview no)
    if url and not allow_open:
        card["open"] = False     # do not offer an "Open station" link
    if url and preview:
        card["preview"] = True   # <url>/api/community/preview.jpg serves a large image
    card["sig"] = base64.b64encode(identity.sign(canonical(card))).decode("ascii")
    verify(card)  # never publish a card that other stations would reject
    return card


def _text(card: Dict[str, Any], key: str, max_len: int, required: bool) -> Optional[str]:
    val = card.get(key)
    if val is None:
        if required:
            raise CardError(f"{key} is required")
        return None
    if not isinstance(val, str) or len(val) > max_len or (required and not val.strip()) or _CONTROL.search(val):
        raise CardError(f"{key} is invalid")
    return val


def _b64(val: Any, size: int, what: str) -> bytes:
    if not isinstance(val, str) or len(val) > 200 or not _B64.match(val):
        raise CardError(f"{what} is invalid")
    try:
        raw = base64.b64decode(val, validate=True)
    except (binascii.Error, ValueError):
        raise CardError(f"{what} is invalid")
    if len(raw) != size:
        raise CardError(f"{what} has the wrong length")
    return raw


def _origin(url: str, *, strict: bool, allow_http: bool = False) -> str:
    """`https://host[:port]` (lower-case host) for `url`. strict: only an origin is accepted (path "" or "/", no query,
    fragment or credentials); otherwise a path, query or fragment is dropped."""
    if not isinstance(url, str) or len(url) > MAX_URL or _CONTROL.search(url) or " " in url:
        raise CardError("url is invalid")
    try:
        parts = urlsplit(url)
        port = parts.port  # raises ValueError for a bad port
    except ValueError:
        raise CardError("url is invalid")
    if parts.scheme.lower() not in (("https", "http") if allow_http else ("https",)) or not parts.hostname \
            or parts.username is not None or parts.password is not None:
        raise CardError("url must be https without credentials")
    if strict and (parts.path not in ("", "/") or parts.query or parts.fragment or "?" in url or "#" in url):
        raise CardError("url must be an origin (https://host[:port]) without path, query or fragment")
    scheme = parts.scheme.lower()
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    default = 443 if scheme == "https" else 80
    return f"{scheme}://{host}" + (f":{port}" if port and port != default else "")


def check_public_url(url: str) -> str:
    """Structure check for the URL carried in a card: an https ORIGIN, no credentials. Returns it normalised.
    (Whether the host is reachable and public is the SSRF guard's job, at fetch time.)"""
    return _origin(url, strict=True)


def to_origin(url: str) -> str:
    """The owner's public URL reduced to its origin (a path or trailing slash is dropped, not an error)."""
    return _origin(url, strict=False)


def seed_origin(url: str) -> str:
    """An owner-typed seed address reduced to its origin; http is allowed (LAN tests)."""
    return _origin(url, strict=False, allow_http=True)


def verify(card: Any, now: Optional[float] = None) -> Dict[str, Any]:
    """Return the card when it is valid, else raise CardError."""
    if not isinstance(card, dict):
        raise CardError("card must be an object")
    if set(card) - _FIELDS:
        raise CardError("unknown field")
    try:
        size = len(canonical(card)) + 100
    except (TypeError, ValueError):
        raise CardError("card is not JSON")
    if size > MAX_CARD_BYTES:
        raise CardError("card is too large")
    if card.get("v") != CARD_VERSION or isinstance(card.get("v"), bool):
        raise CardError("unsupported version")
    for flag in ("open", "preview"):
        if flag in card and not isinstance(card[flag], bool):
            raise CardError(f"{flag} must be true or false")
    _text(card, "name", MAX_NAME, True)
    _text(card, "camera", MAX_SHORT, False)
    _text(card, "software", MAX_SHORT, True)
    for key, lim in (("lat", 90.0), ("lon", 180.0)):
        val = card.get(key)
        if (isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val)
                or not -lim <= val <= lim):
            raise CardError(f"{key} is out of range")
    updated = card.get("updated")
    if isinstance(updated, bool) or not isinstance(updated, (int, float)) or not math.isfinite(updated) or updated < 0:
        raise CardError("updated is invalid")
    if updated > (time.time() if now is None else now) + FUTURE_SKEW_S:
        raise CardError("updated is in the future")
    url = card.get("url")
    if url is not None:
        if not isinstance(url, str):
            raise CardError("url is invalid")
        check_public_url(url)
    thumb = card.get("thumb")
    if thumb is not None:
        if (not isinstance(thumb, str) or not thumb.startswith(THUMB_PREFIXES)
                or len(thumb) > MAX_THUMB_BYTES * 4 // 3 + 64):
            raise CardError("thumb is invalid")
        if not _B64.match(thumb.split(",", 1)[1]):
            raise CardError("thumb is invalid")
    pub = _b64(card.get("pubkey"), 32, "pubkey")
    if card.get("id") != station_id(pub):
        raise CardError("id does not match the public key")
    sig = _b64(card.get("sig"), 64, "sig")
    if not verify_signature(pub, sig, canonical(card)):
        raise CardError("bad signature")
    return card
