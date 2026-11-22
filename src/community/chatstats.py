"""Chat statistics (data/community_chat_stats.json): counters only, never message content, recipients or text.
Totals since start, per-day buckets for the last 30 days and per-peer-hub counters. Saved atomically, at most every 30 s.
See docs/COMMUNITY.md ("Chat")."""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

from src.community import chat as chatmod

DAYS = 30
PEERS_MAX = 100
COUNTERS = ("sent_public", "sent_dm", "recv_public", "recv_dm", "send_fail",
            "hub_accepted", "hub_pulled", "hub_served",
            "refused_rate", "refused_unknown", "refused_sig", "refused_old", "refused_cap")
PEER_FIELDS = ("pulled", "served", "send_fail", "pull_fail")


def _day(now: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(now))


def _int(d: Any, k: str) -> int:
    v = d.get(k) if isinstance(d, dict) else 0
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 10 ** 12 else 0


class ChatStats:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        data = chatmod._read_json(path)
        self.since = float(data["since"]) if isinstance(data.get("since"), (int, float)) else time.time()
        self.totals: Dict[str, int] = {k: _int(data.get("totals"), k) for k in COUNTERS}
        self.days: Dict[str, Dict[str, int]] = {}
        if isinstance(data.get("days"), dict):
            for d, c in data["days"].items():
                if isinstance(d, str) and len(d) == 10 and isinstance(c, dict):
                    self.days[d] = {k: _int(c, k) for k in COUNTERS if _int(c, k)}
        self.peers: Dict[str, Dict[str, Any]] = {}
        if isinstance(data.get("peers"), dict):
            for o, p in list(data["peers"].items())[:PEERS_MAX]:
                if isinstance(o, str) and isinstance(p, dict):
                    last = p.get("last_ok")
                    self.peers[o[:200]] = dict({k: _int(p, k) for k in PEER_FIELDS},
                                               last_ok=float(last) if isinstance(last, (int, float)) else 0.0)
        self._dirty = False
        self._last_save = 0.0

    def inc(self, name: str, n: int = 1, now: Optional[float] = None) -> None:
        if n <= 0 or name not in COUNTERS:
            return
        now = time.time() if now is None else now
        with self._lock:
            self.totals[name] += n
            bucket = self.days.setdefault(_day(now), {})
            bucket[name] = bucket.get(name, 0) + n
            if len(self.days) > DAYS:
                for d in sorted(self.days)[:len(self.days) - DAYS]:
                    del self.days[d]
            self._dirty = True

    def peer(self, origin: str, field: str, n: int = 1, ok: bool = False, now: Optional[float] = None) -> None:
        """Count `n` on a peer hub (origin, or a station id when no address is known); `ok` stamps the last success."""
        if field not in PEER_FIELDS or not origin:
            return
        now = time.time() if now is None else now
        with self._lock:
            p = self.peers.get(origin)
            if p is None:
                if len(self.peers) >= PEERS_MAX:
                    del self.peers[min(self.peers, key=lambda k: self.peers[k]["last_ok"])]
                p = self.peers[origin] = dict({k: 0 for k in PEER_FIELDS}, last_ok=0.0)
            p[field] += max(n, 0)
            if ok:
                p["last_ok"] = now
            self._dirty = True

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            days = {d: dict(c) for d, c in sorted(self.days.items())[-DAYS:]}
            return {"since": self.since, "totals": dict(self.totals), "days": days,
                    "peers": {o: dict(p) for o, p in self.peers.items()}}

    def save(self, force: bool = True, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            if not self._dirty or (not force and now - self._last_save < chatmod.SAVE_INTERVAL_S):
                return
            payload = {"since": self.since, "totals": self.totals, "days": self.days, "peers": self.peers}
            self._dirty = False
            self._last_save = now
            try:
                chatmod._write_json(self.path, payload)
            except OSError:
                self._dirty = True
