"""
Live snapshot, histogram and live video stream (MJPEG, SSE, WebSocket) endpoints.
"""

from typing import Optional
from fastapi import HTTPException, Query, Request, WebSocket, Depends
from fastapi.responses import Response, StreamingResponse

from src.api.service import AllSkyService
from src.api.stream import (
    mjpeg_frame_generator, handle_live_websocket, sse_event_generator, guarded_stream, stream_limiter,
    stream_client_key, GUEST_MJPEG_MAX_FPS, GUEST_MJPEG_MAX_WIDTH, GUEST_STREAM_MAX_S,
)
from src.api.routes.common import (_NO_STORE, router, get_service, _view_for_request)


# ----------------------------------------------------------------------
# Live Snapshot & Histogram Endpoints (AC3.5, AC3.6)
# ----------------------------------------------------------------------

@router.get("/api/live/snapshot.webp")
@router.get("/api/snapshot.webp")
def get_snapshot_webp(service: AllSkyService = Depends(get_service)):
    """
    Return latest calibrated 2592x1944 WebP frame (captured as RAW10, subtracted in RAW10, then saved as WebP).
    """
    try:
        webp_bytes = service.get_snapshot_webp()
        return Response(content=webp_bytes, media_type="image/webp", headers=_NO_STORE)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"WebP snapshot generation failed: {e}")


@router.get("/api/live/frame.jpg")
def get_live_frame_jpeg(request: Request, service: AllSkyService = Depends(get_service)):
    """
    The live viewer picture over plain HTTP, for browsers whose network drops WebSockets: the same size and quality as
    the WebSocket stream (Settings > Performance), encoded once per camera frame and shared by all viewers.
    ETag = capture time, so polling an unchanged frame costs a 304.
    """
    from src.api.stream import _encode_stream_jpeg
    try:
        service.capture_frame()
        frame, frame_ts = service.get_display_frame()
        if frame is None:
            raise RuntimeError("no frame yet")
        perf = service.performance
        key = (frame_ts, int(perf["stream_max_width"]), int(perf["stream_jpeg_quality"]))
        etag = '"%s-%s-%s"' % key
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={**_NO_STORE, "ETag": etag})
        cached = getattr(service, "_http_frame_cache", None)
        if cached is None or cached[0] != key:
            jpg = _encode_stream_jpeg(frame, key[1], key[2])
            if not jpg:
                raise RuntimeError("JPEG compression failed")
            service._http_frame_cache = cached = (key, jpg)
        return Response(content=cached[1], media_type="image/jpeg",
                        headers={**_NO_STORE, "ETag": etag, "X-Capture-Ts": str(frame_ts)})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Live frame failed: {e}")


@router.get("/api/live/snapshot.jpg")
@router.get("/api/snapshot.jpg")
def get_snapshot_jpeg(service: AllSkyService = Depends(get_service)):
    """
    Return latest calibrated 2592x1944 JPEG frame with valid magic bytes (0xFFD8...0xFFD9).
    """
    try:
        jpeg_bytes = service.get_snapshot_jpeg()
        return Response(content=jpeg_bytes, media_type="image/jpeg", headers=_NO_STORE)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Snapshot generation failed: {e}")


@router.get("/api/planets")
def get_planets():
    """Naked-eye planets (J2000 RA/Dec, visual magnitude) for the live sky overlay."""
    from datetime import datetime, timezone
    from src.calibration.bright_star_solver import planet_positions
    return planet_positions(datetime.now(timezone.utc))


@router.get("/api/stream/histogram")
def get_live_histogram(service: AllSkyService = Depends(get_service)):
    """
    Compute and return vectorized 256-bin RGB and Luma histogram in < 10ms with statistics.
    """
    frame, _ = service.capture_frame()
    from src.api.histogram import compute_fast_histogram
    return compute_fast_histogram(frame)


# ----------------------------------------------------------------------
# Live Video Stream (MJPEG & WebSocket)
# ----------------------------------------------------------------------

@router.get("/api/stream/mjpeg")
@router.get("/api/live/mjpeg")
def get_mjpeg_stream(
    request: Request,
    fps: float = Query(default=5.0, ge=0.5, le=30.0),
    downscale: int = Query(default=960, ge=320, le=2592),
    limit: Optional[int] = Query(default=None, ge=1),
    service: AllSkyService = Depends(get_service),
):
    """
    Multipart MJPEG video stream for live browser video preview.
    """
    from src.auth.middleware import is_admin
    key, max_s = None, None
    if not is_admin(request):
        key = stream_client_key(request)
        if not stream_limiter.acquire(key):
            raise HTTPException(status_code=429, detail="Too many live streams, try again shortly",
                                headers={"Retry-After": "10"})
        fps = min(fps, GUEST_MJPEG_MAX_FPS)
        downscale = min(downscale, GUEST_MJPEG_MAX_WIDTH)
        max_s = GUEST_STREAM_MAX_S
        limit = min(limit, int(fps * max_s)) if limit else None
    gen = mjpeg_frame_generator(service, fps=fps, downscale_width=downscale, max_frames=limit, max_seconds=max_s)
    return StreamingResponse(
        guarded_stream(gen, key),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@router.get("/api/stream/sse")
@router.get("/api/sse")
def get_sse_stream(
    request: Request,
    interval: float = Query(default=1.0, ge=0.25, le=10.0),
    service: AllSkyService = Depends(get_service),
):
    """
    Server-Sent Events (SSE) push stream for real-time telemetry and status updates.
    """
    from src.auth.middleware import is_admin
    key, max_s = None, None
    if not is_admin(request):
        key = stream_client_key(request)
        if not stream_limiter.acquire(key):
            raise HTTPException(status_code=429, detail="Too many live streams, try again shortly",
                                headers={"Retry-After": "10"})
        max_s = GUEST_STREAM_MAX_S
    return StreamingResponse(
        guarded_stream(sse_event_generator(service, interval=interval, view=_view_for_request(request, service),
                                           max_seconds=max_s), key),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.websocket("/ws/live")
async def websocket_live_stream(
    websocket: WebSocket,
    service: AllSkyService = Depends(get_service),
):
    """
    Bidirectional WebSocket feed for real-time binary JPEG frames, telemetry streaming, and commands.
    """
    await handle_live_websocket(websocket, service)
