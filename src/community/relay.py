"""Store-and-forward preview relay: a station without a public URL pushes its large preview to stations that opted in
(`relay_previews`); those re-encode it, keep it for 24 h and serve it publicly. See docs/COMMUNITY.md.

Everything here is pure (no network, no manager): signing, verification, image re-encoding and the on-disk store."""
from __future__ import annotations

import base64
import hashlib
import io
import os
import re
import tempfile
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from src.community.identity import verify_signature

DOMAIN = b"zenivo-relay-v1\n"
MAX_UPLOAD = 256 * 1024          # bytes accepted from a sender
SENDER_MAX = 200 * 1024          # bytes a sender aims for
TIME_SKEW_S = 300
MIN_INTERVAL_S = 10 * 60         # per station
TTL_S = 24 * 3600
MAX_STATIONS = 100
MAX_TOTAL = 25 * 1024 * 1024
MAX_SIDE = 1280
MIN_SIDE = 64
MAX_INPUT_PIXELS = 24_000_000    # header check before any pixel is decoded (decompression bombs)
ID_RE = re.compile(r"^[0-9a-f]{32}$")


class RelayRefused(Exception):
    """The upload is not accepted; `status` is the HTTP status to answer with."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def message(station_id: str, t: int, sha256_hex: str) -> bytes:
    return DOMAIN + f"{station_id}\n{int(t)}\n{sha256_hex}".encode("ascii")


def sign_upload(identity, body: bytes, t: Optional[int] = None) -> Dict[str, str]:
    """Headers a sender attaches to an upload of `body` (the exact JPEG bytes)."""
    t = int(time.time()) if t is None else int(t)
    sig = identity.sign(message(identity.id, t, hashlib.sha256(body).hexdigest()))
    return {"X-Zenivo-Time": str(t), "X-Zenivo-Sig": base64.b64encode(sig).decode("ascii")}


def verify_upload(pubkey_b64: str, station_id: str, t_header: Optional[str], sig_header: Optional[str], body: bytes,
                  now: Optional[float] = None) -> bool:
    """True when the headers prove the holder of the card's key sent exactly `body` just now."""
    now = time.time() if now is None else now
    try:
        t = int(t_header or "")
        pub = base64.b64decode(pubkey_b64, validate=True)
        sig = base64.b64decode(sig_header or "", validate=True)
    except (ValueError, TypeError):
        return False
    if abs(now - t) > TIME_SKEW_S or len(sig) != 64 or len(pub) != 32:
        return False
    return verify_signature(pub, sig, message(station_id, t, hashlib.sha256(body).hexdigest()))


def _pil():
    from PIL import Image
    return Image


def reencode(data: bytes, max_side: int = MAX_SIDE, quality: int = 80) -> bytes:
    """Decode an uploaded image and write a fresh JPEG: no metadata, at most `max_side` px on the long side.
    Raises RelayRefused(422) when it is not a usable picture. The header is read first so a decompression bomb is
    refused before any pixel is decoded."""
    Image = _pil()
    Image.MAX_IMAGE_PIXELS = MAX_INPUT_PIXELS
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.format != "JPEG":
                raise RelayRefused(422, "Only JPEG images are accepted")
            w, h = im.size
            if min(w, h) < MIN_SIDE or w * h > MAX_INPUT_PIXELS or max(w, h) > 12000:
                raise RelayRefused(422, "Image dimensions are not acceptable")
            im.draft("RGB", (max_side, max_side))  # JPEG: decode at a reduced size
            im = im.convert("RGB")
            im.thumbnail((max_side, max_side), Image.LANCZOS)
            if min(im.size) < 1:
                raise RelayRefused(422, "Image dimensions are not acceptable")
            out = io.BytesIO()
            im.save(out, "JPEG", quality=quality, optimize=True)  # no exif/icc/comment is passed on
            return out.getvalue()
    except RelayRefused:
        raise
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise RelayRefused(422, "Not a readable JPEG image")


