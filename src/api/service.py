"""
AllSky Observatory State and Camera Service Manager.
Provides thread-safe camera hardware abstraction, analytics integration,
and observational product management for the Edge Web API.
"""

import logging
import os
import sys
import time
import threading
from collections import OrderedDict
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
import numpy as np
import cv2

from src.hardware.driver import get_camera_driver, CameraDriverInterface, describe_camera, camera_limits
from src.hardware.v4l2_mmap import configure_white_balance, set_raw10_bin
from src.calibration.auto_exposure import DayNightAutoExposure
from src.calibration.dark import DarkCalibrationEngine
from src.analytics.cloud_cover import CloudReference, analyze_cloud_cover
from src.analytics.cloud_history import CloudHistory
from src.analytics.sky_events import SkyEventDetector

logger = logging.getLogger("imx335.api")
from src.analytics.focus_tracker import FocusTracker, TempCompensation
from src.analytics.focus import FocusSession
from src.pipeline.keogram import KeogramGenerator
from src.pipeline.startrails import StartrailsAccumulator, StartrailsFrameGate
from src.pipeline.hot_pixels import StaticPointTracker
from src.storage.retention import RetentionManager
from src.activity import get_activity_log

from src.api.service_consts import ARCHIVE_WIDTH, PAST_PEAK_D  # noqa: F401 (re-exported)
from src.api.calibration_mixin import CalibrationMixin
from src.api.products_mixin import ProductsMixin
from src.api.events_mixin import EventsMixin
from src.api.traffic_mixin import TrafficMixin
from src.api.station_mixin import StationMixin
from src.api.focus_mixin import FocusMixin
from src.api.lens_mixin import LensMixin
from src.api.plate_solve_mixin import PlateSolveMixin
from src.api.camera_select_mixin import CameraSelectMixin
from src.api.capture_mixin import CaptureMixin
from src.api.status_mixin import StatusMixin
from src.api.exposure_mixin import ExposureMixin
from src.api.lifecycle_mixin import LifecycleMixin
from src.community.manager import CommunityManager
from src.auth.manager import AuthManager
from src.defaults import DEFAULT_LAT, DEFAULT_LON



class _BoundedCache(OrderedDict):
    """Least-recently-used dict: a full-res master dark is ~10-15 MB, and a night that walks through
    every exposure profile would otherwise keep the whole library (70+ MB) resident."""

    def __init__(self, max_items: int):
        super().__init__()
        self.max_items = max_items

    def get(self, key, default=None):
        if key in self:
            self.move_to_end(key)
            return self[key]
        return default

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self.move_to_end(key)
        while len(self) > self.max_items:
            self.popitem(last=False)


