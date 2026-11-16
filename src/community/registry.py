"""Thread-safe store of verified station cards, persisted atomically to <data dir>/community_peers.json."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlsplit

from src.community.card import CardError, seed_origin, verify

logger = logging.getLogger("imx335.community")

MAX_AGE_S = 30 * 86400
MAX_CARDS = 500
NEW_ID_FUTURE_S = 60          # a first-time id may not claim to be more than a minute ahead of our clock
SAVE_INTERVAL_S = 30          # the file is rewritten at most this often (marked dirty in between)
# Origins that answered a gossip round, newest first. They expire after 180 days without an answer (the cap drops the
# oldest first), so a station that was off for longer than a card lives can still rejoin when the seed stations are down.
MAX_CONTACTS = 50
CONTACT_MAX_AGE_S = 180 * 86400
MAX_CONTACTS_PER_SUFFIX = 2   # per registrable host suffix (last two labels): one domain cannot fill the list


def _no_constant(name: str) -> Any:
    raise ValueError(f"{name} is not valid JSON")


def _suffix(origin: str) -> str:
    host = urlsplit(origin).hostname or ""
    if ":" in host or host.replace(".", "").isdigit():
        return host  # IP literal: its own group
    return ".".join(host.split(".")[-2:])


class Registry:
    """Cards must already be verified (card.verify) by the caller; the registry only keeps the newest per id.

    Local metadata (never part of a card or the /peers payload): `first_seen` per id and whether the id was ever
    refreshed. When the registry is full, ids that were never refreshed and were seen last are dropped first, so a flood
    of new ids cannot push out stations that have been known for long."""

    def __init__(self, path: str, own_id: Optional[str] = None):
        self.path = path
        self.own_id = own_id
        self._lock = threading.Lock()
        self._cards: Dict[str, Dict[str, Any]] = {}
        self._first_seen: Dict[str, int] = {}
        self._refreshed: Set[str] = set()
        self._contacts: Dict[str, int] = {}  # origin -> last time it answered
        self._seen: Dict[str, int] = {}      # id -> last time that station itself reached us (announce) or answered us
        self._dirty = False
        self._last_save = 0.0
        self.version = 0  # bumped whenever the card set changes (cache key for the /peers payload)

    def load(self, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f, parse_constant=_no_constant)
            cards = data.get("cards") if isinstance(data, dict) else None
            contacts = data.get("contacts") if isinstance(data, dict) else None
            first = data.get("first_seen") if isinstance(data, dict) else None
            seen = data.get("seen") if isinstance(data, dict) else None
            refreshed = data.get("refreshed") if isinstance(data, dict) else None
        except (OSError, ValueError):
            return
        if isinstance(contacts, dict):
            with self._lock:
                for url, ts in contacts.items():
                    if isinstance(url, str) and isinstance(ts, (int, float)) and not isinstance(ts, bool):
                        self._add_contact(url, int(ts), now)
        for card in cards if isinstance(cards, list) else []:
            try:
                self.merge(verify(card, now), now)  # persisted cards are re-verified like any other input
            except CardError:
                continue
        with self._lock:  # restore the local metadata for the ids that survived
            if isinstance(first, dict):
                for cid, ts in first.items():
                    if cid in self._cards and isinstance(ts, (int, float)) and not isinstance(ts, bool):
                        self._first_seen[cid] = int(ts)
            if isinstance(refreshed, list):
                self._refreshed |= {c for c in refreshed if isinstance(c, str) and c in self._cards}
            if isinstance(seen, dict):
                for cid, ts in seen.items():
                    if cid in self._cards and isinstance(ts, (int, float)) and not isinstance(ts, bool):
                        self._seen[cid] = int(ts)
            self._dirty = False

    def merge(self, card: Dict[str, Any], now: Optional[float] = None) -> bool:
        """Keep `card` if it is newer than what we hold for that id. Own card and expired cards are ignored."""
        now = time.time() if now is None else now
        cid = card.get("id")
        if not isinstance(cid, str) or cid == self.own_id or card.get("updated", 0) < now - MAX_AGE_S:
            return False
        with self._lock:
            old = self._cards.get(cid)
            if old is None and card.get("updated", 0) > now + NEW_ID_FUTURE_S:
                return False
            if old is not None and old.get("updated", 0) >= card.get("updated", 0):
                return False
            self._cards[cid] = card
            if old is None:
                self._first_seen.setdefault(cid, int(now))
            else:
                self._refreshed.add(cid)
            self._trim(now)
            self._dirty = True
            self.version += 1
            return True

    def _trim(self, now: float) -> None:
        for cid in [c for c, v in self._cards.items() if v.get("updated", 0) < now - MAX_AGE_S]:
            del self._cards[cid]
        if len(self._cards) > MAX_CARDS:
            # keep: refreshed ids first, then the longest known (smallest first_seen); drop the rest
            keep = sorted(self._cards, key=lambda c: (c not in self._refreshed, self._first_seen.get(c, now)))[:MAX_CARDS]
            self._cards = {c: self._cards[c] for c in keep}
        for cid in [c for c in self._first_seen if c not in self._cards]:
            del self._first_seen[cid]
        for cid in [c for c in self._seen if c not in self._cards]:
            del self._seen[cid]
        self._refreshed &= set(self._cards)

    def _add_contact(self, url: str, ts: int, now: float) -> None:
        """Caller holds the lock. Origins only; stale ones are ignored; at most 2 per host suffix; capped."""
        try:
            origin = seed_origin(url)
        except CardError:
            return
        if ts < now - CONTACT_MAX_AGE_S:
            return
        if origin not in self._contacts:
            sfx = _suffix(origin)
            if sum(1 for o in self._contacts if _suffix(o) == sfx) >= MAX_CONTACTS_PER_SUFFIX:
                return
        self._contacts[origin] = max(ts, self._contacts.get(origin, 0))
        if len(self._contacts) > MAX_CONTACTS:
            newest = sorted(self._contacts.items(), key=lambda kv: kv[1], reverse=True)[:MAX_CONTACTS]
            self._contacts = dict(newest)

    def note_seen(self, cid: str, now: Optional[float] = None) -> None:
        """Record that station `cid` itself just reached us (its announce) or answered us. Local metadata, not a card
        field: a card's `updated` only says when it was last signed (up to 6 h ago for a station that is running fine)."""
        now = time.time() if now is None else now
        with self._lock:
            if cid in self._cards and cid != self.own_id:
                self._seen[cid] = int(now)
                self._dirty = True

    def seen_map(self) -> Dict[str, int]:
        with self._lock:
            return dict(self._seen)

    def note_contact(self, url: str, now: Optional[float] = None) -> None:
        """Remember that `url` answered a round (the caller has already passed it through the SSRF guard)."""
        if not isinstance(url, str) or not 0 < len(url) <= 300:
            return
        now = time.time() if now is None else now
        with self._lock:
            self._add_contact(url, int(now), now)
            self._dirty = True

    def contacts(self, now: Optional[float] = None) -> List[str]:
        """Remembered origins that answered within 180 days, most recent first."""
        now = time.time() if now is None else now
        with self._lock:
            for o in [o for o, t in self._contacts.items() if t < now - CONTACT_MAX_AGE_S]:
                del self._contacts[o]
            return [u for u, _ in sorted(self._contacts.items(), key=lambda kv: kv[1], reverse=True)]

    def cards(self, now: Optional[float] = None) -> List[Dict[str, Any]]:
        with self._lock:
            before = len(self._cards)
            self._trim(time.time() if now is None else now)
            if len(self._cards) != before:
                self.version += 1
            return list(self._cards.values())

    def get(self, cid: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._cards.get(cid)

    def count(self) -> int:
        with self._lock:
            return len(self._cards)

    def clear(self) -> None:
        with self._lock:
            self._cards.clear()
            self._first_seen.clear()
            self._refreshed.clear()
            self._contacts.clear()
            self._seen.clear()
            self._dirty = True
            self.version += 1

    def save(self, force: bool = True, now: Optional[float] = None) -> None:
        """Write to disk when something changed (temp file + rename, so a crash never leaves half a file).
        force=False writes at most once per SAVE_INTERVAL_S (cheap to call from a request path)."""
        now = time.time() if now is None else now
        with self._lock:
            if not self._dirty or (not force and now - self._last_save < SAVE_INTERVAL_S):
                return
            payload = json.dumps({"cards": list(self._cards.values()), "contacts": self._contacts,
                                  "first_seen": self._first_seen, "refreshed": sorted(self._refreshed),
                                  "seen": self._seen},
                                 separators=(",", ":"), allow_nan=False)
            self._dirty = False
            self._last_save = now
        tmp = f"{self.path}.tmp"
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp, self.path)
        except OSError as exc:
            logger.warning("Could not save community peers: %s", exc)
            self._dirty = True