def fit_for_upload(jpeg: bytes, limit: int = SENDER_MAX) -> Optional[bytes]:
    """Re-compress `jpeg` (the sender's 1280 px preview) until it is at most `limit` bytes."""
    for side, q in ((1280, 80), (1280, 70), (1024, 70), (800, 65), (640, 60), (480, 55)):
        try:
            out = reencode(jpeg, side, q)
        except RelayRefused:
            return None
        if len(out) <= limit:
            return out
    return None


class RelayStore:
    """data/community_relay/<station id>.jpg, atomic writes, bounded count/size, TTL. Modification time = last update."""

    def __init__(self, folder: str, max_stations: int = MAX_STATIONS, max_total: int = MAX_TOTAL, ttl: float = TTL_S):
        self.folder = folder
        self.max_stations = max_stations
        self.max_total = max_total
        self.ttl = ttl
        self._lock = threading.Lock()
        self.version = 0

    def _path(self, station_id: str) -> Optional[str]:
        return os.path.join(self.folder, station_id + ".jpg") if ID_RE.match(station_id or "") else None

    def _entries(self) -> List[Tuple[str, float, int]]:
        """(id, mtime, size) of every stored image; strays (wrong names, temp files) are not listed."""
        out = []
        try:
            names = os.listdir(self.folder)
        except OSError:
            return out
        for n in names:
            if n.endswith(".jpg") and ID_RE.match(n[:-4]):
                try:
                    st = os.stat(os.path.join(self.folder, n))
                except OSError:
                    continue
                out.append((n[:-4], st.st_mtime, st.st_size))
        return out

    def put(self, station_id: str, data: bytes, now: Optional[float] = None) -> None:
        path = self._path(station_id)
        if path is None or len(data) > MAX_UPLOAD or len(data) > self.max_total:
            raise ValueError("bad relay item")
        now = time.time() if now is None else now
        with self._lock:
            os.makedirs(self.folder, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.folder, prefix=".tmp-")
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                os.chmod(tmp, 0o640)
                os.utime(tmp, (now, now))
                os.replace(tmp, path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            entries = sorted(self._entries(), key=lambda e: e[1])  # oldest update first
            total = sum(e[2] for e in entries)
            while entries and (len(entries) > self.max_stations or total > self.max_total):
                victim = entries.pop(0)
                if victim[0] == station_id and entries:   # never evict the item just stored while others remain older
                    continue
                total -= victim[2]
                self._unlink(victim[0])
            self.version += 1

    def _unlink(self, station_id: str) -> None:
        p = self._path(station_id)
        if p:
            try:
                os.unlink(p)
            except OSError:
                pass

    def get(self, station_id: str) -> Optional[bytes]:
        p = self._path(station_id)
        if p is None:
            return None
        try:
            with open(p, "rb") as f:
                return f.read(MAX_UPLOAD + 1)
        except OSError:
            return None

    def updated(self, station_id: str) -> Optional[float]:
        p = self._path(station_id)
        try:
            return os.stat(p).st_mtime if p else None
        except OSError:
            return None

    def held(self) -> Dict[str, int]:
        """{station id: unix time of the stored image}."""
        return {i: int(m) for i, m, _ in self._entries()}

    def sweep(self, keep: Optional[Callable[[str], bool]] = None, now: Optional[float] = None) -> int:
        """Delete files older than the TTL and those `keep(id)` refuses (card gone or got a url); also stray temp files."""
        now = time.time() if now is None else now
        removed = 0
        with self._lock:
            for sid, mtime, _ in self._entries():
                if now - mtime > self.ttl or (keep is not None and not keep(sid)):
                    self._unlink(sid)
                    removed += 1
            try:
                for n in os.listdir(self.folder):
                    if n.startswith(".tmp-"):
                        try:
                            if now - os.stat(os.path.join(self.folder, n)).st_mtime > 600:
                                os.unlink(os.path.join(self.folder, n))
                        except OSError:
                            pass
            except OSError:
                pass
            if removed:
                self.version += 1
        return removed

    def clear(self) -> None:
        with self._lock:
            for sid, _, _ in self._entries():
                self._unlink(sid)
            self.version += 1
