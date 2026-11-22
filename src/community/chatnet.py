"""Chat client: talk to the hubs (seeds first, then stations that said `chat: true`). Every request goes through the
gossip worker's SSRF-guarded fetch. See docs/COMMUNITY.md ("Chat")."""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from src.community import DEFAULT_SEEDS
from src.community import chat as chatmod
from src.community.card import CardError, seed_origin

logger = logging.getLogger("imx335.community")

POLL_MIN_S = 30
KEYS_EVERY_S = 10 * 60
KEY_PUBLISH_EVERY_S = 12 * 3600
MAX_POLL_HUBS = 5
MAX_SEND_HUBS = 3
MAX_PAGES = 4        # pages of a poll per hub (a hub cuts a reply at 500 messages or 200 KB)


def _norm(url: str) -> str:
    try:
        return seed_origin(url.strip())
    except CardError:
        return url.strip().rstrip("/").lower()


class ChatNet:
    def __init__(self, manager: Any):
        self.m = manager
        self._poll_lock = threading.Lock()
        self._last_poll = 0.0
        self._last_keys = 0.0
        self._published: Dict[str, float] = {}
        self.last: Dict[str, Any] = {}

    def _fetch(self, *a: Any, **kw: Any) -> Any:
        return self.m.worker._fetch(*a, **kw)

    def hubs(self) -> List[Tuple[str, bool]]:
        """(origin, allow_private): the owner's seeds first, then stations that offered chat. A seed that said it has
        chat off (within the last day) is skipped."""
        settings = self.m.settings()
        own = _norm(settings.get("public_url") or "")
        known = self.m.chat_hubs_known()
        out: List[Tuple[str, bool]] = []
        seen = {own} if own else set()
        for url in settings.get("seeds") or []:
            try:
                base = seed_origin(url)
            except CardError:
                continue
            if base in seen:
                continue
            seen.add(base)
            if known.get(base, {}).get("on") is False:
                continue
            out.append((base, url not in DEFAULT_SEEDS))
        rest = sorted((k for k, v in known.items() if v["on"] and k not in seen), key=lambda k: -known[k]["t"])
        out += [(k, bool(known[k]["private"])) for k in rest]
        return out

    # -- reading --------------------------------------------------------------
    def poll(self, force: bool = False) -> Dict[str, Any]:
        """Fetch new messages from the hubs; at most once per 30 s unless `force`. Never raises on a network failure."""
        if not self.m.chat_on():
            return {}
        if not self._poll_lock.acquire(blocking=False):
            return self.last
        try:
            now = time.time()
            if not force and now - self._last_poll < POLL_MIN_S:
                return self.last
            self._last_poll = now
            return self._poll()
        finally:
            self._poll_lock.release()

    def round(self) -> Dict[str, Any]:
        """The gossip round's part: poll now (a new round is not a user action, so no throttle)."""
        with self._poll_lock:
            self._last_poll = time.time()
            return self._poll()

    def _poll(self) -> Dict[str, Any]:
        m = self.m
        m._ensure()
        me = m._identity
        if me is None:
            return {}
        local = m.chat_local()
        got = {"public": 0, "dm": 0, "hubs_ok": 0, "hubs_failed": 0}
        hubs = self.hubs()[:MAX_POLL_HUBS]
        now = time.time()
        for base, priv in hubs:
            try:
                for _ in range(MAX_PAGES):
                    cursor = float(local.cursors.get(base, 0.0))
                    reply = self._fetch(f"{base}/api/community/chat/messages?since={cursor!r}", allow_private=priv,
                                        headers=chatmod.sign_fetch(me), timeout=15.0)
                    msgs = reply.get("messages") if isinstance(reply, dict) else None
                    nxt = reply.get("next") if isinstance(reply, dict) else None
                    if not isinstance(msgs, list) or isinstance(nxt, bool) or not isinstance(nxt, (int, float)):
                        raise ValueError("bad reply")
                    for msg in msgs[:500]:
                        kind = m.chat_ingest(msg)
                        if kind in ("public", "dm"):
                            got[kind] += 1
                    if nxt > cursor:
                        local.set_cursor(base, float(nxt))
                    if reply.get("more") is not True or not nxt > cursor:
                        break
                got["hubs_ok"] += 1
            except Exception as exc:
                got["hubs_failed"] += 1
                logger.debug("Chat poll %s failed: %s", base, exc)
        if hubs and now - self._last_keys > KEYS_EVERY_S:
            self._refresh_keys(hubs)
        self._publish_key(hubs)
        local.save(force=False)
        self.last = got
        return got

    def _refresh_keys(self, hubs: List[Tuple[str, bool]]) -> None:
        m = self.m
        self._last_keys = time.time()
        local = m.chat_local()
        for base, priv in hubs:
            try:
                reply = self._fetch(f"{base}/api/community/chat/keys", allow_private=priv, timeout=15.0)
                recs = reply.get("keys") if isinstance(reply, dict) else None
                if not isinstance(recs, list):
                    raise ValueError("bad reply")
                for rec in recs[:chatmod.KEYS_CAP]:
                    try:
                        card = m._chat_card(rec.get("id") if isinstance(rec, dict) else None)
                        if card is not None:
                            local.put_key(chatmod.verify_key_record(rec, card.get("pubkey")))
                    except chatmod.ChatError:
                        continue
            except Exception as exc:
                logger.debug("Chat keys from %s failed: %s", base, exc)

    def _publish_key(self, hubs: List[Tuple[str, bool]]) -> None:
        rec = self.m.chat_own_key_record()
        if rec is None:
            return
        now = time.time()
        for base, priv in hubs:
            if now - self._published.get(base, 0.0) < KEY_PUBLISH_EVERY_S:
                continue
            try:
                self._fetch(f"{base}/api/community/chat/post", allow_private=priv, method="POST", payload={"key": rec},
                            timeout=15.0)
                self._published[base] = now
            except Exception as exc:
                logger.debug("Chat key post to %s failed: %s", base, exc)

    # -- writing --------------------------------------------------------------
    def send(self, to: str, text: str) -> Dict[str, Any]:
        """Sign (and for a direct message encrypt) `text` and hand it to up to 3 hubs. Raises ChatRefused."""
        m = self.m
        m._ensure()
        me = m._identity
        if me is None or not m.chat_on():
            raise chatmod.ChatRefused(404, "Chat is off")
        local = m.chat_local()
        try:
            if to == chatmod.PUBLIC:
                msg = chatmod.make_public(me, text)
                plain = msg["body"]
            else:
                if not chatmod.ID_RE.match(to or "") or to == me.id:
                    raise chatmod.ChatError("bad recipient")
                if m._chat_card(to) is None:
                    raise chatmod.ChatError("unknown station")
                rec = local.keys.get(to)
                if rec is None:
                    self._refresh_keys(self.hubs()[:MAX_POLL_HUBS])
                    rec = local.keys.get(to)
                if rec is None:
                    raise chatmod.ChatRefused(409, "This station has not published a chat key yet (is its chat switched on?)")
                plain = chatmod.clean_text(text)
                msg = chatmod.make_dm(me, to, rec["xpub"], plain)
        except chatmod.ChatError as exc:
            raise chatmod.ChatRefused(400, str(exc))
        payload: Dict[str, Any] = {"msg": msg}
        own_key = m.chat_own_key_record()
        delivered = 0
        if m.chat_hub():
            delivered += 1 if m.chat_store().add(msg) else 0   # this station is itself a hub: others pull from it
            if delivered:
                m.chat_store().save(force=False)
        now = time.time()
        for base, priv in self.hubs()[:MAX_SEND_HUBS]:
            body = dict(payload)
            if own_key is not None and now - self._published.get(base, 0.0) >= KEY_PUBLISH_EVERY_S:
                body["key"] = own_key
            try:
                self._fetch(f"{base}/api/community/chat/post", allow_private=priv, method="POST", payload=body,
                            timeout=15.0)
                delivered += 1
                if "key" in body:
                    self._published[base] = now
            except Exception as exc:
                logger.debug("Chat post to %s failed: %s", base, exc)
        if not delivered:
            raise chatmod.ChatRefused(503, "No hub could be reached (a hub is a station with chat on and a public address)")
        local.add(to, msg, plain, incoming=False)
        local.save(force=False)
        return {"ok": True, "mid": msg["mid"], "hubs": delivered}
