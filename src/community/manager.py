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
from src.community.card import CardError, MAX_CARD_BYTES, PRECISIONS
from src.community.gossip import GossipWorker
from src.community.registry import Registry
from src.version import __version__

logger = logging.getLogger("imx335.community")

CARD_MAX_AGE_S = 6 * 3600
THUMB_RETRY_S = 300
THUMB_CACHE_S = 60
ANNOUNCES_PER_MIN = 30
MAX_SEEDS = 10

DEFAULTS: Dict[str, Any] = {
    "enabled": False, "name": "", "public_url": "", "public_precision_deg": 0.1, "share_thumbnail": True, "seeds": [],
    # False until the owner saves a seed list: until then the built-in seeds are part of the list (and removable)
    "seeds_set": False,
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
        self._hits: Dict[str, Deque[float]] = {}
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
            self._settings = cur
            self._card = None
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
        key = (name, st.get("lat"), st.get("lon"), s["public_precision_deg"], url, st.get("camera"), want_thumb)
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
                url=url, camera=(st.get("camera") or None), software=f"Zenivo {__version__}", thumb=thumb)
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
        return {"cards": [own] + self.registry_cards()}

    def merge_card(self, card: Dict[str, Any]) -> bool:
        """Merge an already verified card."""
        return self._registry.merge(card) if self._registry is not None else False

    def save_registry(self) -> None:
        if self._registry is not None:
            self._registry.save()

    def accept_announce(self, raw_card: Any, client: str) -> None:
        """Rate-limit, verify and store a card announced by another station. Raises RateLimited or CardError."""
        now = time.time()
        with self._lock:
            hits = self._hits.setdefault(client, deque())
            while hits and hits[0] < now - 60:
                hits.popleft()
            if len(hits) >= ANNOUNCES_PER_MIN:
                raise RateLimited()
            hits.append(now)
            if len(self._hits) > 1000:  # bound memory against many source addresses
                for k in [k for k, v in self._hits.items() if not v or v[-1] < now - 60]:
                    del self._hits[k]
        self._ensure()
        self.merge_card(cardmod.verify(raw_card))
        self._registry.save()

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

    # -- status ------------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        available, reason = idmod.availability()
        s = self.settings()
        if s["enabled"] and available:
            self._ensure()
        return {
            "available": available,
            "reason": reason,
            "enabled": bool(s["enabled"]) and available,
            "id": self._identity.id if self._identity is not None else None,
            "known": self._registry.count() if self._registry is not None else 0,
            "contacts": len(self.contacts()),
            "last_round": dict(self.worker.last_round),
            "default_seeds": list(DEFAULT_SEEDS),  # for the "restore built-in seeds" button in Settings
        }