class AllSkyService(CalibrationMixin, ProductsMixin, EventsMixin, TrafficMixin, StationMixin, FocusMixin, LensMixin, PlateSolveMixin, CameraSelectMixin,
                  CaptureMixin, StatusMixin, ExposureMixin, LifecycleMixin):
    """
    Singleton service managing camera capture lifecycle, telemetry cache,
    calibration routines, and streaming buffers.
    """

    _instance: Optional["AllSkyService"] = None
    _lock = threading.Lock()

    STANDARD_DARK_PROFILES: List[Dict[str, Any]] = [
        {"id": "twilight_0.5s_12dB", "label": "Twilight Short", "exposure_s": 0.5, "gain_db": 12.0, "num_frames": 5},
        {"id": "nautical_1.0s_24dB", "label": "Nautical Twilight", "exposure_s": 1.0, "gain_db": 24.0, "num_frames": 5},
        {"id": "night_1.0s_30dB", "label": "Early Night", "exposure_s": 1.0, "gain_db": 30.0, "num_frames": 5},
        {"id": "night_2.0s_36dB", "label": "Standard Night", "exposure_s": 2.0, "gain_db": 36.0, "num_frames": 7},
        {"id": "deep_night_4.0s_36dB", "label": "Deep Night", "exposure_s": 4.0, "gain_db": 36.0, "num_frames": 5},
        {"id": "astro_8.0s_48dB", "label": "High-Sensitivity Night", "exposure_s": 8.0, "gain_db": 48.0, "num_frames": 5},
        {"id": "ultra_astro_15.0s_48dB", "label": "Ultra-Deep Night", "exposure_s": 15.0, "gain_db": 48.0, "num_frames": 5},
    ]

    # Selectable output resolutions (4:3, downscaled from the native 2592x1944 sensor frame)
    RESOLUTION_PRESETS: List[Dict[str, Any]] = [
        {"width": 2592, "height": 1944, "label": "2592 × 1944 (5.0 MP, native sensor)"},
        {"width": 2304, "height": 1728, "label": "2304 × 1728 (4.0 MP)"},
        {"width": 2048, "height": 1536, "label": "2048 × 1536 (3.1 MP)"},
        {"width": 1920, "height": 1440, "label": "1920 × 1440 (2.8 MP)"},
        {"width": 1600, "height": 1200, "label": "1600 × 1200 (1.9 MP)"},
        {"width": 1296, "height": 972, "label": "1296 × 972 (1.3 MP, bin 2×2)"},
        {"width": 1024, "height": 768, "label": "1024 × 768 (0.8 MP)"},
        {"width": 648, "height": 486, "label": "648 × 486 (0.3 MP, bin 4×4)"},
    ]

    # User-tunable CPU/quality trade-offs (Settings → Performance), persisted in station_config["performance"]
    PERFORMANCE_DEFAULTS: Dict[str, float] = {
        "min_capture_interval_s": 2.0,  # floor between real captures while viewers poll
        "analysis_max_width": 960,      # star/cloud analysis runs on a frame downscaled to this width
        "sky_analysis_enabled": 1,      # star count / cloud cover sampling on (1) or off (0)
        "analysis_interval_s": 300.0,   # star/cloud sampling every 5 min (day and night); frames in between reuse it
        "stream_max_width": 960,        # live WebSocket JPEG width
        "stream_jpeg_quality": 75,      # live WebSocket JPEG quality
        # Capture with nobody watching: 0 = back to back (meteor hunting), N = one frame every N seconds
        "background_capture_interval_s": 0.0,
    }
    PERFORMANCE_LIMITS: Dict[str, Tuple[float, float]] = {
        "min_capture_interval_s": (0.4, 60.0),
        "analysis_max_width": (320, 2592),
        "sky_analysis_enabled": (0, 1),
        "analysis_interval_s": (10.0, 3600.0),
        "stream_max_width": (320, 2592),
        "stream_jpeg_quality": (40, 95),
        "background_capture_interval_s": (0.0, 3600.0),
    }

    # OBS Studio and live stream celestial overlay defaults
    DEFAULT_OBS_OVERLAY: Dict[str, Any] = {
        "sky_overlay": False,
        "sky_labels": False,
        "grid_overlay": False,
        "sun_moon": True,
        "badge": True,
        "adsb_overlay": False,
        "events_overlay": False,
        "fit": "cover",
        "bg": "black",
        "night_vision": False,
        "opacity": 1.0,
        "zoom": 1.0,
        "pan_x": 0.0,
        "pan_y": 0.0,
        "rotate": 0.0,
        "flip_h": False,
        "flip_v": False,
        "brightness": 1.0,
        "contrast": 1.0,
        "title_text": "",
        "title_sub": "",
        "title_pos": "bottom-left",
        "view_mode": "live",
        "replay_url": "",
        "replay_crop_url": "",
        "replay_label": "",
        "replay_bbox": [],
    }

    def __init__(self, camera: Optional[CameraDriverInterface] = None):
        self.start_time = time.time()
        self.activity = get_activity_log()
        self._camera_failing = False
        self._last_profile: Optional[str] = None
        self._plate_solve_lock = threading.Lock()
        self.plate_solve_status: Dict[str, Any] = {"state": "idle"}
        self._timelapse_failing = False
        self._night_check_ts = 0.0
        self.lock = threading.RLock()

        # Initialize camera driver (uses real RTS5869 hardware if attached, else simulator)
        if camera is not None:
            self.camera = camera
        else:
            prefer_sim = (
                bool(os.environ.get("PYTEST_CURRENT_TEST"))
                or os.environ.get("ALLSKY_SIMULATOR", "0") == "1"
            )
            self.camera = get_camera_driver(prefer_simulator=prefer_sim)
            if not getattr(self.camera, "is_open", False):
                self.camera.open()

        self.mode = "auto"  # Default to 'auto' (Day/Night auto-exposure & auto-gain)
        self.auto_exposure = DayNightAutoExposure(
            latitude_deg=DEFAULT_LAT,
            longitude_deg=DEFAULT_LON,
            initial_exposure_s=getattr(self.camera, "current_exposure_s", 0.5),
            initial_gain_db=getattr(self.camera, "current_gain_db", 12.0),
        )

        # Caches
        self.last_frame: Optional[np.ndarray] = None
        self.last_metadata: Dict[str, Any] = {}
        self.last_telemetry: Optional[Dict[str, Any]] = None
        self.last_telemetry_frame_ts: float = -1.0
        self.last_jpeg: Optional[bytes] = None
        self.last_webp: Optional[bytes] = None
        # Viewer copy of last_frame (star boost applied at night); analysis always reads last_frame
        self.display_frame: Optional[np.ndarray] = None
        self.last_brightness: Optional[float] = None  # mean 0-255 of the latest frame (shown by the web overlay)
        self._display_frame_ts: float = -1.0
        self._star_mask_cache: Tuple[Any, Optional[np.ndarray]] = (None, None)
        self._webp_frame_ts: float = -1.0
        self._last_live_webp_write_ts: float = 0.0
        self._performance_overrides: Dict[str, float] = {}
        self.last_capture_time = 0.0
        # Focus assistant: while active, frames stay at native resolution and are neither archived nor
        # fed to auto-exposure; it ends by itself when no focus page has polled for FOCUS_IDLE_S
        self.focus = FocusSession()
        self._focus_lock = threading.Lock()
        self.focus_tracker = FocusTracker()
        self.lens_comp = TempCompensation()
        # Two profiles' worth: a master dark plus its hot-pixel map each
        self._dark_cache: Dict[str, np.ndarray] = _BoundedCache(max_items=4)

        # Calibration engine & live status tracking
        self.dark_engine = DarkCalibrationEngine()
        self.output_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
        self.calibration_dir = os.path.join(self.output_dir, "calibration", "darks")
        self.hot_pixels = StaticPointTracker(os.path.join(self.output_dir, "calibration", "auto_hot_pixels.json"))
        self.live_dir = os.path.join(self.output_dir, "live")
        self.uploads_dir = os.path.join(self.output_dir, "uploads", "allsky")
        self.keograms_dir = os.path.join(self.uploads_dir, "keograms")
        self.startrails_dir = os.path.join(self.uploads_dir, "startrails")
        self.timelapses_dir = os.path.join(self.uploads_dir, "timelapses")
        self.frames_archive_root = os.path.join(self.output_dir, "frames")
        for d in (
            self.calibration_dir,
            self.live_dir,
            self.keograms_dir,
            self.startrails_dir,
            self.timelapses_dir,
            self.frames_archive_root,
        ):
            os.makedirs(d, exist_ok=True)
        self.station_config_path = os.path.join(self.output_dir, "station_config.json")
        self.station_config: Dict[str, Any] = self._load_station_config()
        wb = self.station_config.get("white_balance") or {}
        configure_white_balance(wb.get("mode", "auto"), wb.get("r_gain"), wb.get("b_gain"))
        self.auto_exposure.lat = float(self.station_config.get("latitude_deg", DEFAULT_LAT))
        self.auto_exposure.lon = float(self.station_config.get("longitude_deg", DEFAULT_LON))
        self.auto_exposure.elevation = float(self.station_config.get("elevation_m", 20.0))
        self._sync_camera_identity()
        self._update_raw10_bin()

        # Cloud cover: learned clear-sky reference + time series (tests keep theirs out of data/). Checking for the
        # pytest module, not PYTEST_CURRENT_TEST: test modules may build the app at import, before that is set.
        if os.environ.get("PYTEST_CURRENT_TEST") or "pytest" in sys.modules:
            import tempfile

            _tmp = tempfile.mkdtemp(prefix="allsky-test-")
            self.cloud_reference = CloudReference(None)
            self.cloud_history = CloudHistory(os.path.join(_tmp, "cloud_history.jsonl"))
            self.events_dir = os.path.join(_tmp, "events")
        else:
            self.cloud_reference = CloudReference(os.path.join(self.output_dir, "calibration", "cloud_reference.json"))
            self.cloud_history = CloudHistory(os.path.join(self.output_dir, "logs", "cloud_history.jsonl"))
            self.events_dir = os.path.join(self.uploads_dir, "events")

        # Sky events (meteors, aircraft, ...): streaks are looked for at night on every new frame,
        # cloud changes come from the cloud cover samples; stored per local day under events_dir
        self.sky_events = SkyEventDetector()
        self._events_lock = threading.Lock()
        self._events_cache: Optional[List[Dict[str, Any]]] = None  # newest first, loaded on first use
        self._events_night = False
        self._events_sun_ts = 0.0
        self._event_seq = 0
        self._cloud_state: Optional[str] = None
        self.adsb = None  # ADS-B feed naming the flight behind aircraft events (start_adsb)
        self.adsb_public = None  # public-network fallback feed, only while adsb.public_fallback is on (start_adsb)
        self.adsb_contrib = None  # contributes the receiver's aircraft to the Zenivo network, only while adsb.contribute is on

        # Observational products pipeline accumulators (Keogram, Startrails, Timelapse)
        self.keogram_gen = KeogramGenerator(slice_width=3)
        self.startrails_acc = StartrailsAccumulator(comet_mode=False)
        self.startrails_gate = StartrailsFrameGate()  # keeps day/flash/bumped frames out of the stack
        self._last_archive_ts: float = 0.0
        # A restart must not trigger an immediate full timelapse re-encode (tens of CPU-seconds)
        _tl_latest = os.path.join(self.timelapses_dir, "latest.mp4")
        self._last_timelapse_compile_ts: float = os.path.getmtime(_tl_latest) if os.path.isfile(_tl_latest) else 0.0
        self._timelapse_compiling: bool = False
        self._timelapse_lock = threading.Lock()
        self._archived_date_str: str = self._obs_day_str()

        self.calibration_status: Dict[str, Any] = {
            "state": "idle",
            "current_frame": 0,
            "total_frames": 0,
            "current_profile": 0,
            "total_profiles": len(self.STANDARD_DARK_PROFILES),
            "progress_pct": 0.0,
            "exposure_s": 2.0,
            "gain_db": 36.0,
            "message": "Checking Master Dark Calibration Library...",
            "last_result": None,
            "updated_at": datetime.now().astimezone().isoformat(),
        }
        self._load_existing_calibration_summary()

        # Disk retention: age + size based cleanup of archived frames and dated products
        storage_cfg = {k: v for k, v in (self.station_config.get("storage") or {}).items() if k != "extra_dirs"}
        self.retention = RetentionManager(self.output_dir, storage_cfg)
        self._retention_stop = threading.Event()
        # Opt-in station sharing (src/community); creates no file and starts no thread until enabled in Settings
        self.community = CommunityManager(self.output_dir, self._community_station, lambda: self.last_jpeg,
                                          self.get_snapshot_jpeg)
        # Single-admin login (src/auth): until a password exists everything that changes or leaks stays locked
        self.auth = AuthManager(self.output_dir)
        self.auth.ensure_setup_code()
        if not os.environ.get("PYTEST_CURRENT_TEST"):
            threading.Thread(target=self._retention_worker, name="retention", daemon=True).start()
            threading.Thread(target=self._background_capture_worker, name="bg-capture", daemon=True).start()
            threading.Thread(target=self._traffic_worker, name="traffic-log", daemon=True).start()
            self.community.start()  # no-op while Community is off

        # Initial capture to prime caches
        self._prime_cache()
        if not os.environ.get("PYTEST_CURRENT_TEST"):
            info = describe_camera(self.camera)
            lvl = "warning" if info.get("backend") == "simulator" else "success"
            self.activity.log(
                lvl, "camera",
                f"Camera ready: {info.get('sensor')} via {info.get('backend')} at "
                f"{self.camera.width}x{self.camera.height}"
                + (" (SIMULATOR - no real camera found)" if lvl == "warning" else ""),
                limits=camera_limits(self.camera),
            )

        if not bool(os.environ.get("PYTEST_CURRENT_TEST")):
            threading.Thread(target=self._restore_today_products, daemon=True).start()

    @classmethod
    def get_instance(cls, camera: Optional[CameraDriverInterface] = None) -> "AllSkyService":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(camera=camera)
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton for clean testing isolation."""
        set_raw10_bin(1)
        with cls._lock:
            if cls._instance is not None:
                try:
                    cls._instance.close()
                except Exception:
                    pass
                cls._instance = None

    def _prime_cache(self) -> None:
        """Perform initial frame capture to populate telemetry and JPEG cache."""
        try:
            frame, meta = self.camera.capture_frame()
            bayer10 = meta.pop("bayer10", None)
            frame = self._apply_output_resolution(self._apply_image_transform(frame, self._is_isp_frame(bayer10)))
            self.last_frame = frame
            self.last_metadata = meta
            self.last_capture_time = time.time()

            ret, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if ret:
                self.last_jpeg = jpeg.tobytes()
        except Exception:
            # Fallback synthetic frame if capture fails during boot
            h = getattr(self.camera, "height", 480)
            w = getattr(self.camera, "width", 640)
            dummy = np.zeros((h, w, 3), dtype=np.uint8)
            dummy[:, :, :] = 15
            self.last_frame = dummy
            ret, jpeg = cv2.imencode(".jpg", dummy, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if ret:
                self.last_jpeg = jpeg.tobytes()
            self.last_metadata = {
                "actual_exposure_s": getattr(self.camera, "current_exposure_s", 2.0),
                "gain_db": getattr(self.camera, "current_gain_db", 24.0),
                "hw_frame_delta": 2000.0,
                "VMAX": 24277,
                "HMAX": 612,
                "SHR0": 12,
                "PGC": 120,
                "timestamp": time.time(),
                "simulator": True,
            }




    def _sky_stats(self, frame: np.ndarray, is_pytest: bool, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Star count / cloud cover, at most once per analysis_interval_s; each sample goes to the cloud history."""
        now = time.time()
        cached = getattr(self, "_sky_stats_cache", None)
        last_cc = float((cached or {}).get("cloud_cover_pct", 0.0))
        if not int(self.performance.get("sky_analysis_enabled", 1)):
            return {"star_count": 0, "cloud_cover_pct": last_cc, "status": "disabled"}
        if not is_pytest and cached is not None and now - cached["_ts"] < float(self.performance.get("analysis_interval_s", 300.0)):
            return cached
        sun_elev = -30.0
        if not is_pytest:
            from src.calibration.auto_exposure import calculate_solar_elevation

            sun_elev = float(calculate_solar_elevation(self.auto_exposure.lat, self.auto_exposure.lon))
        meta = meta or {}
        exposure_s = float(meta.get("actual_exposure_s", getattr(self.camera, "current_exposure_s", 1.0)))
        gain_db = float(meta.get("gain_db", getattr(self.camera, "current_gain_db", 0.0)))
        try:
            # Analyse a downscaled copy: 2x2 binning is ~4x cheaper (415 -> 110 ms on a Pi 4) with nearly the same star count
            h, w = frame.shape[:2]
            aw = int(self.performance["analysis_max_width"])
            analysis_frame = cv2.resize(frame, (aw, max(1, round(h * aw / w))), interpolation=cv2.INTER_AREA) if w > aw else frame
            stats = analyze_cloud_cover(
                analysis_frame, exposure_s, gain_db, sun_elev, roi=self.sky_roi, reference=self.cloud_reference,
                sky_polygons=self.station_config.get("sky_mask"), star_ref=self._star_ref(),
            )
        except Exception:
            stats = {"star_count": 0, "cloud_cover_pct": None, "status": "unknown", "method": "", "confidence": 0.0}
        self.cloud_history.append({
            "t": int(now),
            "cc": stats.get("cloud_cover_pct"),
            "m": stats.get("method", ""),
            "conf": stats.get("confidence", 0.0),
            "stars": int(stats.get("star_count", 0)),
            "sun": round(sun_elev, 1),
        })
        if not is_pytest and stats.get("cloud_cover_pct") is not None:
            try:
                self._cloud_event(frame, float(stats["cloud_cover_pct"]), now)
            except Exception:
                pass
        # Live telemetry keeps showing the last measured value when this frame could not be judged
        if stats.get("cloud_cover_pct") is None:
            stats["cloud_cover_pct"] = last_cc
        stats["_ts"] = now
        self._sky_stats_cache = stats
        return stats


















    def public_precision(self) -> float:
        """Grid (degrees, 0 = exact) the station's own coordinates are rounded to for guests and the community card."""
        return float(self.community.settings()["public_precision_deg"])

    def _community_station(self) -> Dict[str, Any]:
        """What the community card says about this station (name, coordinates, sensor)."""
        cfg = self.station_config
        sensor = str((cfg.get("camera_sensor") or {}).get("sensor_model") or "").strip()
        return {"name": str(cfg.get("location_name") or "").strip(), "lat": cfg.get("latitude_deg", 0.0),
                "lon": cfg.get("longitude_deg", 0.0), "camera": sensor or None}








    def try_reconnect_camera(self) -> Dict[str, Any]:
        """Open the real camera with the original selection and swap it in for the simulator."""
        kwargs = dict(getattr(self, "_camera_driver_kwargs", {}) or {})
        new_cam = get_camera_driver(fallback_to_simulator=False, **kwargs)
        with self.lock:
            old = self.camera
            exp = float(getattr(old, "current_exposure_s", self.auto_exposure.exposure_s))
            gain = float(getattr(old, "current_gain_db", self.auto_exposure.gain_db))
            self.camera = new_cam
            try:
                old.close()
            except Exception:
                pass
            try:
                self.camera.set_hardware_exposure(exp, gain)
            except Exception:
                pass
            self._sync_camera_identity()
            self.last_capture_time = 0.0
        info = describe_camera(new_cam)
        self.activity.success(
            "camera", f"Real camera connected: {info.get('sensor')} via {info.get('backend')} "
            f"at {new_cam.width}x{new_cam.height} (left simulator mode)"
        )
        return info

    # ------------------------------------------------------------------
    # Sky events: meteors, aircraft, satellites, flashes, cloud changes
    # ------------------------------------------------------------------
    EVENT_DEFAULTS: Dict[str, int] = {"meteor": 1, "plane": 1, "satellite": 1, "clouds": 0, "flash": 1}
    EVENT_NIGHT_SUN_ELEV = -2.0  # streak / moving-object / flash detection below civil sunset
    EVENT_DAY_TYPES = {"plane", "flash"}  # event types that are also watched while the sun is up
    EVENT_KEEP_IN_MEMORY = 500

    # ------------------------------------------------------------------
    # ADS-B: which flight was that aircraft (SBS-1 feed of a local receiver, e.g. dump1090 / FR24 feeder)
    # ------------------------------------------------------------------
    ADSB_DEFAULT_PORT = 30003

    ADSB_FRAME_LAG_S = 0.5  # frame timestamp (capture returned) minus mid-exposure, roughly

    CALIBRATION_KEYS = ("plate_solver_endpoint", "solve_source", "solved_at", "camera_sensor", "optics",
                        "orientation", "matched_stars_count", "constellations_in_fov")

    # ------------------------------------------------------------------
    # Focus assistant
    # ------------------------------------------------------------------
    FOCUS_IDLE_S = 60.0
    # A forgotten /focus tab keeps polling, so also end focus mode this long after the last user action:
    # auto-exposure is paused meanwhile and the sky would go dark (or white) as the light changes
    FOCUS_MAX_UNATTENDED_S = 900.0
    FOCUS_FPS = 10.0  # CSI cameras otherwise run at their 5 fps CPU-saving ceiling

    REFOCUS_EVERY_H_DEFAULT = 2.0  # AF: full sweep every 2 h by default; tracking/compensation cover between
    AUTO_SWEEP_RETRY_S = 1800.0

