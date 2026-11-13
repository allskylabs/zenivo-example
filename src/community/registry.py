"""Thread-safe store of verified station cards, persisted atomically to <data dir>/community_peers.json."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("imx335.community")

MAX_AGE_S = 7 * 86400
MAX_CARDS = 2000


class Registry:
    """Cards must already be verified (card.verify) by the caller; the registry only keeps the newest per id."""

    def __init__(self, path: str, own_id: Optional[str] = None):
        self.path = path
        self.own_id = own_id
        self._lock = threading.Lock()
        self._cards: Dict[str, Dict[str, Any]] = {}
        self._dirty = False

    def load(self, now: Optional[float] = None) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            cards = data.get("cards") if isinstance(data, dict) else None
        except (OSError, ValueError):
            return
        from src.community.card import CardError, verify  # persisted cards are re-verified like any other input
        for card in cards if isinstance(cards, list) else []:
            try:
                self.merge(verify(card, now), now)
            except CardError:
                continue
        self._dirty = False

    def merge(self, card: Dict[str, Any], now: Optional[float] = None) -> bool:
        """Keep `card` if it is newer than what we hold for that id. Own card and expired cards are ignored."""
        now = time.time() if now is None else now
        cid = card.get("id")
        if not isinstance(cid, str) or cid == self.own_id or card.get("updated", 0) < now - MAX_AGE_S:
            return False
        with self._lock:
            old = self._cards.get(cid)
            if old is not None and old.get("updated", 0) >= card.get("updated", 0):
                return False
            self._cards[cid] = card
            self._trim(now)
            self._dirty = True
            return True

    def _trim(self, now: float) -> None:
        for cid in [c for c, v in self._cards.items() if v.get("updated", 0) < now - MAX_AGE_S]:
            del self._cards[cid]
        if len(self._cards) > MAX_CARDS:
            newest = sorted(self._cards.items(), key=lambda kv: kv[1].get("updated", 0), reverse=True)[:MAX_CARDS]
            self._cards = dict(newest)

    def cards(self, now: Optional[float] = None) -> List[Dict[str, Any]]:
        with self._lock:
            self._trim(time.time() if now is None else now)
            return list(self._cards.values())

    def count(self) -> int:
        with self._lock:
            return len(self._cards)

    def clear(self) -> None:
        with self._lock:
            self._cards.clear()
            self._dirty = True

    def save(self) -> None:
        """Write to disk when something changed (temp file + rename, so a crash never leaves half a file)."""
        with self._lock:
            if not self._dirty:
                return
            payload = json.dumps({"cards": list(self._cards.values())}, separators=(",", ":"))
            self._dirty = False
        tmp = f"{self.path}.tmp"
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp, self.path)
        except OSError as exc:
            logger.warning("Could not save community peers: %s", exc)
            self._dirty = True
