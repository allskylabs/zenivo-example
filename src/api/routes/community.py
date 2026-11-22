"""
Community: opt-in sharing of a signed station card between Zenivo stations.
"""

import json
from fastapi import HTTPException, Request, status, Depends
from fastapi.responses import Response

from src.api.schemas import (
    ChatBlockRequest,
    ChatReadRequest,
    ChatSendRequest,
    CommunitySettingsRequest,
)
from src.api.service import AllSkyService
from src.api.routes.common import (router, get_service)


# ----------------------------------------------------------------------
# Community: opt-in sharing of a signed station card between Zenivo stations (docs/COMMUNITY.md)
# ----------------------------------------------------------------------

_COMMUNITY_MAX_BODY = 16 * 1024
_COMMUNITY_CORS = {"Access-Control-Allow-Origin": "*", "Cache-Control": "no-store"}


@router.get("/api/community/status")
def get_community_status(request: Request, service: AllSkyService = Depends(get_service)):
    """Public summary; the per-address health list (`targets`) is only added for the admin."""
    from src.auth.middleware import is_admin
    return service.community.status(admin=is_admin(request))


@router.post("/api/community/check")
def post_community_check(service: AllSkyService = Depends(get_service)):
    """Admin: run a gossip round now."""
    service.community.check_now()
    return {"ok": True}


@router.get("/api/community/settings")
def get_community_settings(service: AllSkyService = Depends(get_service)):
    return service.community.settings()


@router.post("/api/community/settings")
def set_community_settings(req: CommunitySettingsRequest, service: AllSkyService = Depends(get_service)):
    """Save the settings; turning sharing on creates the station key, starts the gossip worker and runs a round."""
    try:
        return service.community.update_settings(req.model_dump(exclude_none=True))
    except RuntimeError as exc:  # `cryptography` missing
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.get("/api/community/card")
def get_community_card(service: AllSkyService = Depends(get_service)):
    card = service.community.own_card()
    if card is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Community sharing is off")
    return card


@router.get("/api/community/peers")
def get_community_peers(service: AllSkyService = Depends(get_service)):
    """Own card plus every verified card we know. Open to any origin (no cookies) so other stations' pages can read it."""
    return Response(content=service.community.peers_json(), media_type="application/json", headers=_COMMUNITY_CORS)


@router.post("/api/community/announce")
async def announce_community_card(request: Request, service: AllSkyService = Depends(get_service)):
    """Another station tells us its card. Accepted only when the signature verifies; its url is never fetched here."""
    from starlette.concurrency import run_in_threadpool
    from src.community.card import CardError
    from src.community.manager import RateLimited
    if not service.community.enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Community sharing is off")
    length = request.headers.get("content-length")
    if length is not None and length.isdigit() and int(length) > _COMMUNITY_MAX_BODY:
        raise HTTPException(status_code=413, detail="Card too large")
    chunks, size = [], 0
    async for chunk in request.stream():  # bounded read: a chunked body cannot skip the size cap
        size += len(chunk)
        if size > _COMMUNITY_MAX_BODY:
            raise HTTPException(status_code=413, detail="Card too large")
        chunks.append(chunk)

    def _no_constant(name):
        raise ValueError(name)

    try:  # NaN / Infinity are not JSON: refuse them here, before any card logic sees them
        card = json.loads(b"".join(chunks).decode("utf-8"), parse_constant=_no_constant)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Body is not JSON")
    from src.auth.policy import client_key
    key = client_key({k.lower(): v for k, v in request.headers.items()}, request.client.host if request.client else None)
    try:  # verification and the registry work are blocking: off the event loop
        await run_in_threadpool(service.community.accept_announce, card, key)
    except RateLimited:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many announces")
    except CardError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Card rejected: {exc}")
    return {"ok": True, "relay": service.community.relay_enabled(),   # senders without a public URL read `relay`
            "chat": service.community.chat_hub()}


_RELAY_MAX_BODY = 256 * 1024


