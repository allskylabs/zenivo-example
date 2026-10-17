"""
Authoritative Reference FastAPI Application for IMX335 Edge Web API Testing.
Derived directly from ORIGINAL_REQUEST.md § R3, PROJECT.md § 1, and survey_allsky_pipeline.md § 4.
"""

import time
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import numpy as np
import cv2

from fastapi import FastAPI, HTTPException, Query, WebSocket, status
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from src.hardware.simulator import SimulatedIMX335Controller
from src.hardware.registers import calculate_exposure_registers, calculate_gain_register
from tests.fixtures.astro_reference import (
    reference_generate_keogram,
    reference_generate_startrails,
    reference_star_count_and_cloud_cover,
)


# Pydantic Schemas
class ExposureControlRequest(BaseModel):
    exposure_s: float = Field(..., ge=0.001, le=90.0, description="Exposure duration in seconds")
    gain_db: Optional[float] = Field(None, ge=0.0, le=72.0, description="Analog/digital gain in dB")


class GainControlRequest(BaseModel):
    gain_db: float = Field(..., ge=0.0, le=72.0, description="Sensor gain in dB")


class MasterDarkRequest(BaseModel):
    num_frames: int = Field(default=9, ge=3, le=50)
    exposure_s: float = Field(default=2.0, ge=0.001, le=90.0)
    gain_db: float = Field(default=36.0, ge=0.0, le=72.0)


def compute_fast_histogram(img_bgr: np.ndarray) -> Dict[str, Any]:
    """
    Compute 256-bin RGB and Luma histogram in < 10ms via subsampling.
    """
    t0 = time.perf_counter()
    # 4x subsampling (1/16 pixels) for ultra-fast calculation
    sub = img_bgr[::4, ::4, :]

    b_hist = np.bincount(sub[:, :, 0].ravel(), minlength=256)[:256].tolist()
    g_hist = np.bincount(sub[:, :, 1].ravel(), minlength=256)[:256].tolist()
    r_hist = np.bincount(sub[:, :, 2].ravel(), minlength=256)[:256].tolist()

    y = (
        0.114 * sub[:, :, 0].astype(np.float32)
        + 0.587 * sub[:, :, 1].astype(np.float32)
        + 0.299 * sub[:, :, 2].astype(np.float32)
    ).astype(np.uint8)
    y_hist = np.bincount(y.ravel(), minlength=256)[:256].tolist()

    total_px = sub.shape[0] * sub.shape[1]
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    return {
        "r": r_hist,
        "g": g_hist,
        "b": b_hist,
        "y": y_hist,
        "calculation_time_ms": round(elapsed_ms, 3),
        "stats": {
            "mean": round(float(np.mean(y)), 2),
            "stddev": round(float(np.std(y)), 2),
            "median": int(np.median(y)),
            "clipping_low_pct": round((y_hist[0] / total_px) * 100.0, 3),
            "clipping_high_pct": round((y_hist[255] / total_px) * 100.0, 3),
        },
    }


def create_reference_allsky_app(sim_camera: Optional[SimulatedIMX335Controller] = None) -> FastAPI:
    """Create a fully-featured FastAPI reference application for AC3 verification."""
    app = FastAPI(title="IMX335 AllSky Observatory Edge API", version="1.0.0")

    camera = sim_camera or SimulatedIMX335Controller(width=640, height=480, seed=42)
    camera.open()

    events_db: List[Dict[str, Any]] = [
        {
            "event_id": "meteor_20261014_214512_001",
            "type": "meteor",
            "timestamp": "2026-10-14T21:45:12.430Z",
            "length_px": 145.2,
            "aspect_ratio": 7.4,
            "peak_dn": 248,
            "bbox": [200, 150, 95, 110],
        }
    ]

    @app.get("/api/status")
    def get_status():
        hmax, vmax, shr0, actual_exp = calculate_exposure_registers(camera.current_exposure_s)
        pgc, actual_gain = calculate_gain_register(camera.current_gain_db)
        return {
            "status": "online",
            "camera_connected": True,
            "hardware": {
                "vid_pid": "0bda:5869",
                "sensor": "Sony Starvis IMX335",
                "resolution": f"{camera.width}x{camera.height}",
                "registers": {
                    "VMAX": vmax,
                    "HMAX": hmax,
                    "SHR0": shr0,
                    "PGC": pgc,
                },
                "timing": {
                    "exposure_s": round(actual_exp, 6),
                    "gain_db": actual_gain,
                    "hw_frame_delta_ms": round(actual_exp * 1000.0, 2),
                },
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    @app.get("/api/telemetry")
    def get_telemetry():
        frame, meta = camera.capture_frame()
        sky_stats = reference_star_count_and_cloud_cover(frame)
        hist = compute_fast_histogram(frame)

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "exposure_s": meta["actual_exposure_s"],
            "gain_db": meta["gain_db"],
            "hw_frame_delta_ms": meta["hw_frame_delta"],
            "star_count": sky_stats["star_count"],
            "cloud_cover_pct": sky_stats["cloud_cover_pct"],
            "sky_status": sky_stats["status"],
            "histogram": hist,
            "sensor_temp_c": 24.5,
            "cpu_load_pct": 18.2,
        }

    @app.post("/api/camera/exposure")
    def set_exposure(req: ExposureControlRequest):
        gain = req.gain_db if req.gain_db is not None else camera.current_gain_db
        reg_info = camera.set_hardware_exposure(req.exposure_s, gain)
        return {"success": True, "settings": reg_info}

    @app.post("/api/camera/gain")
    def set_gain(req: GainControlRequest):
        reg_info = camera.set_hardware_exposure(camera.current_exposure_s, req.gain_db)
        return {"success": True, "settings": reg_info}

    @app.post("/api/calibration/master-dark")
    def trigger_master_dark(req: MasterDarkRequest):
        # Emulate dark capture
        camera.set_hardware_exposure(req.exposure_s, req.gain_db)
        darks = [camera.capture_frame()[0] for _ in range(req.num_frames)]
        master = np.median(np.stack(darks, axis=0), axis=0).astype(np.uint8)
        hot_pixels = int(np.sum(master[:, :, 1] >= 100))
        filename = f"master_dark_exp{req.exposure_s:.1f}s_gain{req.gain_db:.0f}dB.png"
        return {
            "success": True,
            "filename": filename,
            "frames_stacked": req.num_frames,
            "hot_pixels_detected": hot_pixels,
        }

    @app.get("/api/live/snapshot.jpg")
    def get_snapshot():
        frame, _ = camera.capture_frame()
        ret, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ret:
            raise HTTPException(status_code=500, detail="JPEG encoding failed")
        return Response(content=jpeg.tobytes(), media_type="image/jpeg")

    @app.get("/api/stream/histogram")
    def get_histogram():
        frame, _ = camera.capture_frame()
        hist_data = compute_fast_histogram(frame)
        return hist_data

    @app.get("/api/gallery")
    def get_gallery():
        return {
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "items": [
                {"type": "keogram", "url": "/uploads/allsky/keograms/latest.jpg"},
                {"type": "startrails", "url": "/uploads/allsky/startrails/latest.jpg"},
                {"type": "timelapse", "url": "/uploads/allsky/timelapses/latest.mp4"},
            ],
        }

    @app.get("/api/events/meteors")
    def get_meteor_events(limit: int = Query(default=20, ge=1, le=100)):
        return {"events": events_db[:limit], "total_count": len(events_db)}

    return app
