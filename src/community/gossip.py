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
from src.community.card import CardError, seed_origin, verify
from src.community.netguard import fetch_json

logger = logging.getLogger("imx335.community")

INTERVAL_S = 15 * 60
FIRST_ROUND_S = 20
JITTER = 0.2
MAX_TARGETS = 5
MIN_RANDOM_PEERS = 2
MAX_CARDS_PER_REPLY = 2000


def _norm(url: str) -> str:
    """Comparison form of an address: its origin, lower-case (anything unparsable is compared as typed)."""
    try:
        return seed_origin(url.strip())
    except CardError:
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
        # Per-address outcome of the last round and a short running history: what the owner sees in Settings
        self.last_targets: List[Dict[str, Any]] = []
        self.health: Dict[str, Dict[str, Any]] = {}

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
        """(url, allow_private): the seed list from the settings, then random known peers.

        Known peers = stations with a url in the registry plus the remembered addresses that answered before
        (kept past the card expiry, so the network survives without the seeds)."""
        settings = self.manager.settings()
        own = _norm(settings.get("public_url") or "")
        out: List[Tuple[str, bool]] = []
        seen = {own} if own else set()
        for url in settings.get("seeds") or []:  # the owner's list; it starts with the built-in seeds, which can be removed
            if _norm(url) not in seen:
                seen.add(_norm(url))
                out.append((url, url not in DEFAULT_SEEDS))  # typed addresses may be private (LAN); built-in ones are checked strictly
        peers: List[str] = []
        for u in [c.get("url") for c in self.manager.registry_cards()] + self.manager.contacts():
            if u and _norm(u) not in seen:
                seen.add(_norm(u))
                peers.append(u)
        random.shuffle(peers)
        out += [(u, False) for u in peers[:max(MIN_RANDOM_PEERS, MAX_TARGETS - len(out))]]
        return out

    def _note(self, url: str, ok: bool, now: int) -> Dict[str, Any]:
        """Running history per address (bounded): answers, failures, current failure streak."""
        h = self.health.setdefault(url, {"ok": 0, "failed": 0, "streak": 0, "last_ok": None})
        if ok:
            h["ok"] += 1
            h["streak"] = 0
            h["last_ok"] = now
        else:
            h["failed"] += 1
            h["streak"] += 1
        if len(self.health) > 200:
            for k in list(self.health)[:len(self.health) - 200]:
                del self.health[k]
        return h

    def run_round(self) -> Dict[str, Any]:
        with self._running_round:
            own = self.manager.own_card()
            own_id = own.get("id") if own else None
            seeds = {_norm(u) for u in self.manager.settings().get("seeds") or []}
            contacted = ok = failed = new = 0
            results: List[Dict[str, Any]] = []
            for url, allow_private in self.targets():
                contacted += 1
                try:
                    base = seed_origin(url)  # gossip targets are origins: never a path the owner or a peer smuggled in
                except CardError:
                    base = url.rstrip("/")
                res: Dict[str, Any] = {"url": base, "role": "seed" if _norm(url) in seeds else "station", "ok": False,
                                       "self": False, "cards": 0, "announced": False, "error": "", "ms": None}
                t0 = time.monotonic()
                try:
                    reply = self._fetch(base + "/api/community/peers", allow_private=allow_private)
                    res["ms"] = int((time.monotonic() - t0) * 1000)
                    cards = reply.get("cards") if isinstance(reply, dict) else None
                    if not isinstance(cards, list):
                        raise ValueError("no cards")
                    first = True
                    for card in cards[:MAX_CARDS_PER_REPLY]:
                        try:
                            good = verify(card)
                        except CardError:
                            continue
                        res["cards"] += 1
                        # The first card of a reply is the replying station's own card; every peer that knows us
                        # also carries OUR card further down, which says nothing about who answered.
                        if first and own_id and good.get("id") == own_id:
                            res["self"] = True  # this address is this very station (its own public URL)
                        first = False
                        if self.manager.merge_card(good):
                            new += 1
                    ok += 1
                    res["ok"] = True
                    if not allow_private:  # owner seeds stay in the settings; only strictly checked addresses are remembered
                        self.manager.note_contact(base)
                except Exception as exc:
                    failed += 1
                    res["ms"] = int((time.monotonic() - t0) * 1000)
                    res["error"] = (str(exc) or exc.__class__.__name__)[:120]
                    logger.debug("Community peer %s failed: %s", url, exc)
                if res["ok"] and own is not None:
                    try:
                        self._fetch(base + "/api/community/announce", allow_private=allow_private, method="POST", payload=own)
                        res["announced"] = True
                    except Exception as exc:
                        res["error"] = ("announce: " + (str(exc) or exc.__class__.__name__))[:120]
                        logger.debug("Community announce to %s failed: %s", url, exc)
                now = int(time.time())
                h = self._note(base, res["ok"], now)
                res.update(time=now, total_ok=h["ok"], total_failed=h["failed"], streak=h["streak"], last_ok=h["last_ok"])
                results.append(res)
            self.manager.save_registry()
            self.last_targets = results
            others = sum(1 for r in results if r["ok"] and not r["self"])
            self.last_round = {"time": int(time.time()), "contacted": contacted, "ok": ok, "failed": failed, "new": new,
                               "others_ok": others}
            return dict(self.last_round)
