"""Community manager: settings, identity, own card, known stations and the gossip worker, behind one object.

Nothing is created on disk and no thread runs while the feature is disabled."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional

from src.community import DEFAULT_SEEDS
from src.community import card as cardmod
from src.community import identity as idmod
from src.community.card import CardError, PRECISIONS, seed_origin, to_origin
from src.community.gossip import GossipWorker
from src.community.registry import Registry
from src.community import relay as relaymod
from src.community.relay import RelayRefused, RelayStore
from src.version import __version__

logger = logging.getLogger("imx335.community")

CARD_MAX_AGE_S = 6 * 3600
THUMB_RETRY_S = 300
THUMB_CACHE_S = 60
ANNOUNCES_PER_MIN = 30
# Behind a local proxy/tunnel every client can share one key (loopback) or none (unknown): that bucket gets a wider net
ANNOUNCES_PER_MIN_SHARED = 300
SHARED_KEYS = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost", "unknown", ""})
PEERS_CACHE_S = 5
RELAY_PER_CLIENT_H = 6       # relay uploads per client address per hour (counted before any check)
RELAY_GLOBAL_H = 120
RELAY_ATTEMPT_MIN_S = 30 * 60    # sender: at most one upload per target per 30 min
RELAY_MAX_FAILS = 3
RELAY_MAX_AGE_S = 24 * 3600      # sender: a relay without a success for 24 h is dropped
RELAY_MAX_TARGETS = 3
MAX_SEEDS = 10

DEFAULTS: Dict[str, Any] = {
    "enabled": False, "name": "", "public_url": "", "public_precision_deg": 0.1, "share_thumbnail": True, "seeds": [],
    # Large preview for map visitors, and whether the map may link to this station's own page
    "share_preview": True, "allow_open": True,
    # False until the owner saves a seed list: until then the built-in seeds are part of the list (and removable)
    "seeds_set": False,
    # Receiver: store and serve large previews pushed by stations that have no public URL (on by default; the owner can turn it off)
    "relay_previews": True,
}


class RateLimited(Exception):
    """Too many announces from one client."""


class CommunityManager:
    def __init__(self, data_dir: str, station_fn: Callable[[], Dict[str, Any]],
                 peek_jpeg_fn: Callable[[], Optional[bytes]], snapshot_jpeg_fn: Callable[[], bytes]):
        """station_fn() -> {name, lat, lon, camera}; peek_jpeg_fn() = latest frame without capturing;
        snapshot_jpeg_fn() = latest frame, capturing if needed (used by the thumbnail endpoint)."""
        self.data_dir = data_dir
        self._station_fn = station_fn
        self._peek_jpeg = peek_jpeg_fn
        self._snapshot_jpeg = snapshot_jpeg_fn
        self._lock = threading.RLock()
        self._settings: Optional[Dict[str, Any]] = None
        self._identity: Optional[idmod.Identity] = None
        self._registry: Optional[Registry] = None
        self._card: Optional[Dict[str, Any]] = None
        self._card_key: Any = None
        self._thumb_cache: Optional[tuple] = None
        self._preview_cache: Optional[tuple] = None
        self._peers_cache: Optional[tuple] = None  # (time, registry version, own card signature, JSON bytes)
        self._hits: Dict[str, Deque[float]] = {}
        self._inbound: Deque[float] = deque(maxlen=500)  # times other stations announced themselves to us
        self._relay: Optional[RelayStore] = None
        self._relay_hits: Dict[str, Deque[float]] = {}
        self._relay_all: Deque[float] = deque()
        self._relay_out: Optional[Dict[str, Dict[str, Any]]] = None   # sender: origin -> {ok_t, fail, try_t}
        self._relay_payload: Optional[tuple] = None                   # (time, bytes, sha)
        self.worker = GossipWorker(self)

    # -- paths / settings --------------------------------------------------
    def _path(self, name: str) -> str:
        return os.path.join(self.data_dir, name)

    def settings(self) -> Dict[str, Any]:
        with self._lock:
            if self._settings is None:
                data: Dict[str, Any] = {}
                try:
                    with open(self._path("community.json"), "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                    data = loaded if isinstance(loaded, dict) else {}
                except (OSError, ValueError):
                    pass
                if "public_precision_deg" not in data and "precision_deg" in data:
                    data["public_precision_deg"] = data["precision_deg"]  # older key
                merged = dict(DEFAULTS)
                merged.update({k: v for k, v in data.items() if k in DEFAULTS})
                if not merged["seeds_set"]:
                    seeds = [u for u in merged["seeds"] if isinstance(u, str)]
                    merged["seeds"] = (list(DEFAULT_SEEDS) + [u for u in seeds if u not in DEFAULT_SEEDS])[:MAX_SEEDS]
                self._settings = merged
            return dict(self._settings)

    def update_settings(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        """Validated partial update (see CommunitySettingsRequest); persists and starts/stops the worker."""
        with self._lock:
            cur = self.settings()
            patch = dict(patch)
            if patch.get("precision_deg") is not None:  # older name of public_precision_deg
                patch.setdefault("public_precision_deg", patch["precision_deg"])
            patch.pop("seeds_set", None)
            cur.update({k: v for k, v in patch.items() if k in DEFAULTS and v is not None})
            try:  # addresses are origins only: a path, query or trailing slash the owner typed is dropped
                if cur["public_url"]:
                    cur["public_url"] = to_origin(cur["public_url"])
                cur["seeds"] = list(dict.fromkeys(seed_origin(u) for u in cur["seeds"] if isinstance(u, str) and u.strip()))
            except CardError as exc:
                raise ValueError(str(exc))
            if patch.get("seeds") is not None:
                cur["seeds_set"] = True  # from now on the list is exactly what the owner saved, even when empty
            if cur["public_precision_deg"] not in PRECISIONS:
                raise ValueError("public_precision_deg must be 0, 0.01, 0.1 or 1.0")
            if cur["enabled"]:
                ok, reason = idmod.availability()
                if not ok:
                    raise RuntimeError(reason)
            os.makedirs(self.data_dir, exist_ok=True)
            tmp = self._path("community.json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cur, f, indent=2)
            os.replace(tmp, self._path("community.json"))
            was_relay = bool(self.settings().get("relay_previews"))
            self._settings = cur
            self._card = None
            if not (cur["enabled"] and cur["relay_previews"]):
                if was_relay or os.path.isdir(self._path("community_relay")):
                    self.relay_store().clear()    # turning the relay off (or Community off) deletes what it holds
        if cur["enabled"]:
            self.start()
            self.worker.trigger()
        else:
            self.stop()
        return cur

    @property
    def enabled(self) -> bool:
        return bool(self.settings()["enabled"]) and idmod.availability()[0]

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        """Create the key and registry (first time) and start the gossip worker. No-op while disabled."""
        if not self.enabled:
            return
        self._ensure()
        self.worker.start()

    def stop(self) -> None:
        self.worker.stop()
        with self._lock:
            if self._registry is not None:
                self._registry.save()

    def identity(self) -> Optional[idmod.Identity]:
        """This station's signing identity (created on first use), or None when `cryptography` is missing. Works
        while community sharing is off: it only loads or creates the key file."""
        with self._lock:
            if self._identity is None:
                if not idmod.availability()[0]:
                    return None
                self._identity = idmod.Identity(self._path("community_key.pem"))
            return self._identity

    def _ensure(self) -> None:
        with self._lock:
            if self._identity is None:
                self._identity = idmod.Identity(self._path("community_key.pem"))
            if self._registry is None:
                self._registry = Registry(self._path("community_peers.json"), self._identity.id)
                self._registry.load()

    # -- cards -------------------------------------------------------------
    def own_card(self) -> Optional[Dict[str, Any]]:
        """Our signed card, rebuilt when its content changed, it is older than 6 h or a preview image became available."""
        if not self.enabled:
            return None
        self._ensure()
        s = self.settings()
        st = self._station_fn()
        name = (s["name"] or st.get("name") or "Zenivo station").strip()[:cardmod.MAX_NAME] or "Zenivo station"
        url = s["public_url"] or None
        want_thumb = bool(s["share_thumbnail"]) and not url
        preview = bool(url) and bool(s["share_thumbnail"]) and bool(s["share_preview"])
        relays, rsha, rt = self._relay_card_fields(s, url)
        key = (name, st.get("lat"), st.get("lon"), s["public_precision_deg"], url, st.get("camera"), want_thumb,
               bool(s["allow_open"]), preview, tuple(relays), rsha, rt)
        now = time.time()
        with self._lock:
            c = self._card
            if (c is not None and key == self._card_key and now - c["updated"] < CARD_MAX_AGE_S
                    and not (want_thumb and "thumb" not in c and now - c["updated"] > THUMB_RETRY_S)):
                return c
            thumb = None
            if want_thumb:
                jpeg = self._peek_jpeg()
                if jpeg:
                    from src.community.thumb import make_embedded
                    thumb = make_embedded(jpeg)
            self._card = cardmod.build_card(
                self._identity, name=name, lat=float(st["lat"]), lon=float(st["lon"]), precision_deg=s["public_precision_deg"],
                url=url, camera=(st.get("camera") or None), software=f"Zenivo {__version__}", thumb=thumb,
                allow_open=bool(s["allow_open"]), preview=preview, relays=relays, preview_sha=rsha, preview_t=rt)
            self._card_key = key
            return self._card

    def registry_cards(self) -> List[Dict[str, Any]]:
        return self._registry.cards() if self._registry is not None else []

    def contacts(self) -> List[str]:
        return self._registry.contacts() if self._registry is not None else []

    def note_contact(self, url: str) -> None:
        if self._registry is not None:
            self._registry.note_contact(url)

    def peers_payload(self) -> Dict[str, Any]:
        own = self.own_card()
        if own is None:
            return {"cards": []}
        out: Dict[str, Any] = {"cards": [own] + self.registry_cards()}
        # Previews this station holds for other stations: unsigned, beside the cards (a card field would make stations
        # that predate relaying reject the card as having an unknown field)
        held = self.relay_held()
        if held:
            out["relayed"] = held
        return out

    def peers_json(self) -> bytes:
        """The /peers body, serialised once and reused for 5 s (until a card is merged or our own card changes)."""
        own = self.own_card()
        version = self._registry.version if self._registry is not None else 0
        sig = own.get("sig") if own else None
        version = (version, self._relay.version if self._relay is not None else 0)
        now = time.monotonic()
        with self._lock:
            c = self._peers_cache
            if c is not None and now - c[0] < PEERS_CACHE_S and c[1] == version and c[2] == sig:
                return c[3]
        body = json.dumps(self.peers_payload(), separators=(",", ":"), allow_nan=False).encode("utf-8")
        with self._lock:
            self._peers_cache = (now, version, sig, body)
        return body

    def merge_card(self, card: Dict[str, Any]) -> bool:
        """Merge an already verified card."""
        return self._registry.merge(card) if self._registry is not None else False

    def save_registry(self) -> None:
        if self._registry is not None:
            self._registry.save()

    def accept_announce(self, raw_card: Any, client: str) -> None:
        """Rate-limit, verify and store a card announced by another station. Raises RateLimited or CardError.
        `client` is the throttling key from src.auth.policy.client_key (the real client behind a local proxy).
        Attempts count before verification (it costs CPU); the registry file is written at most every 30 s."""
        now = time.time()
        limit = ANNOUNCES_PER_MIN_SHARED if client in SHARED_KEYS else ANNOUNCES_PER_MIN
        with self._lock:
            hits = self._hits.setdefault(client, deque())
            while hits and hits[0] < now - 60:
                hits.popleft()
            if len(hits) >= limit:
                raise RateLimited()
            hits.append(now)
            if len(self._hits) > 1000:  # bound memory against many source addresses
                for k in [k for k, v in self._hits.items() if not v or v[-1] < now - 60]:
                    del self._hits[k]
        self._ensure()
        card = cardmod.verify(raw_card)
        self.merge_card(card)
        if self._identity is None or card.get("id") != self._identity.id:
            self._inbound.append(now)  # proof that another station can reach this one
        self._registry.save(force=False)  # cheap: a no-op unless something changed and 30 s have passed

    # -- relay: receiver ---------------------------------------------------
    def relay_store(self) -> RelayStore:
        folder = self._path("community_relay")
        with self._lock:
            if self._relay is None or self._relay.folder != folder:
                self._relay = RelayStore(folder)
            return self._relay

    def relay_enabled(self) -> bool:
        return bool(self.settings()["relay_previews"]) and self.enabled

    def relay_rate(self, client: str) -> None:
        """Count one upload attempt (before anything else is checked). Raises RateLimited."""
        now = time.time()
        with self._lock:
            hits = self._relay_hits.setdefault(client, deque())
            while hits and hits[0] < now - 3600:
                hits.popleft()
            while self._relay_all and self._relay_all[0] < now - 3600:
                self._relay_all.popleft()
            if len(hits) >= RELAY_PER_CLIENT_H or len(self._relay_all) >= RELAY_GLOBAL_H:
                raise RateLimited()
            hits.append(now)
            self._relay_all.append(now)
            if len(self._relay_hits) > 1000:
                for k in [k for k, v in self._relay_hits.items() if not v or v[-1] < now - 3600]:
                    del self._relay_hits[k]

    def _relay_card_ok(self, card: Optional[Dict[str, Any]]) -> bool:
        return bool(card) and not card.get("url") and card.get("id") != (self._identity.id if self._identity else None)

    def accept_relay(self, station_id: str, t_header: Optional[str], sig_header: Optional[str], body: bytes,
                     now: Optional[float] = None) -> None:
        """Verify and store a preview pushed by a station without a public URL. Raises RelayRefused. Blocking (image
        work): call it in a threadpool. The caller has rate-limited and size-capped already."""
        now = time.time() if now is None else now
        if not self.relay_enabled():
            raise RelayRefused(404, "This station does not relay previews")
        self._ensure()
        card = self._registry.get(station_id) if relaymod.ID_RE.match(station_id or "") else None
        if card is None or not self._relay_card_ok(card):
            raise RelayRefused(403, "Unknown station, or a station that has a public address")
        if not relaymod.verify_upload(card.get("pubkey"), station_id, t_header, sig_header, body, now):
            raise RelayRefused(403, "Bad signature")
        store = self.relay_store()
        last = store.updated(station_id)
        if last is not None and now - last < relaymod.MIN_INTERVAL_S:
            raise RelayRefused(429, "Too soon after the last upload")
        data = relaymod.reencode(body)
        store.put(station_id, data, now)

    def relay_jpeg(self, station_id: str) -> Optional[bytes]:
        if not self.relay_enabled():
            return None
        return self.relay_store().get(station_id)

    def relay_held(self) -> Dict[str, int]:
        """{station id: time} of previews held for stations whose card (no url) we still know."""
        if not self.relay_enabled() or self._registry is None:
            return {}
        return {i: t for i, t in self.relay_store().held().items() if self._relay_card_ok(self._registry.get(i))}

    def relay_sweep(self) -> int:
        """Drop previews that are 24 h old or whose station vanished / got a public URL / we turned relaying off."""
        folder = self._path("community_relay")
        if not os.path.isdir(folder):
            return 0
        if not self.relay_enabled() or self._registry is None:
            if not self.relay_enabled():
                self.relay_store().clear()
            return 0
        return self.relay_store().sweep(lambda i: self._relay_card_ok(self._registry.get(i)))

    # -- relay: sender -----------------------------------------------------
    def relay_wanted(self) -> bool:
        """This station should park its large preview on relays: sharing on, preview shared, and no public URL."""
        s = self.settings()
        return bool(self.enabled and s["share_thumbnail"] and s["share_preview"] and not s["public_url"])

    def _relay_state(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            if self._relay_out is None:
                data: Any = {}
                try:
                    with open(self._path("community_relays.json"), "r", encoding="utf-8") as f:
                        data = json.load(f)
                except (OSError, ValueError):
                    pass
                out: Dict[str, Dict[str, Any]] = {}
                for o, v in (data.items() if isinstance(data, dict) else []):
                    if isinstance(v, dict) and isinstance(v.get("ok_t"), (int, float)):
                        out[str(o)] = {"ok_t": int(v["ok_t"]), "fail": int(v.get("fail") or 0),
                                       "try_t": int(v.get("try_t") or 0), "sha": str(v.get("sha") or "")}
                self._relay_out = out
            return self._relay_out

    def _relay_save(self) -> None:
        try:
            os.makedirs(self.data_dir, exist_ok=True)
            tmp = self._path("community_relays.json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._relay_state(), f)
            os.replace(tmp, self._path("community_relays.json"))
        except OSError as exc:
            logger.warning("Could not save community relays: %s", exc)

    def relay_origins(self, now: Optional[float] = None) -> List[str]:
        """Relays that hold our preview now: up to 3, newest success first, none with 3 failures or 24 h of silence."""
        now = time.time() if now is None else now
        st = self._relay_state()
        live = [(o, v) for o, v in st.items() if v["fail"] < RELAY_MAX_FAILS and now - v["ok_t"] < RELAY_MAX_AGE_S]
        live.sort(key=lambda kv: kv[1]["ok_t"], reverse=True)
        return [o for o, _ in live[:RELAY_MAX_TARGETS]]

    def _relay_card_fields(self, s: Dict[str, Any], url: Optional[str]) -> tuple:
        if url or not (s["share_thumbnail"] and s["share_preview"]):
            return [], None, None
        origins = self.relay_origins()
        if not origins:
            return [], None, None
        newest = max(self._relay_state()[o]["ok_t"] for o in origins)
        sha = next((self._relay_state()[o]["sha"] for o in origins if self._relay_state()[o]["ok_t"] == newest), "")
        return (origins, sha, newest) if sha else ([], None, None)

    def relay_upload_payload(self) -> Optional[tuple]:
        """(jpeg bytes of at most 200 KB, sha256 hex) of the current preview, cached for a minute."""
        now = time.time()
        with self._lock:
            c = self._relay_payload
            if c is not None and now - c[0] < THUMB_CACHE_S:
                return c[1], c[2]
        jpeg = self.preview_jpeg()
        data = relaymod.fit_for_upload(jpeg) if jpeg else None
        if not data:
            return None
        import hashlib
        sha = hashlib.sha256(data).hexdigest()
        with self._lock:
            self._relay_payload = (now, data, sha)
        return data, sha

    def relay_headers(self, data: bytes) -> Dict[str, str]:
        self._ensure()
        return relaymod.sign_upload(self._identity, data)

    def relay_try_due(self, origin: str, now: Optional[float] = None) -> bool:
        now = time.time() if now is None else now
        v = self._relay_state().get(origin)
        return v is None or now - v.get("try_t", 0) >= RELAY_ATTEMPT_MIN_S

    def relay_known(self, origin: str) -> bool:
        v = self._relay_state().get(origin)
        return bool(v and v["fail"] < RELAY_MAX_FAILS)

    def relay_result(self, origin: str, ok: bool, sha: str = "", now: Optional[float] = None) -> None:
        """Record an upload outcome. A failure only counts against a relay that accepted before."""
        now = int(time.time() if now is None else now)
        st = self._relay_state()
        with self._lock:
            v = st.get(origin)
            if ok:
                st[origin] = {"ok_t": now, "fail": 0, "try_t": now, "sha": sha}
            elif v is not None:
                v["fail"] += 1
                v["try_t"] = now
                if v["fail"] >= RELAY_MAX_FAILS:
                    del st[origin]
            for o in [o for o, x in st.items() if now - x["ok_t"] > RELAY_MAX_AGE_S]:
                del st[o]
            self._card = None   # the card lists the relays: sign it again
        self._relay_save()

    # -- thumbnail ---------------------------------------------------------
    def thumb_jpeg(self) -> Optional[bytes]:
        """Latest frame, at most 320 px wide, cached for a minute. None when sharing it is off."""
        s = self.settings()
        if not self.enabled or not s["share_thumbnail"]:
            return None
        now = time.time()
        with self._lock:
            if self._thumb_cache and now - self._thumb_cache[0] < THUMB_CACHE_S:
                return self._thumb_cache[1]
        from src.community.thumb import make_thumb
        data = make_thumb(self._snapshot_jpeg())
        if data is not None:
            with self._lock:
                self._thumb_cache = (now, data)
        return data

    def preview_jpeg(self) -> Optional[bytes]:
        """Latest frame, at most 1280 px wide, cached for a minute. None unless the owner shares the large preview."""
        s = self.settings()
        if not self.enabled or not s["share_thumbnail"] or not s["share_preview"]:
            return None
        now = time.time()
        with self._lock:
            if self._preview_cache and now - self._preview_cache[0] < THUMB_CACHE_S:
                return self._preview_cache[1]
        from src.community.thumb import make_preview
        data = make_preview(self._snapshot_jpeg())
        if data is not None:
            with self._lock:
                self._preview_cache = (now, data)
        return data

    # -- status ------------------------------------------------------------
    def network(self, enabled: bool) -> Dict[str, Any]:
        """Is this station part of the network? Outbound = it reaches others; inbound = others reach it."""
        now = time.time()
        inbound_1h = sum(1 for t in self._inbound if t > now - 3600)
        last_in = int(self._inbound[-1]) if self._inbound else None
        r = self.worker.last_round
        if not enabled:
            state = "off"
        elif not r:
            state = "starting"
        elif r.get("others_ok", 0) > 0 and last_in and last_in > now - 7200:
            state = "connected"      # talks to others and others talk to it
        elif r.get("others_ok", 0) > 0:
            state = "outbound_only"  # reaches others; nobody has called in yet (normal without a public URL)
        elif last_in and last_in > now - 7200:
            state = "inbound_only"
        else:
            state = "isolated"
        return {"state": state, "inbound_1h": inbound_1h, "last_inbound": last_in,
                "known": self._registry.count() if self._registry is not None else 0}

    def status(self, admin: bool = False) -> Dict[str, Any]:
        available, reason = idmod.availability()
        s = self.settings()
        if s["enabled"] and available:
            self._ensure()
        enabled = bool(s["enabled"]) and available
        out = {
            "available": available,
            "reason": reason,
            "enabled": enabled,
            "id": self._identity.id if self._identity is not None else None,
            "known": self._registry.count() if self._registry is not None else 0,
            "contacts": len(self.contacts()),
            "last_round": dict(self.worker.last_round),
            "network": self.network(enabled),
            "default_seeds": list(DEFAULT_SEEDS),  # for the "restore built-in seeds" button in Settings
            "relay": bool(enabled and s["relay_previews"]),   # senders read this: this station stores large previews
            "relays": self.relay_origins() if enabled and not s["public_url"] else [],  # stations holding OUR large preview (card `relays`)
        }
        if enabled and s["relay_previews"] and admin:
            out["relay_held"] = len(self.relay_held())
        if admin:  # addresses (possibly LAN seeds the owner typed) and their health are for the owner only
            out["targets"] = [dict(t) for t in self.worker.last_targets]
        return out

    def check_now(self) -> None:
        """Run a gossip round right away (Settings: "Check now")."""
        if self.settings()["enabled"]:
            self.worker.start()
            self.worker.trigger()
