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
from fastapi.responses import StreamingResponse

from src.api.service import AllSkyService

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


async def mjpeg_frame_generator(
    service: AllSkyService,
    fps: float = 5.0,
    downscale_width: int = 960,
    max_frames: Optional[int] = None,
):
    """
    Asynchronous generator yielding multipart JPEG frames for browser video streaming.
    Decimates resolution to save edge network bandwidth.
    """
    interval = 1.0 / max(0.5, fps)
    frames_sent = 0
    while True:
        if max_frames is not None and frames_sent >= max_frames:
            break
        t_start = time.perf_counter()
        try:
            frame, _ = await asyncio.to_thread(service.capture_frame)
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
        float(time.time()),
        float(telem.get("exposure_s", 0.04)),
        float(telem.get("gain_db", 12.0)),
        float(telem.get("hw_frame_delta_ms", 40.0)),
        float(telem.get("fps", 1.0)),
        int(telem.get("star_count", 0)),
        float(telem.get("cloud_cover_pct", 0.0)),
        float(telem.get("sensor_temp_c", 24.5)),
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


async def _build_full_binary_packet(service: AllSkyService, packet_type: int = 1) -> bytes:
    """Capture/fetch latest telemetry, status, and JPEG and pack into a single ASB1 binary frame."""
    telem = await asyncio.to_thread(service.get_telemetry)
    status = await asyncio.to_thread(service.get_status)
    jpeg_bytes = b""
    frame, frame_ts = service.get_display_frame()
    if frame is not None:
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
    return pack_asb1_binary_packet(telem, status, jpeg_bytes, packet_type=packet_type)


# [frame object, capture timestamp, encoded JPEG] of the last frame sent to WebSocket viewers
_stream_jpeg_cache: list = [None, -1.0, b""]


async def sse_event_generator(service: AllSkyService, interval: float = 1.0):
    """
    Server-Sent Events (SSE) generator yielding real-time telemetry and status pushes
    without client HTTP fetch polling.
    """
    while True:
        try:
            status_payload = await asyncio.to_thread(service.get_status)
            telem_payload = await asyncio.to_thread(service.get_telemetry)
            payload = json.dumps({
                "type": "telemetry",
                "status": status_payload,
                "telemetry": telem_payload,
            })
            yield f"data: {payload}\n\n"
        except asyncio.CancelledError:
            break
        except Exception:
            pass
        await asyncio.sleep(max(0.25, interval))


async def handle_live_websocket(websocket: WebSocket, service: AllSkyService):
    """
    Handles bidirectional WebSocket communication on /ws/live.
    - When ?binary=1 is passed: 100% pure binary protocol (ASB1 server->client, ASC1 client->server),
      zero JSON text messages sent over the wire.
    - When ?binary=1 is omitted: legacy JSON mode for unit/E2E test compatibility.
    """
    await ws_manager.connect(websocket)
    binary_mode = websocket.query_params.get("binary", "0") in ("1", "true", "yes")
    try:
        if binary_mode:
            pkt = await _build_full_binary_packet(service, packet_type=1)
            await websocket.send_bytes(pkt)
        else:
            status_payload = await asyncio.to_thread(service.get_status)
            telem_payload = await asyncio.to_thread(service.get_telemetry)
            await websocket.send_json({
                "type": "welcome",
                "status": status_payload,
                "telemetry": telem_payload,
            })

        # Background streamer task
        async def live_streamer():
            while True:
                exp_s = float(getattr(service.camera, "current_exposure_s", 0.1))
                sleep_s = max(0.35, min(2.0, exp_s * 0.95))
                await asyncio.sleep(sleep_s)
                if binary_mode:
                    pkt = await _build_full_binary_packet(service, packet_type=1)
                    await websocket.send_bytes(pkt)
                else:
                    telem = await asyncio.to_thread(service.get_telemetry)
                    status = await asyncio.to_thread(service.get_status)
                    await websocket.send_json({
                        "type": "telemetry",
                        "data": telem,
                        "status": status,
                    })

        streamer_task = asyncio.create_task(live_streamer())

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
                        if cmd_id == 1:  # set_mode
                            target_mode = "auto" if mode_val == 1 else "manual"
                            await asyncio.to_thread(service.set_mode, target_mode)
                        elif cmd_id == 2:  # set_exposure + gain
                            await asyncio.to_thread(service.set_exposure, float(exp_val), float(gain_val))
                        pkt = await _build_full_binary_packet(service, packet_type=2)
                        await websocket.send_bytes(pkt)
                    continue

                # 2. Text command packet (for legacy /ws/live tests or text clients)
                data_text = msg.get("text")
                if not data_text:
                    continue
                try:
                    cmd = json.loads(data_text)
                    action = cmd.get("action", "")

                    if action == "ping":
                        await websocket.send_json({"type": "pong", "timestamp": time.time()})
                    elif action == "get_telemetry":
                        telem = await asyncio.to_thread(service.get_telemetry)
                        await websocket.send_json({"type": "telemetry", "data": telem})
                    elif action == "get_status":
                        status = await asyncio.to_thread(service.get_status)
                        await websocket.send_json({"type": "status", "data": status})
                    elif action == "set_exposure":
                        exp = float(cmd.get("exposure_s", 2.0))
                        gain = cmd.get("gain_db")
                        gain_val = float(gain) if gain is not None else None
                        res = await asyncio.to_thread(service.set_exposure, exp, gain_val)
                        if binary_mode:
                            pkt = await _build_full_binary_packet(service, packet_type=2)
                            await websocket.send_bytes(pkt)
                        else:
                            await websocket.send_json({"type": "exposure_result", "data": res})
                    elif action == "set_gain":
                        gain = float(cmd.get("gain_db", 24.0))
                        res = await asyncio.to_thread(service.set_gain, gain)
                        if binary_mode:
                            pkt = await _build_full_binary_packet(service, packet_type=2)
                            await websocket.send_bytes(pkt)
                        else:
                            await websocket.send_json({"type": "gain_result", "data": res})
                    elif action == "set_mode":
                        mode = str(cmd.get("mode", "manual"))
                        res = await asyncio.to_thread(service.set_mode, mode)
                        if binary_mode:
                            pkt = await _build_full_binary_packet(service, packet_type=2)
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
    except WebSocketDisconnect:
        pass
    finally:
        ws_manager.disconnect(websocket)
