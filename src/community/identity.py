"""Station identity: an Ed25519 keypair kept in <data dir>/community_key.pem (mode 0600).

The station id is the first 16 bytes of sha256(public key) in hex, so an id cannot be claimed without the key.
If the `cryptography` package is missing the feature reports unavailable and the rest of Zenivo keeps working."""
from __future__ import annotations

import hashlib
import os
import threading
from typing import Optional, Tuple

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
    _IMPORT_ERROR: Optional[str] = None
except Exception as exc:  # ImportError or a broken native library
    _IMPORT_ERROR = f"The 'cryptography' package is not available ({exc}). Install it with: pip install cryptography"


def availability() -> Tuple[bool, str]:
    """(available, reason): reason is empty when available."""
    return (_IMPORT_ERROR is None, _IMPORT_ERROR or "")


def station_id(pubkey: bytes) -> str:
    return hashlib.sha256(pubkey).digest()[:16].hex()


def verify_signature(pubkey: bytes, signature: bytes, message: bytes) -> bool:
    """True only for a valid Ed25519 signature; every malformed input is simply False."""
    if _IMPORT_ERROR is not None:
        return False
    try:
        Ed25519PublicKey.from_public_bytes(pubkey).verify(signature, message)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


class Identity:
    """The private key of this station. Created on first use; the file is written 0600 before it holds any key."""

    def __init__(self, key_path: str):
        if _IMPORT_ERROR is not None:
            raise RuntimeError(_IMPORT_ERROR)
        self.key_path = key_path
        self._lock = threading.Lock()
        self._key = self._load_or_create()
        self.pubkey: bytes = self._key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.id: str = station_id(self.pubkey)

    def _load_or_create(self):
        if os.path.isfile(self.key_path):
            with open(self.key_path, "rb") as f:
                return serialization.load_pem_private_key(f.read(), password=None)
        key = Ed25519PrivateKey.generate()
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
        os.makedirs(os.path.dirname(self.key_path) or ".", exist_ok=True)
        fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(pem)
        return key

    def sign(self, message: bytes) -> bytes:
        with self._lock:
            return self._key.sign(message)
