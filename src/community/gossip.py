"""Gossip worker: every ~15 minutes ask a few stations for their cards and tell them ours.

Per-peer failures are expected (stations go offline, sit behind NAT): they are counted and logged at debug level,
never raised. The worker only exists while Community is enabled."""
from __future__ import annotations

import logging
import os
import random
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.community import DEFAULT_SEEDS
from src.community.card import CardError, verify
from src.community.netguard import fetch_json

logger = logging.getLogger("imx335.community")

INTERVAL_S = 15 * 60
FIRST_ROUND_S = 20
JITTER = 0.2
MAX_TARGETS = 5
MIN_RANDOM_PEERS = 2
MAX_CARDS_PER_REPLY = 2000


def _norm(url: str) -> str:
    return url.strip().rstrip("/").lower()


class GossipWorker:
    def __init__(self, manager: Any, fetch: Callable[..., Any] = fetch_json):
        self.manager = manager
        self._fetch = fetch
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._running_round = threading.Lock()
        self.last_round: Dict[str, Any] = {}

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if os.environ.get("PYTEST_CURRENT_TEST"):  # tests never gossip with the real network
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(self._stop, self._wake), name="community-gossip", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self._thread = None

    def trigger(self) -> None:
        """Run a round soon (after the settings changed)."""
        self._wake.set()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self, stop: threading.Event, wake: threading.Event) -> None:
        delay = FIRST_ROUND_S * random.uniform(1 - JITTER, 1 + JITTER)
        while not stop.is_set():
            wake.wait(delay)
            if stop.is_set():
                break
            wake.clear()
            try:
                self.run_round()
            except Exception:  # a bug must not kill the worker
                logger.exception("Community round failed")
            delay = INTERVAL_S * random.uniform(1 - JITTER, 1 + JITTER)

    # -- one round ---------------------------------------------------------
    def targets(self) -> List[Tuple[str, bool]]:
        """(url, allow_private): all owner seeds (private allowed), the default seeds, then random known peers.

        Known peers = stations with a url in the registry plus the remembered addresses that answered before
        (kept past the card expiry, so the network survives without the seeds)."""
        settings = self.manager.settings()
        own = _norm(settings.get("public_url") or "")
        out: List[Tuple[str, bool]] = []
        seen = {own} if own else set()
        for url in settings.get("seeds") or []:
            if _norm(url) not in seen:
                seen.add(_norm(url))
                out.append((url, True))
        for url in DEFAULT_SEEDS:
            if _norm(url) not in seen:
                seen.add(_norm(url))
                out.append((url, False))
        peers: List[str] = []
        for u in [c.get("url") for c in self.manager.registry_cards()] + self.manager.contacts():
            if u and _norm(u) not in seen:
                seen.add(_norm(u))
                peers.append(u)
        random.shuffle(peers)
        out += [(u, False) for u in peers[:max(MIN_RANDOM_PEERS, MAX_TARGETS - len(out))]]
        return out

    def run_round(self) -> Dict[str, Any]:
        with self._running_round:
            own = self.manager.own_card()
            contacted = ok = failed = new = 0
            for url, allow_private in self.targets():
                contacted += 1
                base = url.rstrip("/")
                try:
                    reply = self._fetch(base + "/api/community/peers", allow_private=allow_private)
                    cards = reply.get("cards") if isinstance(reply, dict) else None
                    if not isinstance(cards, list):
                        raise ValueError("no cards")
                    for card in cards[:MAX_CARDS_PER_REPLY]:
                        try:
                            if self.manager.merge_card(verify(card)):
                                new += 1
                        except CardError:
                            continue
                    ok += 1
                    if not allow_private:  # owner seeds stay in the settings; only strictly checked addresses are remembered
                        self.manager.note_contact(base)
                except Exception as exc:
                    failed += 1
                    logger.debug("Community peer %s failed: %s", url, exc)
                    continue
                if own is not None:
                    try:
                        self._fetch(base + "/api/community/announce", allow_private=allow_private, method="POST", payload=own)
                    except Exception as exc:
                        logger.debug("Community announce to %s failed: %s", url, exc)
            self.manager.save_registry()
            self.last_round = {"time": int(time.time()), "contacted": contacted, "ok": ok, "failed": failed, "new": new}
            return dict(self.last_round)
