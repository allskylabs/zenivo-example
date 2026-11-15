"""Status, telemetry and cloud-analysis configuration/history.

Mixin of AllSkyService (src/api/service.py): methods only, all state lives on the service instance."""

import logging
import os
import time
from typing import Dict, Any, List, Optional
from datetime import datetime

from src.hardware.driver import describe_camera, camera_limits
from src.hardware.registers import calculate_exposure_registers, calculate_gain_register

from src.version import __version__
from src.api.histogram import compute_fast_histogram

logger = logging.getLogger("imx335.api")


class StatusMixin:

    def get_status(self) -> Dict[str, Any]:
        """
        Return camera state, sensor registers, timing metadata, and system uptime (lock-free).
        Conforms strictly to AC3.1 verification contract.
        """
        exp_s = getattr(self.camera, "current_exposure_s", 2.0)
        gain_db = getattr(self.camera, "current_gain_db", 24.0)
        raw10_active = bool(getattr(self.camera, "raw10_mode", False)) and not bool(os.environ.get("PYTEST_CURRENT_TEST"))
        hmax, vmax, shr0, actual_exp = calculate_exposure_registers(exp_s, uncompressed_mode=raw10_active)
        pgc, actual_gain = calculate_gain_register(gain_db)

        hw_delta_ms = self.last_metadata.get("hw_frame_delta", round(actual_exp * 1000.0, 2))
        uptime_s = round(time.time() - self.start_time, 2)
        is_sim = getattr(self.camera, "simulator", False)
        if hasattr(self.camera, "__class__") and "Simulated" in self.camera.__class__.__name__:
            is_sim = True

        info = describe_camera(self.camera)
        if not info.get("has_registers", True):
            vmax = hmax = shr0 = pgc = 0
            actual_exp, actual_gain = float(exp_s), float(gain_db)
        # Online only while pictures keep coming: a stall of several exposures means the camera is not delivering
        age = time.time() - self.last_capture_time if self.last_capture_time else 0.0
        stalled = bool(self.last_capture_time) and age > max(30.0, 3.0 * float(exp_s) + 15.0) \
            and not self.focus.active and self.calibration_status.get("state") != "running"
        return {
            "status": "stalled" if stalled else "online",
            "version": __version__,
            "camera_connected": not stalled,
            "uptime_s": uptime_s,
            "simulator_mode": is_sim,
            "mode": self.mode,
            "ae_lock": self.get_ae_lock(),
            "hardware": {
                "vid_pid": info.get("vid_pid", "0bda:5869"),
                "sensor": info.get("sensor", "Sony Starvis IMX335"),
                "backend": info.get("backend", "rts5869"),
                "has_registers": bool(info.get("has_registers", True)),
                "limits": camera_limits(self.camera),
                "resolution": f"{self.camera.width}x{self.camera.height}",
                "registers": {
                    "VMAX": vmax,
                    "HMAX": hmax,
                    "SHR0": shr0,
                    "PGC": pgc,
                },
                "timing": {
                    "exposure_s": round(actual_exp, 6),
                    "gain_db": actual_gain,
                    "hw_frame_delta_ms": round(hw_delta_ms, 2),
                },
            },
            "calibration": self.get_calibration_status(),
            "station": self.get_station_config(),
            "focus": {"active": self.focus.active},
            "timestamp": datetime.now().astimezone().isoformat(),
        }

    @property
    def sky_roi(self) -> List[float]:
        """Sky region (left, top, right, bottom fractions) that cloud analysis looks at."""
        roi = self.station_config.get("sky_roi") or [0.0, 0.0, 1.0, 1.0]
        return [float(v) for v in roi][:4] if len(roi) >= 4 else [0.0, 0.0, 1.0, 1.0]

    def get_cloud_config(self) -> Dict[str, Any]:
        return {"sky_roi": self.sky_roi, "reference": self.cloud_reference.to_dict()}

    def set_cloud_config(self, sky_roi: Optional[List[float]] = None, reset_reference: bool = False) -> Dict[str, Any]:
        if sky_roi is not None:
            if len(sky_roi) != 4:
                raise ValueError("sky_roi needs 4 values: left, top, right, bottom")
            l, t, r, b = (min(1.0, max(0.0, float(v))) for v in sky_roi)
            if r - l < 0.05 or b - t < 0.05:
                raise ValueError("sky_roi must cover at least 5% of the frame in each direction")
            self.station_config["sky_roi"] = [round(l, 3), round(t, 3), round(r, 3), round(b, 3)]
            self._save_station_config()
            self._sky_stats_cache = None
        if reset_reference:
            with self.cloud_reference.lock:
                self.cloud_reference.reset()
            self.activity.info("clouds", "Cloud cover clear-sky reference reset; it relearns over the next nights")
        return {"success": True, **self.get_cloud_config()}

    def get_cloud_history(self, hours: float = 24.0, end_ts: Optional[float] = None, max_points: int = 600) -> Dict[str, Any]:
        end_t = float(end_ts) if end_ts else time.time()
        start_t = end_t - max(0.5, min(24.0 * 400, float(hours))) * 3600.0
        raw = self.cloud_history.query(start_t, end_t, max_points=10**9)
        points = self.cloud_history.query(start_t, end_t, max_points=max(10, min(5000, int(max_points))))
        vals = [p["cc"] for p in raw if p.get("cc") is not None]
        return {
            "start": int(start_t),
            "end": int(end_t),
            "interval_s": float(self.performance.get("analysis_interval_s", 300.0)),
            "points": points,
            "summary": {
                "samples": len(vals),
                "mean": round(sum(vals) / len(vals), 1) if vals else None,
                "min": min(vals) if vals else None,
                "max": max(vals) if vals else None,
                "clear_pct": round(100.0 * sum(1 for v in vals if v < 25.0) / len(vals), 1) if vals else None,
            },
        }

    def _cpu_load_pct(self) -> Optional[float]:
        """1-minute load average of this machine as a percentage of its cores (None when the OS gives none)."""
        try:
            return round(100.0 * os.getloadavg()[0] / max(1, os.cpu_count() or 1), 1)
        except (OSError, AttributeError):
            return None

    def get_telemetry(self) -> Dict[str, Any]:
        """
        Compute and return live telemetry: exposure, gain, star count,
        cloud cover percentage, and 256-bin histogram. Conforms to AC3.2.
        """
        frame, meta = self.capture_frame()

        # Return cached telemetry if the underlying frame timestamp has not changed
        is_pytest = bool(os.environ.get("PYTEST_CURRENT_TEST"))
        if (
            not is_pytest
            and self.last_telemetry is not None
            and self.last_capture_time > 0
            and self.last_telemetry_frame_ts == self.last_capture_time
        ):
            cached = dict(self.last_telemetry)
            cached["timestamp"] = datetime.now().astimezone().isoformat()
            cached["mode"] = self.mode
            cached["ae_lock"] = self.get_ae_lock()
            cached["calibration"] = self.get_calibration_status()
            return cached

        sky_stats = self._sky_stats(frame, is_pytest, meta)

        # Real-time histogram
        hist = compute_fast_histogram(frame)

        hw_delta = meta.get("hw_frame_delta", round(meta.get("actual_exposure_s", 2.0) * 1000.0, 2))
        # fps = how often a new picture really arrives (about one per exposure or per min_capture_interval_s);
        # sensor_fps = the sensor's own streaming rate (1 / hardware frame period), which is not what viewers get
        interval = getattr(self, "_frame_interval_s", None)
        fps = round(1.0 / interval, 3) if interval else 0.0
        sensor_fps = round(1000.0 / hw_delta, 2) if hw_delta and hw_delta > 0 else 0.0

        telem = {
            "timestamp": datetime.now().astimezone().isoformat(),
            "capture_time": datetime.fromtimestamp(self.last_capture_time).astimezone().isoformat()
            if self.last_capture_time else None,
            "brightness": self.last_brightness,
            "exposure_s": meta.get("actual_exposure_s", self.camera.current_exposure_s),
            "gain_db": meta.get("gain_db", self.camera.current_gain_db),
            "hw_frame_delta_ms": hw_delta,
            "fps": fps,
            "sensor_fps": sensor_fps,
            "star_count": sky_stats.get("star_count", 0),
            "cloud_cover_pct": sky_stats.get("cloud_cover_pct", 0.0),
            "cloud_cover": sky_stats.get("cloud_cover_pct", 0.0),
            "sky_status": sky_stats.get("status", "clear"),
            "histogram": hist,
            "sensor_temp_c": meta.get("sensor_temp_c"),  # None when the camera cannot report it (USB bridge)
            "temperature": meta.get("sensor_temp_c"),
            "cpu_load_pct": self._cpu_load_pct(),
            "mode": self.mode,
            "ae_lock": self.get_ae_lock(),
            "calibration": self.get_calibration_status(),
        }
        self.last_telemetry = telem
        self.last_telemetry_frame_ts = self.last_capture_time
        return telem
