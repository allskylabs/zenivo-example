"""
AllSky Observatory State and Camera Service Manager.
Provides thread-safe camera hardware abstraction, analytics integration,
and observational product management for the Edge Web API.
"""

import logging
import math
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
from src.hardware.registers import calculate_exposure_registers, calculate_gain_register
from src.hardware.v4l2_mmap import configure_white_balance, set_raw10_bin
from src.calibration.auto_exposure import DayNightAutoExposure
from src.calibration.dark import DarkCalibrationEngine
from src.analytics.cloud_cover import CloudReference, analyze_cloud_cover
from src.analytics.cloud_history import CloudHistory
from src.analytics.sky_events import SkyEventDetector

logger = logging.getLogger("imx335.api")
from src.analytics.focus_tracker import FocusTracker, TempCompensation
from src.analytics.focus import FocusSession
from src.version import __version__
from src.api.histogram import compute_fast_histogram
from src.pipeline.keogram import KeogramGenerator
from src.pipeline.startrails import StartrailsAccumulator, StartrailsFrameGate
from src.pipeline.overlay import frame_brightness
from src.pipeline.hot_pixels import StaticPointTracker
from src.storage.retention import RetentionManager, merge_policy
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
from src.community.manager import CommunityManager



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


class AllSkyService(CalibrationMixin, ProductsMixin, EventsMixin, TrafficMixin, StationMixin, FocusMixin, LensMixin, PlateSolveMixin):
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
            latitude_deg=21.0285,  # Hanoi coordinates default
            longitude_deg=105.8542,
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
        self.auto_exposure.lat = float(self.station_config.get("latitude_deg", 21.0285))
        self.auto_exposure.lon = float(self.station_config.get("longitude_deg", 105.8542))
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

    def capture_frame(self, force_refresh: bool = False) -> Tuple[np.ndarray, Dict[str, Any]]:
        """
        Capture a frame from camera, caching across concurrent API/MJPEG/WebSocket reads
        so the V4L2 bus is not reopened multiple times per exposure cycle.
        """
        # Do not block live stream/telemetry readers while dark frame stack is actively capturing
        if not force_refresh and self.calibration_status.get("state") == "running" and self.last_frame is not None:
            return self.last_frame, self.last_metadata

        now = time.time()
        is_pytest = bool(os.environ.get("PYTEST_CURRENT_TEST"))
        if is_pytest or self.focus.active:
            cache_ttl = 0.05
        else:
            cache_ttl = max(self.performance["min_capture_interval_s"], getattr(self.camera, "current_exposure_s", 0.5) * 0.9)
        if not force_refresh and self.last_frame is not None and (now - self.last_capture_time) < cache_ttl:
            return self.last_frame, self.last_metadata

        with self.lock:
            now = time.time()
            if not force_refresh and self.last_frame is not None and (now - self.last_capture_time) < cache_ttl:
                return self.last_frame, self.last_metadata

            # Night (sun below -6°): capture RAW10 so the ISP's purple shadow cast never appears
            if not is_pytest and hasattr(self.camera, "set_night_mode") and now - self._night_check_ts >= 60.0:
                self._night_check_ts = now
                try:
                    from src.calibration.auto_exposure import calculate_solar_elevation
                    elev = calculate_solar_elevation(self.auto_exposure.lat, self.auto_exposure.lon)
                    self.camera.set_night_mode(elev < -6.0)
                except Exception:
                    pass

            self._sync_decode_reduce()
            try:
                frame, meta = self.camera.capture_frame()
                if self._camera_failing:
                    self._camera_failing = False
                    self.activity.success("capture", "Camera capture recovered")
            except Exception as first_exc:
                # Re-open hardware fd once before falling back to simulator
                ctx = (f"exposure {getattr(self.camera, 'current_exposure_s', '?')} s, "
                       f"gain {getattr(self.camera, 'current_gain_db', '?')} dB, "
                       f"{time.time() - self.last_capture_time:.0f} s since last good frame, "
                       f"calibration={self.calibration_status.get('state')}, focus={self.focus.active}")
                self.activity.warning("capture", f"Capture failed ({type(first_exc).__name__}: {first_exc}; {ctx}); re-opening camera")
                try:
                    self.camera.close()
                    self.camera.open()
                    frame, meta = self.camera.capture_frame()
                    self.activity.success("capture", "Camera re-opened, capture OK")
                except Exception as exc:
                    self._camera_failing = True
                    self.activity.error("capture", f"Camera re-open failed ({type(exc).__name__}: {exc}; {ctx})")
                    if (
                        self.last_frame is not None
                        and not getattr(self.camera, "simulator", False)
                        and not self.last_metadata.get("simulator", False)
                    ):
                        return self.last_frame, self.last_metadata
                    from src.hardware.simulator import SimulatedIMX335Controller
                    try:
                        self.camera.close()
                    except Exception:
                        pass
                    self.camera = SimulatedIMX335Controller(
                        width=getattr(self.camera, "width", 2592),
                        height=getattr(self.camera, "height", 1944),
                    )
                    self.activity.error("camera", "Switched to SIMULATOR because the real camera is not responding; restart the service after fixing the camera")
                    self.camera.open()
                    frame, meta = self.camera.capture_frame()

            bayer10 = meta.pop("bayer10", None)

            # Automatically apply closest Master Dark from library on long exposures (>= 0.4s)
            if not is_pytest and self.calibration_status.get("state") != "running":
                cur_exp = float(meta.get("actual_exposure_s", getattr(self.camera, "current_exposure_s", 0.2)))
                cur_gain = float(meta.get("gain_db", getattr(self.camera, "current_gain_db", 12.0)))
                if cur_exp >= 0.4:
                    frame, matched_dark = self._apply_closest_master_dark(frame, cur_exp, cur_gain, bayer10=bayer10)
                    if matched_dark:
                        meta["applied_master_dark"] = matched_dark
                    if not self.focus.active:
                        frame = self._repair_hot_pixels(frame, bayer10, now)

            # Mirror / flip / rotate after dark subtraction (darks stay in native sensor orientation)
            isp_frame = self._is_isp_frame(bayer10)
            meta["capture_path"] = "isp" if isp_frame else "raw10" if bayer10 is not None else "native"
            frame = self._apply_image_transform(frame, isp_frame)
            if not self.focus.active:
                frame = self._apply_output_resolution(frame)
            meta["image_transform"] = dict(self.station_config.get("image_transform", {}))
            meta["output_resolution"] = {"width": int(frame.shape[1]), "height": int(frame.shape[0])}

            self.last_frame = frame
            self.last_metadata = meta
            self.last_capture_time = time.time()
            self._note_real_capture(self.last_capture_time)

            # Automatic Sunrise Overexposure & CMOS Solarization Guard:
            # If left in manual night exposure after daybreak, switch to auto (note: at >=0.25s in full daylight
            # solar_elev >= 2.0, Sony IMX335 CDS reset overflow solarizes/inverts the frame so p90 drops to ~130-160)
            # An exposure lock in auto mode (the "other knob follows the sky" lock) has the same problem: by day the
            # free gain is already at 0 dB, so nothing can darken the frame and /live stays white.
            exposure_locked_auto = self.mode == "auto" and self.auto_exposure.lock_exposure_s is not None
            if not is_pytest and (self.mode == "manual" or exposure_locked_auto) and self.calibration_status.get("state") != "running":
                try:
                    cur_exp_val = float(getattr(self.camera, "current_exposure_s", 0.1))
                    if cur_exp_val >= 0.08 and frame is not None and frame.shape[0] >= 200:
                        from src.calibration.auto_exposure import calculate_solar_elevation
                        solar_elev = calculate_solar_elevation(self.auto_exposure.lat, self.auto_exposure.lon)
                        p90_sample = float(np.percentile(frame[::16, ::16], 90))
                        if (solar_elev >= -2.0 and p90_sample >= 245.0) or (solar_elev >= 2.0 and cur_exp_val >= 0.25):
                            if exposure_locked_auto:
                                self.activity.info(
                                    "camera",
                                    f"Daylight: released the {self.auto_exposure.lock_exposure_s:.4g} s exposure lock "
                                    f"(sun {solar_elev:.1f}°, frame p90 {p90_sample:.0f}) so auto exposure can darken the frame")
                            self.mode = "auto"
                            self.auto_exposure.lock_exposure_s = None
                            self.auto_exposure.lock_gain_db = None
                except Exception:
                    pass

            # If auto-exposure is active (and not calibrating), update exposure and gain dynamically
            if self.mode == "auto" and self.calibration_status.get("state") != "running" and not self.focus.active:
                try:
                    self.auto_exposure.exposure_s = float(getattr(self.camera, "current_exposure_s", self.auto_exposure.exposure_s))
                    self.auto_exposure.gain_db = float(getattr(self.camera, "current_gain_db", self.auto_exposure.gain_db))
                    self.auto_exposure.hw_min_exposure_s = getattr(self.camera, "min_exposure_s", None)
                    self.auto_exposure.hw_max_exposure_s = camera_limits(self.camera).get("max_exposure_s")
                    new_exp, new_gain, ae_telem = self.auto_exposure.process_frame_feedback(
                        frame, live_camera=not is_pytest
                    )
                    self.camera.set_hardware_exposure(new_exp, new_gain)
                    self.last_metadata["auto_exposure"] = ae_telem
                    profile = ae_telem.get("profile")
                    if profile and profile != self._last_profile and not is_pytest:
                        if self._last_profile is not None:
                            self.activity.info(
                                "capture",
                                f"Sky profile {self._last_profile} -> {profile} (sun {ae_telem.get('solar_elevation_deg')}°), "
                                f"exposure {new_exp:.4g}s, gain {new_gain:.1f} dB",
                            )
                        self._last_profile = profile
                except Exception:
                    pass

            display = self._star_boost_frame(frame, meta.get("capture_path") == "raw10") if not self.focus.active else frame
            # Time / exposure / gain / brightness reach viewers via telemetry (HTML overlay), not the pixels
            self.last_brightness = round(frame_brightness(frame), 1)
            self.display_frame, self._display_frame_ts = display, self.last_capture_time

            # Update cached snapshots after RAW10 subtraction + demosaic. A full-res WebP encode costs
            # ~250 ms of single-core CPU, so it is encoded lazily in get_snapshot_webp() and the
            # data/live/latest.webp copy (not read by the app itself) is refreshed at most every 120 s.
            self.last_webp = None
            if not is_pytest and now - self._last_live_webp_write_ts >= 120.0:
                self._last_live_webp_write_ts = now
                try:
                    with open(os.path.join(self.live_dir, "latest.webp"), "wb") as fp:
                        fp.write(self._encode_webp())
                except Exception:
                    pass

            if self.focus.active:
                # A native 5 MP encode per frame would halve the focus frame rate; snapshots encode lazily
                self.last_jpeg = None
            else:
                ret, jpeg = cv2.imencode(".jpg", display, [cv2.IMWRITE_JPEG_QUALITY, 85])
                if ret:
                    self.last_jpeg = jpeg.tobytes()

            if not is_pytest and self.calibration_status.get("state") != "running" and not self.focus.active:
                try:
                    self._maybe_archive_frame_and_update_products(frame, meta)
                except Exception:
                    pass
                try:
                    self._track_focus(frame, meta, now)
                except Exception:
                    pass
                try:
                    # The star-boosted viewer frame: aircraft dots are several times brighter there than in
                    # the raw frame (short exposures), and it is what people see
                    ev_types = self._detect_sky_events(display, meta, now)
                    if ev_types:
                        # The event frame would otherwise fall between two archive ticks: keep it
                        self._maybe_archive_frame_and_update_products(frame, meta, force=True, events=ev_types)
                except Exception:
                    pass

            return frame, meta

    def _repair_hot_pixels(self, frame: np.ndarray, bayer10: Optional[np.ndarray], now: float) -> np.ndarray:
        """Learn hot pixels from a sampled long exposure (every 90 s) and paint them out of every one."""
        tracker = self.hot_pixels
        try:
            if tracker.due(now):
                before = len(tracker.confirmed)
                # The user's sky polygons are drawn on the displayed (mirrored/flipped) image; flips undo themselves
                isp = self._is_isp_frame(bayer10)
                from src.calibration.bright_star_solver import combined_sky_mask
                # User polygons are stable; the automatic mask shrinks under passing clouds
                user = self.station_config.get("sky_mask")
                sky = combined_sky_mask(self._apply_image_transform(frame, isp), user,
                                        use_auto=not (user or {}).get("sky"))
                if sky is not None:
                    sky = self._apply_image_transform(sky.astype(np.uint8), isp).astype(bool)
                n = tracker.sample(frame, now, sky)
                if n != before and (before == 0 or abs(n - before) >= 25):
                    self.activity.info("capture", f"Hot pixels: {n} found on the open sky by staying still while "
                                                  "the stars drift; they are repaired in every long exposure")
            return tracker.repair(frame)
        except Exception as exc:
            self.activity.warning("capture", f"Hot-pixel repair skipped: {exc}")
            return frame

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

    def _note_real_capture(self, t: float) -> None:
        """Remember how often a new frame really arrives (what the viewers get): exponential average of the interval."""
        prev = getattr(self, "_last_real_capture", None)
        self._last_real_capture = t
        if prev is not None and 0.0 < t - prev < 600.0:
            ema = getattr(self, "_frame_interval_s", None)
            self._frame_interval_s = (t - prev) if ema is None else 0.8 * ema + 0.2 * (t - prev)

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

    def set_exposure(self, exposure_s: float, gain_db: Optional[float] = None) -> Dict[str, Any]:
        """Update hardware exposure duration and optional gain."""
        with self.lock:
            target_gain = gain_db if gain_db is not None else self.camera.current_gain_db
            reg_info = self.camera.set_hardware_exposure(exposure_s, target_gain)
            self.last_capture_time = 0.0
            if self.focus.active:
                self.focus.last_action = time.time()
            self._persist_camera_settings()
            self.activity.info(
                "camera",
                f"Exposure set to {reg_info.get('actual_exposure_s')}s, gain {reg_info.get('gain_db')} dB"
                f" (requested {exposure_s}s / {target_gain} dB)",
            )
            return {"success": True, "settings": reg_info}

    def set_gain(self, gain_db: float) -> Dict[str, Any]:
        """Update sensor analog/digital gain."""
        with self.lock:
            reg_info = self.camera.set_hardware_exposure(self.camera.current_exposure_s, gain_db)
            self.last_capture_time = 0.0
            self._persist_camera_settings()
            return {"success": True, "settings": reg_info}

    def set_mode(self, mode: str) -> Dict[str, Any]:
        """Toggle between Auto and Manual modes."""
        mode_clean = mode.lower().strip()
        # Plain Auto/Manual drops any exposure or gain lock: Auto computes both knobs again
        self.auto_exposure.lock_exposure_s = None
        self.auto_exposure.lock_gain_db = None
        if mode_clean in ["auto", "automatic"]:
            self.mode = "auto"
            if not bool(os.environ.get("PYTEST_CURRENT_TEST")) and self.last_frame is not None:
                try:
                    self.auto_exposure.exposure_s = float(getattr(self.camera, "current_exposure_s", self.auto_exposure.exposure_s))
                    self.auto_exposure.gain_db = float(getattr(self.camera, "current_gain_db", self.auto_exposure.gain_db))
                    self.auto_exposure.hw_min_exposure_s = getattr(self.camera, "min_exposure_s", None)
                    self.auto_exposure.hw_max_exposure_s = camera_limits(self.camera).get("max_exposure_s")
                    new_exp, new_gain, _ = self.auto_exposure.process_frame_feedback(self.last_frame, live_camera=True)
                    self.camera.set_hardware_exposure(new_exp, new_gain)
                    self.last_capture_time = 0.0
                except Exception:
                    pass
        else:
            self.mode = "manual"
        self._persist_camera_settings()
        self.activity.info("camera", f"Exposure mode set to {self.mode.upper()}")
        return {"success": True, "mode": self.mode}

    def get_ae_lock(self) -> Dict[str, Any]:
        """Which knobs are held: manual mode is both locked, auto may hold one of them."""
        if self.mode == "manual":
            return {"lock": "both", "exposure_s": float(self.camera.current_exposure_s),
                    "gain_db": float(self.camera.current_gain_db),
                    "target_brightness": self.auto_exposure.target_brightness, "brightness": self.last_brightness}
        ae = self.auto_exposure
        return {"lock": ae.lock_name(), "exposure_s": ae.lock_exposure_s, "gain_db": ae.lock_gain_db,
                "target_brightness": ae.target_brightness, "brightness": self.last_brightness}

    def set_target_brightness(self, value: Optional[float]) -> Dict[str, Any]:
        """Lock the auto-exposure target for the frame's mean brightness (0-255), or None for the built-in targets."""
        self.auto_exposure.target_brightness = None if value is None else max(5.0, min(250.0, float(value)))
        self._persist_camera_settings()
        t = self.auto_exposure.target_brightness
        self.activity.info("camera", f"Target brightness locked at {t:.0f}/255" if t is not None
                           else "Target brightness back to automatic day/night targets")
        return {"success": True, "mode": self.mode, "ae_lock": self.get_ae_lock()}

    def set_ae_lock(self, lock: str, value: Optional[float] = None,
                    exposure_s: Optional[float] = None, gain_db: Optional[float] = None) -> Dict[str, Any]:
        """Lock exposure, gain, both (= manual) or neither (= full auto); the loop adjusts what is unlocked."""
        if lock not in ("none", "exposure", "gain", "both"):
            raise ValueError("lock must be 'none', 'exposure', 'gain' or 'both'")
        if lock == "both":
            exp = exposure_s if exposure_s is not None else self.camera.current_exposure_s
            gain = gain_db if gain_db is not None else self.camera.current_gain_db
            self.set_mode("manual")
            self.set_exposure(float(exp), float(gain))
            return {"success": True, "mode": self.mode, "ae_lock": self.get_ae_lock()}
        with self.lock:
            ae = self.auto_exposure
            lim = camera_limits(self.camera)
            min_exp = float(lim.get("min_exposure_s") or 1e-6)
            max_exp = float(lim.get("max_exposure_s") or 15.0)
            max_gain = float(lim.get("max_gain_db") or 48.0)
            old_exp = max(min_exp, float(getattr(self.camera, "current_exposure_s", ae.exposure_s) or ae.exposure_s))
            old_gain = float(getattr(self.camera, "current_gain_db", ae.gain_db) or ae.gain_db)
            ae.lock_exposure_s = ae.lock_gain_db = None
            if lock == "exposure":
                v = float(value if value is not None else old_exp)
                ae.lock_exposure_s = max(min_exp, min(max_exp, v))
                ae.exposure_s = ae.lock_exposure_s
                if old_exp > 0 and ae.lock_exposure_s > 0 and abs(ae.lock_exposure_s - old_exp) / max(old_exp, 1e-6) > 0.05:
                    ev_delta_db = 20.0 * math.log10(old_exp / ae.lock_exposure_s)
                    ae.gain_db = max(0.0, min(max_gain, round(old_gain + ev_delta_db, 1)))
                if self.last_frame is not None:
                    ae.process_frame_feedback(self.last_frame)
            elif lock == "gain":
                v = float(value if value is not None else old_gain)
                ae.lock_gain_db = max(0.0, min(max_gain, v))
                ae.gain_db = ae.lock_gain_db
                if abs(ae.lock_gain_db - old_gain) > 0.5:
                    ev_scale = 10.0 ** ((old_gain - ae.lock_gain_db) / 20.0)
                    ae.exposure_s = max(min_exp, min(max_exp, old_exp * ev_scale))
                if self.last_frame is not None:
                    ae.process_frame_feedback(self.last_frame)
            else:
                if self.last_frame is not None:
                    ae.process_frame_feedback(self.last_frame)
            self.mode = "auto"
            self.camera.set_hardware_exposure(
                ae.lock_exposure_s if ae.lock_exposure_s is not None else ae.exposure_s,
                ae.lock_gain_db if ae.lock_gain_db is not None else ae.gain_db,
            )
            self.last_capture_time = 0.0
            self._persist_camera_settings()
        if lock == "none":
            self.activity.info("camera", "Auto exposure: exposure and gain both automatic")
        else:
            held = f"{ae.lock_exposure_s:.4g}s" if lock == "exposure" else f"{ae.lock_gain_db:.1f} dB"
            self.activity.info("camera", f"Auto exposure: {lock} locked at {held}, the other follows the sky")
        return {"success": True, "mode": self.mode, "ae_lock": self.get_ae_lock()}

    def _persist_camera_settings(self) -> None:
        """Remember exposure mode and manual exposure/gain so they survive a server restart."""
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return
        self.station_config["camera_settings"] = {
            "mode": self.mode,
            "exposure_s": float(getattr(self.camera, "current_exposure_s", 0.2)),
            "gain_db": float(getattr(self.camera, "current_gain_db", 12.0)),
            "lock_exposure_s": self.auto_exposure.lock_exposure_s,
            "lock_gain_db": self.auto_exposure.lock_gain_db,
            "target_brightness": self.auto_exposure.target_brightness,
        }
        self._save_station_config()

    def restore_camera_settings(self) -> Optional[Dict[str, Any]]:
        """Re-apply the last saved manual exposure/gain on startup (auto mode needs nothing restored)."""
        cs = self.station_config.get("camera_settings") or {}
        self.auto_exposure.target_brightness = cs.get("target_brightness")
        if cs.get("mode") != "manual":
            self.auto_exposure.lock_exposure_s = cs.get("lock_exposure_s")
            self.auto_exposure.lock_gain_db = cs.get("lock_gain_db")
            return None
        try:
            self.camera.set_hardware_exposure(float(cs["exposure_s"]), float(cs["gain_db"]))
            self.mode = "manual"
            self.last_capture_time = 0.0
        except Exception:
            return None
        return dict(cs)

    def get_snapshot_webp(self) -> bytes:
        """Return calibrated Light frame in WebP format (captured as RAW10, subtracted in RAW10, then encoded to WebP)."""
        self.capture_frame()
        return self._encode_webp()

    def _encode_webp(self) -> bytes:
        """Encode the display frame to WebP at most once per captured frame (lock-free, like get_status)."""
        frame, frame_ts = self.get_display_frame()
        cached, cached_ts = self.last_webp, self._webp_frame_ts
        if cached is not None and cached_ts == frame_ts:
            return cached
        ret, webp = cv2.imencode(".webp", frame, [cv2.IMWRITE_WEBP_QUALITY, 92])
        if not ret:
            raise RuntimeError("WebP compression failed")
        data = webp.tobytes()
        self.last_webp, self._webp_frame_ts = data, frame_ts
        return data

    def _community_station(self) -> Dict[str, Any]:
        """What the community card says about this station (name, coordinates, sensor)."""
        cfg = self.station_config
        sensor = str((cfg.get("camera_sensor") or {}).get("sensor_model") or "").strip()
        return {"name": str(cfg.get("location_name") or "").strip(), "lat": cfg.get("latitude_deg", 0.0),
                "lon": cfg.get("longitude_deg", 0.0), "camera": sensor or None}

    def get_snapshot_jpeg(self) -> bytes:
        """Return valid JPEG binary payload, refreshing via capture_frame() when TTL expires."""
        self.capture_frame()
        if self.last_jpeg is not None:
            return self.last_jpeg
        frame, _ = self.get_display_frame()
        ret, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ret:
            raise RuntimeError("JPEG compression failed")
        self.last_jpeg = jpeg.tobytes()
        return self.last_jpeg

    # ------------------------------------------------------------------
    # Disk retention
    # ------------------------------------------------------------------
    def _background_capture_worker(self) -> None:
        """Keep capturing with no viewer connected, so auto exposure, archiving and meteor/aircraft
        detection never stop. Viewers share the same cached frames, so this adds no extra exposures."""
        while not self._retention_stop.is_set():
            interval = float(self.performance.get("background_capture_interval_s", 0.0))
            started = time.time()
            if not self.focus.active and self.calibration_status.get("state") != "running":
                try:
                    self.capture_frame()
                except Exception:
                    pass
            if interval > 0:
                wait = interval - (time.time() - started)
            else:
                # Back to back: wake when the frame cache expires (one exposure, or min_capture_interval_s)
                ttl = max(self.performance["min_capture_interval_s"],
                          float(getattr(self.camera, "current_exposure_s", 0.5)) * 0.9)
                wait = ttl - (time.time() - self.last_capture_time)
            self._retention_stop.wait(max(0.2, wait))

    def _retention_worker(self) -> None:
        """Run cleanup shortly after start, then hourly."""
        delay = 30.0
        while not self._retention_stop.wait(delay):
            self.run_storage_cleanup()
            delay = 3600.0

    def configure_storage(self, persist: bool = True, **overrides: Any) -> Dict[str, Any]:
        """Update the retention policy (unknown keys are ignored) and optionally persist it."""
        with self.lock:
            # extra_dirs come from the command line only (never from the web API or the saved config)
            extra_dirs = overrides.pop("extra_dirs", None)
            if extra_dirs is None:
                extra_dirs = self.retention.policy.get("extra_dirs", [])
            cfg = dict(self.station_config.get("storage") or {})
            cfg.update({k: v for k, v in overrides.items() if v is not None})
            cfg["extra_dirs"] = list(extra_dirs)
            self.retention.policy = merge_policy(cfg)
            if persist:
                self.station_config["storage"] = {k: v for k, v in self.retention.policy.items() if k != "extra_dirs"}
                self._save_station_config()
        return dict(self.retention.policy)

    def get_storage_status(self) -> Dict[str, Any]:
        return self.retention.usage()

    def run_storage_cleanup(self) -> Dict[str, Any]:
        """Apply the retention policy and record the outcome in the activity log."""
        try:
            report = self.retention.run()
        except Exception as exc:
            self.activity.error("storage", f"Cleanup failed: {exc}")
            return {"success": False, "error": str(exc)}
        usage = self.retention.usage()
        disk = usage.get("disk", {})
        summary = (
            f"data {usage['data_gb']:.2f}/{self.retention.policy['max_data_gb']} GB, "
            f"{usage['frame_files']} archived frames over {len(usage['frame_days'])} day(s), "
            f"disk free {disk.get('free_gb', '?')} GB"
        )
        if report["deleted_count"]:
            self.activity.success(
                "storage", f"Cleanup removed {report['deleted_count']} item(s), freed {report['freed_mb']} MB; {summary}",
                deleted=report["deleted"][:20],
            )
        else:
            self.activity.info("storage", f"Cleanup: nothing to delete; {summary}")
        if disk and disk.get("free_gb", 1e9) < float(self.retention.policy["min_free_gb"]):
            self.activity.error(
                "storage", f"LOW DISK SPACE: only {disk['free_gb']} GB free (minimum {self.retention.policy['min_free_gb']} GB)"
            )
        return {"success": True, **report}

    # ------------------------------------------------------------------
    # Camera watchdog: leave the simulator as soon as the real camera can be opened
    # ------------------------------------------------------------------
    def start_camera_watchdog(self, driver_kwargs: Dict[str, Any], interval_s: float = 30.0) -> None:
        """While running on the fallback simulator, retry the real camera (boot-time permissions, USB re-plug)."""
        self._camera_driver_kwargs = dict(driver_kwargs)
        stop = threading.Event()
        self._camera_watchdog_stop = stop

        def _loop():
            while not stop.wait(interval_s):
                if "Simulated" not in self.camera.__class__.__name__:
                    continue
                try:
                    self.try_reconnect_camera()
                except Exception as exc:
                    self.activity.warning("camera", f"Still on SIMULATOR, real camera not available yet: {exc}")

        threading.Thread(target=_loop, name="camera-watchdog", daemon=True).start()

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

    def close(self) -> None:
        """Release camera hardware resources."""
        if self.adsb is not None:
            self.adsb.stop()
            os.makedirs(os.path.dirname(self._adsb_trails_path()), exist_ok=True)
            self.adsb.save_trails(self._adsb_trails_path())
        if getattr(self, "aircraft_info", None) is not None:
            self.aircraft_info.stop()
        if getattr(self, "community", None) is not None:
            self.community.stop()
        for name in ("_retention_stop", "_camera_watchdog_stop"):
            stop = getattr(self, name, None)
            if stop is not None:
                stop.set()
        if self.camera is not None:
            try:
                self.camera.close()
            except Exception:
                pass