@router.post("/api/community/relay/{station_id}")
async def post_community_relay(station_id: str, request: Request, service: AllSkyService = Depends(get_service)):
    """A station without a public URL pushes its large preview (raw image/jpeg). Refused (404) unless the owner turned
    `relay_previews` on. Checks run cheapest first: rate limit, size, known card without url, signature, interval,
    then the image is re-encoded; see docs/COMMUNITY.md."""
    from starlette.concurrency import run_in_threadpool
    from src.auth.policy import client_key
    from src.community.manager import RateLimited
    from src.community.relay import RelayRefused
    mgr = service.community
    if not mgr.relay_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This station does not relay previews")
    key = client_key({k.lower(): v for k, v in request.headers.items()}, request.client.host if request.client else None)
    try:
        mgr.relay_rate(key)
    except RateLimited:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many uploads")
    length = request.headers.get("content-length")
    if length is not None and length.isdigit() and int(length) > _RELAY_MAX_BODY:
        raise HTTPException(status_code=413, detail="Image too large")
    chunks, size = [], 0
    async for chunk in request.stream():  # bounded read: a chunked body cannot skip the cap
        size += len(chunk)
        if size > _RELAY_MAX_BODY:
            raise HTTPException(status_code=413, detail="Image too large")
        chunks.append(chunk)
    try:
        await run_in_threadpool(mgr.accept_relay, station_id, request.headers.get("x-zenivo-time"),
                                request.headers.get("x-zenivo-sig"), b"".join(chunks))
    except RelayRefused as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)
    return {"ok": True}


@router.get("/api/community/relay/{station_file}")
def get_community_relay(station_file: str, service: AllSkyService = Depends(get_service)):
    """Public: the large preview this station holds for a station without a public URL (<id>.jpg)."""
    data = service.community.relay_jpeg(station_file[:-4]) if station_file.endswith(".jpg") else None
    if data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No preview held for this station")
    return Response(content=data, media_type="image/jpeg", headers={
        "X-Content-Type-Options": "nosniff", "Cache-Control": "public, max-age=300",
        "Cross-Origin-Resource-Policy": "cross-origin", "Access-Control-Allow-Origin": "*"})


@router.get("/api/community/thumb.jpg")
def get_community_thumb(service: AllSkyService = Depends(get_service)):
    """Latest frame, at most 320 px wide (JPEG q70), cached for a minute."""
    try:
        data = service.community.thumb_jpeg()
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Thumbnail failed: {exc}")
    if data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Preview sharing is off")
    return Response(content=data, media_type="image/jpeg", headers={**_COMMUNITY_CORS, "Cache-Control": "public, max-age=60"})


@router.get("/api/community/preview.jpg")
def get_community_preview(service: AllSkyService = Depends(get_service)):
    """Latest frame, at most 1280 px wide (JPEG q80), cached for a minute; only when the owner shares the large preview."""
    try:
        data = service.community.preview_jpeg()
    except Exception:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Preview failed")
    if data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="The large preview is not shared")
    return Response(content=data, media_type="image/jpeg", headers={**_COMMUNITY_CORS, "Cache-Control": "public, max-age=60"})


# ----------------------------------------------------------------------
# Chat (docs/COMMUNITY.md "Chat"): hub endpoints for other stations, owner endpoints for the local UI
# ----------------------------------------------------------------------

_CHAT_MAX_BODY = 16 * 1024


def _chat_hub_or_404(mgr):
    if not mgr.chat_hub():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This station is not a chat hub")


def _chat_on_or_404(mgr):
    if not mgr.chat_on():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat is off")


@router.post("/api/community/chat/post")
async def post_community_chat(request: Request, service: AllSkyService = Depends(get_service)):
    """A station hands a signed message (and/or its chat key record) to this hub. Cheapest checks first: chat on, client
    rate limit, body size, JSON, known card, signature, per-station limits."""
    from starlette.concurrency import run_in_threadpool
    from src.auth.policy import client_key
    from src.community.chat import ChatRefused
    from src.community.manager import RateLimited
    mgr = service.community
    _chat_hub_or_404(mgr)
    key = client_key({k.lower(): v for k, v in request.headers.items()}, request.client.host if request.client else None)
    try:
        mgr.chat_rate(key, "ip")
    except RateLimited:
        mgr.chat_count("refused_rate")
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many requests")
    length = request.headers.get("content-length")
    if length is not None and length.isdigit() and int(length) > _CHAT_MAX_BODY:
        raise HTTPException(status_code=413, detail="Message too large")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > _CHAT_MAX_BODY:
            raise HTTPException(status_code=413, detail="Message too large")
        chunks.append(chunk)

    def _no_constant(name):
        raise ValueError(name)

    try:
        payload = json.loads(b"".join(chunks).decode("utf-8"), parse_constant=_no_constant)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Body is not JSON")
    try:
        return await run_in_threadpool(mgr.accept_chat_post, payload)
    except ChatRefused as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)


