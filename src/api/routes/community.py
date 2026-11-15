"""
Community: opt-in sharing of a signed station card between Zenivo stations.
"""

import json
from fastapi import HTTPException, Request, status, Depends
from fastapi.responses import Response

from src.api.schemas import (
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
    return {"ok": True, "relay": service.community.relay_enabled()}   # senders without a public URL read `relay`


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
