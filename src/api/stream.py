"""
Live Streaming Engine for IMX335 AllSky Camera.
Provides:
1. Downscaled MJPEG Video Stream (/api/stream/mjpeg, /api/live/mjpeg)
2. WebSocket Telemetry & Binary Broadcast (/ws/live)
"""

import asyncio
import struct
import time
import json
from typing import Set, Dict, Any, Optional
import cv2
import numpy as np
from fastapi import WebSocket, WebSocketDisconnect

from src.api.service import AllSkyService
from src.auth.middleware import can_mutate, is_admin
from src.auth.rounding import view_for
from src.api.guest_view import guest_view

# 92-byte Little-Endian Binary Header ("ASB1") + 4096-byte uint32[4][256] Histogram + Raw JPEG
ASB1_HEADER_STRUCT = struct.Struct("<4sBBBBdffffIfffIIIIfffffHHI")
ASC1_CMD_STRUCT = struct.Struct("<4sBBHff")

SKY_STATUS_TO_CODE = {
    "clear": 0,
    "mostly_clear": 1,
    "scattered": 2,
    "broken": 3,
    "overcast": 4,
    "unknown": 5,
    "overexposed": 6,
    "disabled": 7,
}
CAL_STATE_TO_CODE = {
    "idle": 0,
    "running": 1,
    "completed": 2,
    "error": 3,
}


class WebSocketConnectionManager:
    """Manages active browser WebSocket clients for live telemetry and frame distribution."""

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)

    async def broadcast_json(self, message: Dict[str, Any]):
        dead_connections = set()
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                dead_connections.add(connection)
        for dead in dead_connections:
            self.active_connections.discard(dead)

    async def broadcast_bytes(self, data: bytes):
        dead_connections = set()
        for connection in list(self.active_connections):
            try:
                await connection.send_bytes(data)
            except Exception:
                dead_connections.add(connection)
        for dead in dead_connections:
            self.active_connections.discard(dead)


ws_manager = WebSocketConnectionManager()


class StreamLimiter:
    """In-memory cap on concurrent live streams (MJPEG, SSE, WebSocket): a global total and a per-client count."""

    def __init__(self, total: int = 40, per_client: int = 6):
        self.total = total
        self.per_client = per_client
        self._counts: Dict[str, int] = {}
        self._n = 0

    def acquire(self, key: str) -> bool:
        if self._n >= self.total or self._counts.get(key, 0) >= self.per_client:
            return False
        self._counts[key] = self._counts.get(key, 0) + 1
        self._n += 1
        return True

    def release(self, key: str) -> None:
        c = self._counts.get(key, 0)
        if c <= 1:
            self._counts.pop(key, None)
        else:
            self._counts[key] = c - 1
        if c > 0:
            self._n = max(0, self._n - 1)


stream_limiter = StreamLimiter()
GUEST_STREAM_MAX_S = 600.0  # guest streams end after 10 min; the browser reconnects
GUEST_MJPEG_MAX_FPS = 10.0
GUEST_MJPEG_MAX_WIDTH = 1280


def stream_client_key(conn) -> str:
    """Throttling key of a Request/WebSocket (see src.auth.policy.client_key)."""
    from src.auth.policy import client_key
    peer = conn.client.host if getattr(conn, "client", None) else None
    return client_key(conn.headers, peer)


async def guarded_stream(gen, key: Optional[str]):
    """Yield from `gen`; release the limiter slot of `key` (None = not counted) when the stream ends or is dropped."""
    try:
        async for chunk in gen:
            yield chunk
    finally:
        try:
            await gen.aclose()
        except Exception:
            pass
        if key is not None:
            stream_limiter.release(key)