@router.get("/api/community/chat/messages")
def get_community_chat_messages(request: Request, since: float = 0.0, hub: int = 0,
                                service: AllSkyService = Depends(get_service)):
    """Public room; plus the direct messages of the station that signed X-Zenivo-Id/-Time/-Sig. `hub=1`: another hub
    pulling every direct message (ciphertext) with hop counts; needs a hub-pull signature of a station with an address."""
    import math
    from src.community.chat import ChatRefused
    mgr = service.community
    _chat_hub_or_404(mgr)
    if not math.isfinite(since) or since < 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Bad cursor")
    h = request.headers
    try:
        out = mgr.chat_fetch(since, h.get("x-zenivo-id"), h.get("x-zenivo-time"), h.get("x-zenivo-sig"), hub=bool(hub))
    except ChatRefused as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)
    return Response(content=json.dumps(out, separators=(",", ":")), media_type="application/json",
                    headers=_COMMUNITY_CORS)


@router.get("/api/community/chat/keys")
def get_community_chat_keys(service: AllSkyService = Depends(get_service)):
    from src.community.chat import ChatRefused
    mgr = service.community
    _chat_hub_or_404(mgr)
    try:
        return Response(content=json.dumps(mgr.chat_keys_payload(), separators=(",", ":")), media_type="application/json",
                        headers=_COMMUNITY_CORS)
    except ChatRefused as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)


@router.get("/api/community/chat/stats")
def get_community_chat_stats(service: AllSkyService = Depends(get_service)):
    """Owner: chat counters (no message content), see docs/COMMUNITY.md "Chat"."""
    return service.community.chat_stats_payload()


@router.get("/api/community/chat/local")
def get_community_chat_local(peer: str = "", poll: int = 0, service: AllSkyService = Depends(get_service)):
    """Owner: conversations (with the messages of `peer`), stations one can write to. `poll=1` asks the hubs first
    (throttled to once per 30 s)."""
    import re
    from src.community import chat as chatmod
    mgr = service.community
    _chat_on_or_404(mgr)
    if peer and peer != chatmod.PUBLIC and not re.match(r"^[0-9a-f]{32}$", peer):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Bad station id")
    if poll:
        mgr.chat_net().poll()
    local = mgr.chat_local()
    names = {c.get("id"): c.get("name") or "" for c in mgr.registry_cards()}
    convs = [dict(c, name=names.get(c["peer"], "")) for c in local.conversations()]
    convs.sort(key=lambda c: (c["peer"] != chatmod.PUBLIC, -c["last_t"]))
    me = mgr.status().get("id")
    stations = sorted(({"id": i, "name": n or i[:8], "dm": i in local.keys, "blocked": local.is_blocked(i)}
                       for i, n in names.items() if i != me), key=lambda s: s["name"].lower())
    return {"enabled": True, "id": me, "hub": mgr.chat_hub(), "conversations": convs, "stations": stations,
            "blocked": sorted(local.blocked), "peer": peer,
            "messages": local.messages(peer) if peer else []}


@router.post("/api/community/chat/send")
def post_community_chat_send(req: ChatSendRequest, service: AllSkyService = Depends(get_service)):
    from src.community.chat import ChatRefused
    mgr = service.community
    _chat_on_or_404(mgr)
    try:
        return mgr.chat_net().send(req.to, req.body)
    except ChatRefused as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)


@router.post("/api/community/chat/block")
def post_community_chat_block(req: ChatBlockRequest, service: AllSkyService = Depends(get_service)):
    mgr = service.community
    _chat_on_or_404(mgr)
    mgr.chat_local().set_blocked(req.id, req.blocked)
    mgr.chat_local().save()
    return {"ok": True}


@router.post("/api/community/chat/read")
def post_community_chat_read(req: ChatReadRequest, service: AllSkyService = Depends(get_service)):
    mgr = service.community
    _chat_on_or_404(mgr)
    mgr.chat_local().mark_read(req.peer)
    mgr.chat_local().save(force=False)
    return {"ok": True}