async def mjpeg_frame_generator(
    service: AllSkyService,
    fps: float = 5.0,
    downscale_width: int = 960,
    max_frames: Optional[int] = None,
    max_seconds: Optional[float] = None,
):
    """
    Asynchronous generator yielding multipart JPEG frames for browser video streaming.
    Decimates resolution to save edge network bandwidth.
    """
    interval = 1.0 / max(0.5, fps)
    frames_sent = 0
    started = time.monotonic()
    while True:
        if max_frames is not None and frames_sent >= max_frames:
            break
        if max_seconds is not None and time.monotonic() - started >= max_seconds:
            break
        t_start = time.perf_counter()
        try:
            await asyncio.to_thread(service.capture_frame)
            frame, _ = service.get_display_frame()  # star-boosted; capture info goes out as telemetry
            if frame is not None and frame.size > 0:
                h, w = frame.shape[:2]
                if w > downscale_width:
                    scale = downscale_width / float(w)
                    target_h = int(h * scale)
                    stream_frame = cv2.resize(frame, (downscale_width, target_h), interpolation=cv2.INTER_LINEAR)
                else:
                    stream_frame = frame

                ret, jpeg = cv2.imencode(".jpg", stream_frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
                if ret:
                    jpeg_bytes = jpeg.tobytes()
                    chunk = (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n"
                        b"Content-Length: " + str(len(jpeg_bytes)).encode("ascii") + b"\r\n\r\n"
                        + jpeg_bytes
                        + b"\r\n"
                    )
                    yield chunk
                    frames_sent += 1
        except Exception:
            pass

        elapsed = time.perf_counter() - t_start
        sleep_dur = max(0.01, interval - elapsed)
        await asyncio.sleep(sleep_dur)


def _encode_stream_jpeg(frame: np.ndarray, downscale_width: int = 1280, quality: int = 82) -> Optional[bytes]:
    """Encode a BGR frame to JPEG bytes for real-time Binary WebSocket transmission."""
    if frame is None or frame.size == 0:
        return None
    h, w = frame.shape[:2]
    if downscale_width and w > downscale_width:
        scale = downscale_width / float(w)
        target_h = int(h * scale)
        stream_frame = cv2.resize(frame, (downscale_width, target_h), interpolation=cv2.INTER_LINEAR)
    else:
        stream_frame = frame
    ret, jpeg = cv2.imencode(".jpg", stream_frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return jpeg.tobytes() if ret else None


def pack_asb1_binary_packet(
    telem: Dict[str, Any],
    status: Dict[str, Any],
    jpeg_bytes: Optional[bytes],
    packet_type: int = 1,
    capture_ts: Optional[float] = None,
) -> bytes:
    """
    Pack telemetry, hardware registers, 4x256 uint32 histogram bins, and raw JPEG image
    into a single zero-JSON binary packet (ASB1 format).
    """
    jpeg_payload = jpeg_bytes or b""
    mode_auto = 1 if str(telem.get("mode", status.get("mode", "auto"))).lower() == "auto" else 0
    cam_conn = 1 if status.get("camera_connected", True) else 0
    sim_mode = 1 if status.get("simulator_mode", False) else 0
    online_flag = 1 if str(status.get("status", "online")).lower() == "online" else 0
    flags = mode_auto | (cam_conn << 1) | (sim_mode << 2) | (online_flag << 3)

    sky_code = SKY_STATUS_TO_CODE.get(str(telem.get("sky_status", "clear")).lower(), 0)
    cal = telem.get("calibration") or status.get("calibration") or {}
    cal_code = CAL_STATE_TO_CODE.get(str(cal.get("state", "idle")).lower(), 0)
    ready_cnt = int(cal.get("ready_profiles_count", 0)) & 0xFF
    total_cnt = int(cal.get("total_profiles_count", 6)) & 0xFF
    cal_ready_packed = ready_cnt | (total_cnt << 8)

    hw_regs = (status.get("hardware") or {}).get("registers") or {}
    hist = telem.get("histogram") or {}
    stats = hist.get("stats") or {}

    header_bytes = ASB1_HEADER_STRUCT.pack(
        b"ASB1",
        packet_type & 0xFF,
        flags & 0xFF,
        sky_code & 0xFF,
        cal_code & 0xFF,
        float(capture_ts or time.time()),  # capture time of the JPEG, shown by the web overlay
        float(telem.get("exposure_s", 0.04)),
        float(telem.get("gain_db", 12.0)),
        float(telem.get("hw_frame_delta_ms") or 0.0),
        float(telem.get("fps") or 0.0),
        int(telem.get("star_count", 0)),
        float(telem.get("cloud_cover_pct", 0.0)),
        float("nan" if telem.get("sensor_temp_c") is None else telem["sensor_temp_c"]),  # NaN: no sensor reading
        float(status.get("uptime_s", 0.0)),
        int(hw_regs.get("VMAX", 0)),
        int(hw_regs.get("HMAX", 0)),
        int(hw_regs.get("SHR0", 0)),
        int(hw_regs.get("PGC", 0)),
        float(hist.get("calculation_time_ms", 0.0)),
        float(stats.get("mean", 0.0)),
        float(stats.get("stddev", 0.0)),
        float(stats.get("clipping_low_pct", 0.0)),
        float(stats.get("clipping_high_pct", 0.0)),
        int(stats.get("median", 0)) & 0xFFFF,
        cal_ready_packed,
        len(jpeg_payload),
    )

    r_bins = hist.get("r") or [0] * 256
    g_bins = hist.get("g") or [0] * 256
    b_bins = hist.get("b") or [0] * 256
    y_bins = hist.get("y") or [0] * 256
    hist_arr = np.asarray([r_bins[:256], g_bins[:256], b_bins[:256], y_bins[:256]], dtype="<u4")
    hist_bytes = hist_arr.tobytes()

    return header_bytes + hist_bytes + jpeg_payload


def resolve_uploads_path(output_dir: str, replay_url: str) -> Optional[str]:
    """Map a `/uploads/...` URL to a file under `<output_dir>/uploads`; None if it escapes that folder (.. or symlink)."""
    import os
    rel = replay_url.lstrip("/").split("?")[0]
    root = os.path.realpath(os.path.join(output_dir, "uploads"))
    cand = os.path.realpath(os.path.join(output_dir, rel))
    if cand != root and not cand.startswith(root + os.sep):
        return None
    return cand if os.path.isfile(cand) else None


def _apply_obs_transform_fallback(frame: np.ndarray, service: AllSkyService) -> Optional[bytes]:
    """
    Apply OBS zoom, pan, rotate, flip, brightness/contrast, title banner, ADS-B radar,
    recent event streaks, and event/product replay server-side for legacy OBS Browser Source
    instances that have not yet reloaded v2 HTML.
    """
    import os
    obs = service.get_obs_overlay()
    mode = str(obs.get("view_mode") or "live")
    zoom = float(obs.get("zoom") or 1.0)
    pan_x = float(obs.get("pan_x") or 0.0)
    pan_y = float(obs.get("pan_y") or 0.0)
    rot = float(obs.get("rotate") or 0.0)
    flip_h = bool(obs.get("flip_h"))
    flip_v = bool(obs.get("flip_v"))
    bright = float(obs.get("brightness") or 1.0)
    contrast = float(obs.get("contrast") or 1.0)
    replay_url = str(obs.get("replay_url") or "")
    crop_url = str(obs.get("replay_crop_url") or "")
    replay_label = str(obs.get("replay_label") or "")
    obs.get("replay_bbox") or []
    title_text = str(obs.get("title_text") or "").strip()
    title_sub = str(obs.get("title_sub") or "").strip()
    title_pos = str(obs.get("title_pos") or "bottom-left")
    show_events = bool(obs.get("events_overlay"))
    show_adsb = bool(obs.get("adsb_overlay"))

    needs_work = (
        mode != "live"
        or abs(zoom - 1.0) > 0.01
        or abs(pan_x) > 0.1
        or abs(pan_y) > 0.1
        or abs(rot) > 0.1
        or flip_h
        or flip_v
        or abs(bright - 1.0) > 0.01
        or abs(contrast - 1.0) > 0.01
        or bool(title_text)
        or show_events
        or show_adsb
    )
    if not needs_work:
        return None

    img = frame.copy()
    h, w = img.shape[:2]

    # 1. If in replay mode (event, keogram, startrails, timelapse), load the target image/frame
    if mode in ("event", "keogram", "startrails", "timelapse") and replay_url:
        local_path = None
        if replay_url.startswith("/api/events/img/"):
            parts = replay_url.split("/")
            if len(parts) >= 6:
                local_path = service.event_image_path(parts[4], parts[5].split("?")[0])
        elif replay_url.startswith("/uploads/"):
            local_path = resolve_uploads_path(service.output_dir, replay_url)
        if local_path and os.path.isfile(local_path) and not local_path.endswith(".mp4"):
            loaded = cv2.imread(local_path, cv2.IMREAD_COLOR)
            if loaded is not None and loaded.size > 0:
                img = cv2.resize(loaded, (w, h), interpolation=cv2.INTER_LINEAR)


    # 1c. Draw recent event streaks if events_overlay is enabled
    if show_events and mode in ("live", "event"):
        try:
            ev_data = service.get_events(limit=12)
            for ev in (ev_data.get("events") or [])[:12]:
                ln = ev.get("line")
                if isinstance(ln, list) and len(ln) >= 4:
                    p1 = (int(float(ln[0]) * w), int(float(ln[1]) * h))
                    p2 = (int(float(ln[2]) * w), int(float(ln[3]) * h))
                    col = (11, 158, 245) if ev.get("type") == "meteor" else (248, 189, 56)
                    cv2.line(img, p1, p2, col, 2, cv2.LINE_AA)
                    cv2.circle(img, p2, 5, col, -1, cv2.LINE_AA)
        except Exception:
            pass

    # 1d. Draw live ADS-B aircraft positions on sky if adsb_overlay is enabled
    if show_adsb and mode == "live":
        try:
            ac_data = service.get_aircraft_now(limit=30)
            for ac in ac_data.get("aircraft") or []:
                fxy = ac.get("frame_xy")
                if isinstance(fxy, list) and len(fxy) == 2:
                    ax, ay = int(float(fxy[0]) * w), int(float(fxy[1]) * h)
                    cv2.circle(img, (ax, ay), 9, (129, 185, 16), 2, cv2.LINE_AA)
                    call = str(ac.get("callsign") or ac.get("icao") or "ACFT")
                    alt_km = f"{ac['altitude_m']/1000:.1f}km" if ac.get("altitude_m") else ""
                    cv2.putText(img, f"{call} {alt_km}", (ax + 12, ay + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (153, 211, 52), 2, cv2.LINE_AA)
        except Exception:
            pass

    # 2. Brightness & contrast
    if abs(bright - 1.0) > 0.01 or abs(contrast - 1.0) > 0.01:
        beta = (bright - 1.0) * 100.0
        img = cv2.convertScaleAbs(img, alpha=contrast, beta=beta)

    # 3. Flip
    if flip_h and flip_v:
        img = cv2.flip(img, -1)
    elif flip_h:
        img = cv2.flip(img, 1)
    elif flip_v:
        img = cv2.flip(img, 0)

    # 4. Rotate
    if abs(rot) > 0.1:
        mat = cv2.getRotationMatrix2D((w * 0.5, h * 0.5), -rot, 1.0)
        img = cv2.warpAffine(img, mat, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)

    # 5. Zoom & Pan
    if zoom > 1.01:
        crop_w = max(64, int(round(w / zoom)))
        crop_h = max(48, int(round(h / zoom)))
        cx = int(round(w * 0.5 - (pan_x / 100.0) * w * 0.5))
        cy = int(round(h * 0.5 - (pan_y / 100.0) * h * 0.5))
        x0 = max(0, min(w - crop_w, cx - crop_w // 2))
        y0 = max(0, min(h - crop_h, cy - crop_h // 2))
        cropped = img[y0 : y0 + crop_h, x0 : x0 + crop_w]
        img = cv2.resize(cropped, (w, h), interpolation=cv2.INTER_LINEAR)

    # 6. Picture-in-Picture close-up crop for event replay
    if mode == "event" and crop_url and crop_url.startswith("/api/events/img/"):
        parts = crop_url.split("/")
        if len(parts) >= 6:
            cpath = service.event_image_path(parts[4], parts[5].split("?")[0])
            if cpath and os.path.isfile(cpath):
                cimg = cv2.imread(cpath, cv2.IMREAD_COLOR)
                if cimg is not None and cimg.size > 0:
                    pw = min(420, w // 4)
                    pip = cv2.resize(cimg, (pw, pw), interpolation=cv2.INTER_LINEAR)
                    y1, x1 = h - pw - 28, w - pw - 28
                    img[y1 : y1 + pw, x1 : x1 + pw] = pip
                    cv2.rectangle(img, (x1, y1), (x1 + pw, y1 + pw), (248, 189, 56), 3)

    # 7. Title / Lower-Third Banner (rendered onto fallback frame with PIL if available or OpenCV)
    if title_text or (mode != "live" and replay_label):
        try:
            from PIL import Image, ImageDraw, ImageFont
            pil_img = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
            draw = ImageDraw.Draw(pil_img, "RGBA")
            font_lg = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", max(22, w // 68))
            font_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", max(16, w // 96))

            if title_text:
                tb = draw.textbbox((0, 0), title_text, font=font_lg)
                tw, th = tb[2] - tb[0], tb[3] - tb[1]
                sw, sh = 0, 0
                if title_sub:
                    sb = draw.textbbox((0, 0), title_sub, font=font_sm)
                    sw, sh = sb[2] - sb[0], sb[3] - sb[1] + 6
                bw = max(tw, sw) + 36
                bh = th + sh + 24
                bx = (w - bw) // 2 if "center" in title_pos else 28
                by = 56 if "top" in title_pos else (h - bh - 28)
                draw.rounded_rectangle([bx, by, bx + bw, by + bh], radius=8, fill=(2, 6, 23, 215), outline=(56, 189, 248, 220), width=2)
                draw.text((bx + 18, by + 10), title_text, font=font_lg, fill=(255, 255, 255, 255))
                if title_sub:
                    draw.text((bx + 18, by + 14 + th), title_sub, font=font_sm, fill=(125, 211, 252, 255))

            if mode != "live" and replay_label:
                rb = draw.textbbox((0, 0), replay_label, font=font_sm)
                rw, rh = rb[2] - rb[0] + 28, rb[3] - rb[1] + 16
                rx, ry = w - rw - 24, 20
                draw.rounded_rectangle([rx, ry, rx + rw, ry + rh], radius=6, fill=(15, 23, 42, 225), outline=(245, 158, 11, 230), width=2)
                draw.text((rx + 14, ry + 7), replay_label, font=font_sm, fill=(254, 243, 199, 255))

            img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
        except Exception:
            pass

    ret, jpeg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return jpeg.tobytes() if ret else None


async def _build_full_binary_packet(
    service: AllSkyService,
    packet_type: int = 1,
    full_res: bool = False,
    client_v2: bool = False,
    last_ts: Optional[float] = None,
) -> tuple:
    """Capture/fetch latest telemetry, status, and JPEG and pack into a single ASB1 binary frame.
    Returns (packet, frame_ts). With last_ts given, a frame already sent (same timestamp) is not encoded or attached again:
    the packet then carries telemetry only (the page keeps showing the picture it has)."""
    telem = await asyncio.to_thread(service.get_telemetry)
    status = await asyncio.to_thread(service.get_status)
    jpeg_bytes = b""
    frame, frame_ts = service.get_display_frame()
    if frame is not None and last_ts is not None and frame_ts == last_ts and not full_res:
        frame = None
    if frame is not None:
        if full_res:
            custom_jpeg = None if client_v2 else await asyncio.to_thread(_apply_obs_transform_fallback, frame, service)
            if custom_jpeg is not None:
                jpeg_bytes = custom_jpeg
                frame_ts = time.time()  # force frame swap on client even when camera exposure is long
            else:
                jpeg_bytes = service.last_jpeg or await asyncio.to_thread(service.get_snapshot_jpeg)
        else:
            # Viewers poll faster than the camera delivers; encode each frame once and share it
            cached = _stream_jpeg_cache
            if cached[0] is frame and cached[1] == frame_ts:
                jpeg_bytes = cached[2]
            else:
                perf = service.performance
                jpeg_bytes = await asyncio.to_thread(
                    _encode_stream_jpeg, frame, int(perf["stream_max_width"]), int(perf["stream_jpeg_quality"])
                ) or b""
                _stream_jpeg_cache[:] = [frame, frame_ts, jpeg_bytes]
    return pack_asb1_binary_packet(telem, status, jpeg_bytes, packet_type=packet_type, capture_ts=frame_ts), frame_ts


# One computation per second shared by every connected page: layer -> (monotonic time, payload)
_overlay_cache: Dict[str, tuple] = {}

# [frame object, capture timestamp, encoded JPEG] of the last frame sent to WebSocket viewers
_stream_jpeg_cache: list = [None, -1.0, b""]


# WebSocket messages that change state: only a signed-in admin (cookie from the handshake) may send them
WS_MUTATING_ACTIONS = frozenset({"set_exposure", "set_gain", "set_mode", "trigger_master_dark"})
WS_GUEST_ACTIONS = frozenset({"ping", "get_telemetry", "get_status", "subscribe", "idle"})
WS_MUTATING_COMMANDS = frozenset({1, 2})  # ASC1 binary: 1 = set_mode, 2 = set_exposure + gain
WS_AUTH_ERROR = {"type": "error", "code": "auth", "message": "Sign in as admin"}


async def sse_event_generator(service: AllSkyService, interval: float = 1.0, view=None,
                              max_seconds: Optional[float] = None):
    """
    Server-Sent Events (SSE) generator yielding real-time telemetry and status pushes
    without client HTTP fetch polling. `view` filters the payload for the caller (guests get rounded coordinates).
    """
    view = view or (lambda p: p)
    started = time.monotonic()
    while True:
        if max_seconds is not None and time.monotonic() - started >= max_seconds:
            break
        try:
            status_payload = await asyncio.to_thread(service.get_status)
            telem_payload = await asyncio.to_thread(service.get_telemetry)
            payload = json.dumps(view({
                "type": "telemetry",
                "status": status_payload,
                "telemetry": telem_payload,
            }))
            yield f"data: {payload}\n\n"
        except asyncio.CancelledError:
            break
        except Exception:
            pass
        await asyncio.sleep(max(0.25, interval))


async def handle_live_websocket(websocket: WebSocket, service: AllSkyService):
    """
    Handles bidirectional WebSocket communication on /ws/live.
    - When ?binary=1 is passed: binary protocol (ASB1 server->client, ASC1 client->server); the only
      text message is a small {"type": "ae_lock"} JSON sent when the lock state or frame brightness changes.
    - When ?full=1 is also passed: sends full native resolution JPEG (service.last_jpeg) instead of stream_max_width.
    - When ?binary=1 is omitted: legacy JSON mode for unit/E2E test compatibility.
    """
    slot_key: Optional[str] = None
    if not is_admin(websocket):
        slot_key = stream_client_key(websocket)
        if not stream_limiter.acquire(slot_key):
            await websocket.accept()
            await websocket.close(code=1013)  # try again later: too many live streams
            return
    try:
        await _live_websocket_session(websocket, service)
    finally:
        if slot_key is not None:
            stream_limiter.release(slot_key)


def _ws_action_ok(action: str, may_change: bool) -> bool:
    """Gate for text commands: the auth policy decides when available; fallback = mutating actions need admin and
    guests only get the read-only ones."""
    try:
        from src.auth.policy import ws_action_allowed
        return bool(ws_action_allowed(action, may_change))
    except (ImportError, AttributeError):
        if action in WS_MUTATING_ACTIONS:
            return may_change
        return may_change or action in WS_GUEST_ACTIONS


async def _live_websocket_session(websocket: WebSocket, service: AllSkyService):
    await ws_manager.connect(websocket)
    binary_mode = websocket.query_params.get("binary", "0") in ("1", "true", "yes")
    full_res = websocket.query_params.get("full", "0") in ("1", "true", "yes")
    client_v2 = websocket.query_params.get("v", "") == "2"
    view = view_for(is_admin(websocket), service.public_precision)  # guests get rounded station coordinates
    if not is_admin(websocket):
        view = guest_view(view)
    may_change = can_mutate(websocket)  # admin session from the handshake cookie + same-site Origin
    try:
        if binary_mode:
            pkt, first_ts = await _build_full_binary_packet(service, packet_type=1, full_res=full_res, client_v2=client_v2)
            await websocket.send_bytes(pkt)
        else:
            status_payload = await asyncio.to_thread(service.get_status)
            telem_payload = await asyncio.to_thread(service.get_telemetry)
            await websocket.send_json(view({
                "type": "welcome",
                "status": status_payload,
                "telemetry": telem_payload,
            }))

        # Lock state is not in ASB1: push it as text whenever it (or the frame brightness) changes
        last_lock = None

        async def push_lock_state():
            nonlocal last_lock
            payload = {"type": "ae_lock", "mode": service.mode, "ae_lock": service.get_ae_lock()}
            if payload != last_lock:
                last_lock = payload
                await websocket.send_text(json.dumps(payload))

        if binary_mode:
            await push_lock_state()

        # Background streamer task
        idle = {"on": False}      # the page is in a hidden tab: nothing but a slow heartbeat
        async def live_streamer():
            sent_ts = first_ts if binary_mode else None   # capture time of the picture this client already holds
            last_sent = time.monotonic()
            while True:
                if idle.pop("kick", False):       # the tab is visible again: send the current picture now
                    sent_ts, last_sent = None, 0.0
                exp_s = float(getattr(service.camera, "current_exposure_s", 0.1))
                sleep_s = 0.45 if (full_res and not client_v2) else max(0.35, min(2.0, exp_s * 0.95))
                await asyncio.sleep(sleep_s)
                if binary_mode:
                    # A picture goes out once. Without a new one, only a telemetry-only packet every 5 s (30 s while hidden)
                    # keeps the page's clock and "online" state alive; the same JPEG is never sent twice.
                    frame_ts = service.get_display_frame()[1]
                    new_frame = frame_ts != sent_ts or full_res
                    if idle["on"] and time.monotonic() - last_sent < 30.0:
                        continue
                    if not new_frame and time.monotonic() - last_sent < 5.0:
                        await push_lock_state()
                        continue
                    pkt, sent = await _build_full_binary_packet(service, packet_type=1, full_res=full_res, client_v2=client_v2,
                                                                last_ts=None if full_res else sent_ts)
                    if sent is not None:
                        sent_ts = sent
                    last_sent = time.monotonic()
                    await websocket.send_bytes(pkt)
                    await push_lock_state()
                else:
                    telem = await asyncio.to_thread(service.get_telemetry)
                    status = await asyncio.to_thread(service.get_status)
                    await websocket.send_json(view({
                        "type": "telemetry",
                        "data": telem,
                        "status": status,
                    }))

        streamer_task = asyncio.create_task(live_streamer())

        # Overlay layers the page asked for ("subscribe"): pushed here instead of the page polling REST every 1.5 s
        subs: set = set()
        subs_hold: set = set()     # what the page asked for while its tab is hidden
        last_push: Dict[str, float] = {}
        OVERLAY_EVERY_S = {"aircraft": 1.5, "satellites": 1.5, "sondes": 3.0, "flights": 5.0, "events": 2.0, "logs": 2.0}
        admin_ws = is_admin(websocket)

        def overlay_payload(layer: str):
            if layer in ("aircraft", "sondes", "flights"):
                hit = _overlay_cache.get(layer)
                if hit and time.monotonic() - hit[0] < 1.0:
                    return hit[1]
                data = _overlay_compute(layer)
                _overlay_cache[layer] = (time.monotonic(), data)
                return data
            return _overlay_compute(layer)

        def _overlay_compute(layer: str):
            if layer == "aircraft":
                return service.get_aircraft_now(limit=60, trail_s=0.0, bounds=None, include_fr24=None, compact=False)
            if layer == "sondes":
                return service.get_radiosondes_now(limit=40)
            if layer == "flights":
                return service.get_aircraft_now(limit=12, trail_s=0.0, bounds=None, include_fr24=None, compact=False)
            if layer == "events":   # only a version: the page fetches the list when it changes
                ev = service._load_events()
                return {"v": [len(ev), ev[0].get("id") or ev[0].get("ts") or ev[0].get("time") if ev else None]}
            if layer == "logs":
                from src.activity import get_activity_log
                return {"v": get_activity_log()._next_id}
            from src.api.routes.common import _sat_cache
            return _sat_cache.get_or_compute((0.0, False, 60, tuple(), 0.0), lambda: service.get_satellites_now(
                track_min=0.0, below=False, limit=60, trail_s=0.0, track_ids=set()))

        async def overlay_pusher():
            last = last_push
            sent_v: Dict[str, Any] = {}
            sent_h: Dict[str, int] = {}
            while True:
                await asyncio.sleep(0.5)
                now = time.time()
                for layer in list(subs):
                    if now - last.get(layer, 0.0) < OVERLAY_EVERY_S[layer]:
                        continue
                    last[layer] = now
                    try:
                        if layer == "logs" and not admin_ws:   # the activity log is admin-only
                            continue
                        data = await asyncio.to_thread(overlay_payload, layer)
                        if layer in ("events", "logs"):
                            if sent_v.get(layer) == data["v"]:
                                continue
                            sent_v[layer] = data["v"]
                        out = view({"type": "overlay", "layer": layer, "data": data})
                        # Skip a push whose content equals the last one (timestamps aside): nothing moved, nothing to draw
                        body = json.dumps(out["data"], separators=(",", ":"), sort_keys=True, default=str)
                        h = hash(body.replace(str(data.get("t", "")), "") if isinstance(data, dict) else body)
                        if sent_h.get(layer) == h:
                            continue
                        sent_h[layer] = h
                        await websocket.send_text(json.dumps(out, separators=(",", ":"), default=str))
                    except WebSocketDisconnect:
                        raise
                    except Exception:  # a failing layer must not end the live socket; the page falls back to polling it
                        last[layer] = now + 10.0

        overlay_task = asyncio.create_task(overlay_pusher())

        try:
            while True:
                msg = await websocket.receive()
                if msg.get("type") == "websocket.disconnect":
                    break

                # 1. Binary command packet ("ASC1")
                raw_bytes = msg.get("bytes")
                if raw_bytes is not None:
                    if len(raw_bytes) >= ASC1_CMD_STRUCT.size and raw_bytes[:4] == b"ASC1":
                        _, cmd_id, mode_val, _, exp_val, gain_val = ASC1_CMD_STRUCT.unpack(
                            raw_bytes[:ASC1_CMD_STRUCT.size]
                        )
                        if cmd_id in WS_MUTATING_COMMANDS and not may_change:
                            await websocket.send_json(WS_AUTH_ERROR)
                            continue
                        if cmd_id == 1:  # set_mode
                            target_mode = "auto" if mode_val == 1 else "manual"
                            await asyncio.to_thread(service.set_mode, target_mode)
                        elif cmd_id == 2:  # set_exposure + gain
                            await asyncio.to_thread(service.set_exposure, float(exp_val), float(gain_val))
                        pkt, _ = await _build_full_binary_packet(service, packet_type=2)
                        await websocket.send_bytes(pkt)
                    continue

                # 2. Text command packet (for legacy /ws/live tests or text clients)
                data_text = msg.get("text")
                if not data_text:
                    continue
                try:
                    cmd = json.loads(data_text)
                    if not isinstance(cmd, dict):
                        continue
                    raw_action = cmd.get("action", "")
                    action = raw_action if isinstance(raw_action, str) else ""
                    if not _ws_action_ok(action, may_change):
                        await websocket.send_json(WS_AUTH_ERROR)
                        continue

                    if action == "ping":
                        await websocket.send_json({"type": "pong", "timestamp": time.time()})
                    elif action == "subscribe":
                        want = {l for l in (cmd.get("layers") or []) if l in OVERLAY_EVERY_S}
                        (subs_hold if idle["on"] else subs).clear()
                        (subs_hold if idle["on"] else subs).update(want)
                        last_push.clear()
                        await websocket.send_json({"type": "subscribed", "layers": sorted(want)})
                    elif action == "idle":   # the tab was hidden / shown again
                        idle["on"] = bool(cmd.get("on"))
                        if not idle["on"]:
                            idle["kick"] = True
                        if idle["on"]:
                            subs_hold.update(subs)
                            subs.clear()
                        else:
                            subs.update(subs_hold)
                            subs_hold.clear()
                            last_push.clear()
                    elif action == "get_telemetry":
                        telem = await asyncio.to_thread(service.get_telemetry)
                        await websocket.send_json(view({"type": "telemetry", "data": telem}))
                    elif action == "get_status":
                        status = await asyncio.to_thread(service.get_status)
                        await websocket.send_json(view({"type": "status", "data": status}))
                    elif action == "set_exposure":
                        exp = float(cmd.get("exposure_s", 2.0))
                        gain = cmd.get("gain_db")
                        gain_val = float(gain) if gain is not None else None
                        res = await asyncio.to_thread(service.set_exposure, exp, gain_val)
                        if binary_mode:
                            pkt, _ = await _build_full_binary_packet(service, packet_type=2)
                            await websocket.send_bytes(pkt)
                        else:
                            await websocket.send_json({"type": "exposure_result", "data": res})
                    elif action == "set_gain":
                        gain = float(cmd.get("gain_db", 24.0))
                        res = await asyncio.to_thread(service.set_gain, gain)
                        if binary_mode:
                            pkt, _ = await _build_full_binary_packet(service, packet_type=2)
                            await websocket.send_bytes(pkt)
                        else:
                            await websocket.send_json({"type": "gain_result", "data": res})
                    elif action == "set_mode":
                        mode = str(cmd.get("mode", "manual"))
                        res = await asyncio.to_thread(service.set_mode, mode)
                        if binary_mode:
                            pkt, _ = await _build_full_binary_packet(service, packet_type=2)
                            await websocket.send_bytes(pkt)
                        else:
                            await websocket.send_json({"type": "mode_result", "data": res})
                    elif action == "trigger_master_dark":
                        num_frames = int(cmd.get("num_frames", 5))
                        exp = float(cmd.get("exposure_s", 2.0))
                        gain = float(cmd.get("gain_db", 36.0))
                        res = await asyncio.to_thread(service.trigger_master_dark, num_frames, exp, gain)
                        await websocket.send_json({"type": "master_dark_result", "data": res})
                    else:
                        await websocket.send_json({"type": "ack", "received": action})
                except json.JSONDecodeError:
                    if data_text.strip() == "ping":
                        await websocket.send_text("pong")
        finally:
            streamer_task.cancel()
            overlay_task.cancel()
    except WebSocketDisconnect:
        pass
    finally:
        ws_manager.disconnect(websocket)
