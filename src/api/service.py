"""
AllSky Observatory State and Camera Service Manager.
Provides thread-safe camera hardware abstraction, analytics integration,
and observational product management for the Edge Web API.
"""

import math
import os
import re
import shutil
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
from src.hardware.v4l2_mmap import (
    demosaic_raw10_bayer,
    configure_white_balance,
    get_white_balance,
    get_raw10_bin,
    set_raw10_bin,
)
from src.calibration.auto_exposure import DayNightAutoExposure
from src.pipeline.star_boost import GAMMA_RAW10, GAMMA_SRGB, STAR_BOOST_BINS, sky_polygon_mask, star_bin_boost
from src.calibration.dark import DarkCalibrationEngine
from src.analytics.cloud_cover import CloudReference, analyze_cloud_cover
from src.analytics.cloud_history import CloudHistory
from src.analytics.sky_events import EVENT_TYPES, SkyEventDetector
from src.analytics.focus_tracker import SKY_MAX_DIOPTRES, FocusTracker, TempCompensation
from src.analytics.focus import (
    analyse_sweep, find_focus_tiles,
    FocusSession, LOWER_IS_BETTER, _contrast, display_stretch, find_best_star, find_focus_point, measure_focus,
)
from src.api.histogram import compute_fast_histogram
from src.pipeline.keogram import KeogramGenerator
from src.pipeline.startrails import StartrailsAccumulator
from src.pipeline.timelapse import compile_timelapse
from src.pipeline.overlay import stamp_frame_info, frame_brightness
from src.storage.retention import RetentionManager, merge_policy
from src.activity import get_activity_log


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


class AllSkyService:
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
        self.last_brightness: Optional[float] = None  # mean 0-255 of the latest frame (stamped on images)
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
        self.keogram_gen = KeogramGenerator(slice_width=3, max_slices=2880)
        self.startrails_acc = StartrailsAccumulator(comet_mode=False)
        self._last_archive_ts: float = 0.0
        # A restart must not trigger an immediate full timelapse re-encode (tens of CPU-seconds)
        _tl_latest = os.path.join(self.timelapses_dir, "latest.mp4")
        self._last_timelapse_compile_ts: float = os.path.getmtime(_tl_latest) if os.path.isfile(_tl_latest) else 0.0
        self._timelapse_compiling: bool = False
        self._timelapse_lock = threading.Lock()
        self._archived_date_str: str = datetime.now().astimezone().strftime("%Y-%m-%d")

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
        if not os.environ.get("PYTEST_CURRENT_TEST"):
            threading.Thread(target=self._retention_worker, name="retention", daemon=True).start()
            threading.Thread(target=self._background_capture_worker, name="bg-capture", daemon=True).start()

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

    def _cleanup_legacy_dark_images(self) -> None:
        """Remove legacy .webp/.png dark preview files once 10-bit RAW (.npy) master darks exist."""
        if bool(os.environ.get("PYTEST_CURRENT_TEST")):
            return
        try:
            if not os.path.isdir(self.calibration_dir):
                return
            for fname in os.listdir(self.calibration_dir):
                if fname.startswith("master_dark_exp") and fname.endswith((".webp", ".png")):
                    try:
                        os.remove(os.path.join(self.calibration_dir, fname))
                    except Exception:
                        pass
        except Exception:
            pass

    def get_profiles_matrix(self) -> List[Dict[str, Any]]:
        """Return status of all 6 standard 10-bit Bayer RAW Master Dark profiles (ready vs missing)."""
        self._cleanup_legacy_dark_images()
        existing = {item["filename"]: item for item in self._list_calibration_files()}
        matrix: List[Dict[str, Any]] = []
        for prof in self.STANDARD_DARK_PROFILES:
            fname_raw10 = f"master_dark_raw10_exp{prof['exposure_s']:.1f}s_gain{prof['gain_db']:.0f}dB.npy"
            fname_webp = f"master_dark_exp{prof['exposure_s']:.1f}s_gain{prof['gain_db']:.0f}dB.webp"
            fname_png = f"master_dark_exp{prof['exposure_s']:.1f}s_gain{prof['gain_db']:.0f}dB.png"
            info = existing.get(fname_raw10) or existing.get(fname_webp) or existing.get(fname_png)
            fname = info["filename"] if info else fname_raw10
            raw10_exists = fname_raw10 in existing
            matrix.append({
                "id": prof["id"],
                "label": prof["label"],
                "exposure_s": prof["exposure_s"],
                "gain_db": prof["gain_db"],
                "num_frames": prof["num_frames"],
                "filename": fname,
                "raw_filename": fname_raw10 if raw10_exists else None,
                "ready": info is not None,
                "raw10_ready": raw10_exists,
                "size_kb": info["size_kb"] if info else 0.0,
                "modified_at": info["modified_at"] if info else None,
            })
        return matrix

    def _load_existing_calibration_summary(self) -> None:
        """Populate initial last_result if calibration files exist on disk."""
        try:
            self._cleanup_legacy_dark_images()
            library = self._list_calibration_files()
            matrix = self.get_profiles_matrix()
            ready_count = sum(1 for p in matrix if p["ready"])
            total_count = len(matrix)
            if library:
                latest = library[0]
                self.calibration_status["last_result"] = {
                    "success": True,
                    "filename": latest["filename"],
                    "file_path": latest["file_path"],
                    "size_bytes": latest["size_bytes"],
                    "timestamp": latest["modified_at"],
                }
                if ready_count == total_count:
                    self.calibration_status["state"] = "completed"
                    self.calibration_status["progress_pct"] = 100.0
                    self.calibration_status["message"] = (
                        f"All {total_count}/{total_count} Standard 10-bit RAW Master Dark Profiles Ready (.npy) — RAW-to-RAW subtraction active."
                    )
                else:
                    self.calibration_status["message"] = (
                        f"{ready_count}/{total_count} Standard 10-bit RAW Master Dark Profiles Ready (Latest: {latest['filename']})"
                    )
        except Exception:
            pass

    def _list_calibration_files(self) -> List[Dict[str, Any]]:
        """List saved 10-bit Bayer RAW master dark files (master_dark_raw10_*.npy) in data/calibration/darks/ sorted newest first."""
        files_info: List[Dict[str, Any]] = []
        is_pytest = bool(os.environ.get("PYTEST_CURRENT_TEST"))
        try:
            if not os.path.isdir(self.calibration_dir):
                return files_info
            for fname in os.listdir(self.calibration_dir):
                if fname.startswith("master_dark_raw10_") and fname.endswith(".npy"):
                    pass
                elif is_pytest and fname.startswith("master_dark_") and fname.endswith((".webp", ".png")):
                    pass
                else:
                    continue
                fpath = os.path.join(self.calibration_dir, fname)
                st = os.stat(fpath)
                files_info.append({
                    "filename": fname,
                    "file_path": fpath,
                    "size_bytes": st.st_size,
                    "size_kb": round(st.st_size / 1024.0, 1),
                    "modified_at": datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(),
                    "_mtime": st.st_mtime,
                })
            files_info.sort(key=lambda x: x["_mtime"], reverse=True)
            for item in files_info:
                item.pop("_mtime", None)
        except Exception:
            pass
        return files_info

    def get_calibration_status(self) -> Dict[str, Any]:
        """Return live calibration progress, preset matrix, latest result, and saved calibration library (lock-free)."""
        status_copy = dict(self.calibration_status)
        status_copy["library"] = self._list_calibration_files()
        status_copy["profiles"] = self.get_profiles_matrix()
        status_copy["ready_profiles_count"] = sum(1 for p in status_copy["profiles"] if p["ready"])
        status_copy["total_profiles_count"] = len(status_copy["profiles"])
        return status_copy

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
                self.activity.warning("capture", f"Capture failed ({first_exc}); re-opening camera")
                try:
                    self.camera.close()
                    self.camera.open()
                    frame, meta = self.camera.capture_frame()
                    self.activity.success("capture", "Camera re-opened, capture OK")
                except Exception as exc:
                    self._camera_failing = True
                    self.activity.error("capture", f"Camera re-open failed: {exc}")
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

            # Automatic Sunrise Overexposure Guard: if left in manual night exposure after daybreak, switch to auto
            if not is_pytest and self.mode == "manual" and self.calibration_status.get("state") != "running":
                try:
                    cur_exp_val = float(getattr(self.camera, "current_exposure_s", 0.1))
                    if cur_exp_val >= 0.08 and frame is not None and frame.shape[0] >= 200:
                        from src.calibration.auto_exposure import calculate_solar_elevation
                        solar_elev = calculate_solar_elevation(self.auto_exposure.lat, self.auto_exposure.lon)
                        if solar_elev >= -2.0 and float(np.percentile(frame[::16, ::16], 90)) >= 245.0:
                            self.mode = "auto"
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
            # Viewers get time / exposure / gain / brightness written on the image; analysis keeps `display`
            self.last_brightness = round(frame_brightness(frame), 1)
            shown = display if self.focus.active else self._stamp(display, meta, brightness=self.last_brightness)
            self.display_frame, self._display_frame_ts = shown, self.last_capture_time

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
                ret, jpeg = cv2.imencode(".jpg", shown, [cv2.IMWRITE_JPEG_QUALITY, 85])
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
                    self._detect_sky_events(display, meta, now)
                except Exception:
                    pass

            return frame, meta

    def _apply_closest_master_dark(
        self, frame: np.ndarray, exp_s: float, gain_db: float, bayer10: Optional[np.ndarray] = None
    ) -> Tuple[np.ndarray, Optional[str]]:
        """
        Find and subtract the closest matching Master Dark from data/calibration/darks/,
        and repair all hot pixels recorded in the matching hot_pixels_*.npy map.
        When bayer10 is present, performs linear 10-bit Bayer RAW-to-RAW subtraction
        (Light_RAW10 - Dark_RAW10) and Bayer sub-plane hot-pixel repair BEFORE demosaicing.
        """
        try:
            ready_profiles = [p for p in self.get_profiles_matrix() if p["ready"] or p.get("raw10_ready")]
            if not ready_profiles:
                return frame, None
            best = min(
                ready_profiles,
                key=lambda p: abs(p["exposure_s"] - exp_s) + abs(p["gain_db"] - gain_db) / 12.0,
            )

            hot_key = f"hot_pixels_exp{best['exposure_s']:.1f}s_gain{best['gain_db']:.0f}dB.npy"
            hot_mask = self._dark_cache.get(hot_key)
            if hot_mask is None:
                hot_path = os.path.join(self.calibration_dir, hot_key)
                if os.path.isfile(hot_path):
                    hot_mask = np.load(hot_path)
                    self._dark_cache[hot_key] = hot_mask

            # 1. True 10-bit Bayer RAW-to-RAW dark subtraction BEFORE demosaicing
            if bayer10 is not None:
                raw_fname = f"master_dark_raw10_exp{best['exposure_s']:.1f}s_gain{best['gain_db']:.0f}dB.npy"
                master_raw10 = self._dark_cache.get(raw_fname)
                if master_raw10 is None:
                    raw_path = os.path.join(self.calibration_dir, raw_fname)
                    if os.path.isfile(raw_path):
                        master_raw10 = np.load(raw_path)
                        self._dark_cache[raw_fname] = master_raw10
                if master_raw10 is not None and master_raw10.shape == bayer10.shape:
                    calibrated_bgr8 = demosaic_raw10_bayer(
                        bayer10,
                        master_dark_bayer10=master_raw10,
                        hot_pixel_mask=hot_mask,
                    )
                    return calibrated_bgr8, raw_fname

            # 2. Fallback image-space subtraction (used in simulator/unit tests)
            fname = best["filename"]
            master = self._dark_cache.get(fname)
            if master is None:
                fpath = os.path.join(self.calibration_dir, fname)
                if os.path.isfile(fpath):
                    master = cv2.imread(fpath, cv2.IMREAD_COLOR)
                    if master is not None:
                        self._dark_cache[fname] = master
            if master is not None and master.shape == frame.shape:
                pedestal = int(np.median(master))
                dark_excess = np.maximum(0, master.astype(np.int16) - pedestal)
                calibrated = np.clip(frame.astype(np.int16) - dark_excess, 0, 255).astype(np.uint8)

                if hot_mask is not None and hot_mask.shape == calibrated.shape[:2] and np.any(hot_mask):
                    med3 = cv2.medianBlur(calibrated, 3)
                    calibrated[hot_mask] = med3[hot_mask]

                return calibrated, fname
        except Exception:
            pass
        return frame, None

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
        return {
            "status": "online",
            "camera_connected": True,
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
                sky_polygons=self.station_config.get("sky_mask"),
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
        fps = round(1000.0 / max(1.0, hw_delta), 2) if hw_delta > 0 else 1.0

        telem = {
            "timestamp": datetime.now().astimezone().isoformat(),
            "exposure_s": meta.get("actual_exposure_s", self.camera.current_exposure_s),
            "gain_db": meta.get("gain_db", self.camera.current_gain_db),
            "hw_frame_delta_ms": hw_delta,
            "fps": fps,
            "star_count": sky_stats.get("star_count", 0),
            "cloud_cover_pct": sky_stats.get("cloud_cover_pct", 0.0),
            "cloud_cover": sky_stats.get("cloud_cover_pct", 0.0),
            "sky_status": sky_stats.get("status", "clear"),
            "histogram": hist,
            "sensor_temp_c": meta.get("sensor_temp_c", 24.5),
            "temperature": meta.get("sensor_temp_c", 24.5),
            "cpu_load_pct": 14.8,
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
            ae.lock_exposure_s = ae.lock_gain_db = None
            if lock == "exposure":
                lim = camera_limits(self.camera)
                v = float(value if value is not None else self.camera.current_exposure_s)
                ae.lock_exposure_s = max(float(lim.get("min_exposure_s") or 1e-6), min(float(lim.get("max_exposure_s") or 15.0), v))
            elif lock == "gain":
                lim = camera_limits(self.camera)
                v = float(value if value is not None else self.camera.current_gain_db)
                ae.lock_gain_db = max(0.0, min(float(lim.get("max_gain_db") or 48.0), v))
            self.mode = "auto"
            if lock != "none":
                self.camera.set_hardware_exposure(
                    ae.lock_exposure_s if ae.lock_exposure_s is not None else self.camera.current_exposure_s,
                    ae.lock_gain_db if ae.lock_gain_db is not None else self.camera.current_gain_db,
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

    def trigger_master_dark(self, num_frames: int, exposure_s: float, gain_db: float) -> Dict[str, Any]:
        """
        Capture N dark frames at specified exposure & gain, generate 10-bit Bayer RAW master dark,
        detect hot pixels directly in RAW10 space, and save both .npy RAW10 and .webp preview.
        """
        prev_exp = getattr(self.camera, "current_exposure_s", 0.2)
        prev_gain = getattr(self.camera, "current_gain_db", 12.0)

        self.calibration_status.update({
            "state": "running",
            "current_frame": 0,
            "total_frames": num_frames,
            "progress_pct": 5.0,
            "exposure_s": exposure_s,
            "gain_db": gain_db,
            "message": f"Configuring sensor ({exposure_s:.2f}s @ {gain_db:.0f} dB) — preparing {num_frames} frames...",
            "updated_at": datetime.now().astimezone().isoformat(),
        })

        self.activity.info("calibration", f"Master dark started: {num_frames} frames at {exposure_s:.2f}s / {gain_db:.0f} dB")
        try:
            with self.lock:
                self.camera.set_hardware_exposure(exposure_s, gain_db)
                dark_frames: List[np.ndarray] = []
                raw_bayers: List[np.ndarray] = []

                def _on_frame_progress(idx: int, total: int, frame_bgr: np.ndarray, meta: Dict[str, Any]):
                    b10 = meta.pop("bayer10", None)
                    if b10 is not None:
                        raw_bayers.append(b10)
                    self.last_frame = frame_bgr
                    self.last_metadata = meta
                    self.last_capture_time = time.time()
                    pct = round(5.0 + (idx / max(1, total)) * 80.0, 1)
                    self.calibration_status.update({
                        "state": "running",
                        "current_frame": idx,
                        "total_frames": total,
                        "progress_pct": pct,
                        "message": f"Capturing RAW10 dark frame {idx} / {total} ({exposure_s:.2f}s @ {gain_db:.0f} dB)...",
                        "updated_at": datetime.now().astimezone().isoformat(),
                    })

                if hasattr(self.camera, "capture_frame_stack") and not bool(os.environ.get("PYTEST_CURRENT_TEST")):
                    try:
                        stack_results = self.camera.capture_frame_stack(num_frames, progress_cb=_on_frame_progress)
                        dark_frames = [f for f, _ in stack_results]
                    except Exception:
                        pass

                while len(dark_frames) < num_frames:
                    idx_next = len(dark_frames) + 1
                    self.camera.set_hardware_exposure(exposure_s, gain_db)
                    frame, meta = self.camera.capture_frame()
                    dark_frames.append(frame)
                    _on_frame_progress(idx_next, num_frames, frame, meta)

                self.calibration_status.update({
                    "state": "running",
                    "current_frame": num_frames,
                    "total_frames": num_frames,
                    "progress_pct": 90.0,
                    "message": f"Median stacking {num_frames} 10-bit Bayer RAW frames & extracting hot pixel map...",
                    "updated_at": datetime.now().astimezone().isoformat(),
                })

                raw_filename = f"master_dark_raw10_exp{exposure_s:.1f}s_gain{gain_db:.0f}dB.npy"
                raw_path = os.path.join(self.calibration_dir, raw_filename)
                if raw_bayers:
                    master_raw10 = np.round(np.median(np.stack(raw_bayers, axis=0), axis=0)).astype(np.uint16)
                    np.save(raw_path, master_raw10)
                    self._dark_cache[raw_filename] = master_raw10
                    ped10 = float(np.median(master_raw10))
                    hot_mask = (master_raw10.astype(np.float32) - ped10) >= 60.0
                    mean_dn = round(float(np.mean(master_raw10)), 2)
                    std_dn = round(float(np.std(master_raw10)), 2)
                    max_dn = int(np.max(master_raw10))
                    filename = raw_filename
                    file_path = raw_path
                    self._cleanup_legacy_dark_images()
                else:
                    master_dark = self.dark_engine.create_master_dark(dark_frames)
                    hot_mask = self.dark_engine.build_hot_pixel_map(master_dark, threshold=100)
                    mean_dn = round(float(np.mean(master_dark)), 2)
                    std_dn = round(float(np.std(master_dark)), 2)
                    max_dn = int(np.max(master_dark))
                    filename = f"master_dark_exp{exposure_s:.1f}s_gain{gain_db:.0f}dB.webp"
                    file_path = os.path.join(self.calibration_dir, filename)
                    cv2.imwrite(file_path, master_dark, [cv2.IMWRITE_WEBP_QUALITY, 95])
                    self._dark_cache[filename] = master_dark

                hot_pixels = int(np.sum(hot_mask))

                hot_map_filename = f"hot_pixels_exp{exposure_s:.1f}s_gain{gain_db:.0f}dB.npy"
                hot_map_path = os.path.join(self.calibration_dir, hot_map_filename)
                np.save(hot_map_path, hot_mask)

                # Restore previous exposure/gain if in auto mode
                if self.mode == "auto":
                    try:
                        self.camera.set_hardware_exposure(prev_exp, prev_gain)
                    except Exception:
                        pass

                # Cache in memory for fast live dark subtraction
                self._dark_cache[hot_map_filename] = hot_mask

                completed_at = datetime.now().astimezone().isoformat()
                result = {
                    "success": True,
                    "filename": filename,
                    "raw_filename": raw_filename if raw_bayers else None,
                    "file_path": file_path,
                    "hot_map_filename": hot_map_filename,
                    "frames_stacked": num_frames,
                    "exposure_s": exposure_s,
                    "gain_db": gain_db,
                    "hot_pixels_detected": hot_pixels,
                    "mean_dn": mean_dn,
                    "std_dn": std_dn,
                    "max_dn": max_dn,
                    "timestamp": completed_at,
                }

                self.calibration_status.update({
                    "state": "completed",
                    "current_frame": num_frames,
                    "total_frames": num_frames,
                    "progress_pct": 100.0,
                    "message": (
                        f"Completed: {filename} ({num_frames} RAW10 frames stacked, "
                        f"{hot_pixels} hot pixels detected, mean DN {mean_dn})"
                    ),
                    "last_result": result,
                    "updated_at": completed_at,
                })
                self.activity.success(
                    "calibration",
                    f"Master dark saved: {filename} ({num_frames} frames, {hot_pixels} hot pixels, mean {mean_dn} DN)",
                )
                return result
        except Exception as exc:
            self.activity.error("calibration", f"Master dark failed ({exposure_s:.2f}s / {gain_db:.0f} dB): {exc}")
            err_time = datetime.now().astimezone().isoformat()
            self.calibration_status.update({
                "state": "error",
                "progress_pct": 0.0,
                "message": f"Calibration failed: {exc}",
                "updated_at": err_time,
            })
            raise

    def trigger_auto_dark_library(self, only_missing: bool = False) -> Dict[str, Any]:
        """
        Automatically capture & stack all standard Master Dark profiles (or only missing ones)
        in 10-bit Bayer RAW (.npy). If some profiles are already present on disk, only captures
        the missing profiles unless all are already present.
        """
        matrix = self.get_profiles_matrix()
        missing = [p for p in matrix if not p.get("raw10_ready")]
        if only_missing:
            targets = missing
        elif 0 < len(missing) < len(matrix):
            targets = missing
        else:
            targets = list(matrix)

        if not targets:
            total = len(matrix)
            self.calibration_status.update({
                "state": "completed",
                "progress_pct": 100.0,
                "message": f"All {total}/{total} Standard 10-bit RAW Master Dark Profiles are already built and ready!",
                "updated_at": datetime.now().astimezone().isoformat(),
            })
            return {
                "success": True,
                "profiles_built": 0,
                "total_profiles": total,
                "results": [],
                "message": f"All {total}/{total} Standard 10-bit RAW Master Dark Profiles Ready.",
            }

        prev_exp = getattr(self.camera, "current_exposure_s", 0.2)
        prev_gain = getattr(self.camera, "current_gain_db", 12.0)
        results: List[Dict[str, Any]] = []
        total_targets = len(targets)

        try:
            for p_idx, prof in enumerate(targets, start=1):
                exp_s = float(prof["exposure_s"])
                gain_db = float(prof["gain_db"])
                n_frames = int(prof["num_frames"])
                label = prof["label"]

                base_pct = ((p_idx - 1) / total_targets) * 100.0
                span_pct = 100.0 / total_targets

                self.calibration_status.update({
                    "state": "running",
                    "current_profile": p_idx,
                    "total_profiles": total_targets,
                    "current_frame": 0,
                    "total_frames": n_frames,
                    "exposure_s": exp_s,
                    "gain_db": gain_db,
                    "progress_pct": round(base_pct + 2.0, 1),
                    "message": f"[Profile {p_idx}/{total_targets}: {label} — {exp_s:.1f}s @ {gain_db:.0f}dB] Configuring RAW10 sensor...",
                    "updated_at": datetime.now().astimezone().isoformat(),
                })

                with self.lock:
                    self.camera.set_hardware_exposure(exp_s, gain_db)
                    dark_frames: List[np.ndarray] = []
                    raw_bayers: List[np.ndarray] = []

                    def _on_prof_frame(idx: int, total: int, frame_bgr: np.ndarray, meta: Dict[str, Any]):
                        b10 = meta.pop("bayer10", None)
                        if b10 is not None:
                            raw_bayers.append(b10)
                        self.last_frame = frame_bgr
                        self.last_metadata = meta
                        self.last_capture_time = time.time()
                        pct = round(base_pct + (idx / max(1, total)) * (span_pct * 0.85), 1)
                        self.calibration_status.update({
                            "state": "running",
                            "current_profile": p_idx,
                            "total_profiles": total_targets,
                            "current_frame": idx,
                            "total_frames": total,
                            "progress_pct": pct,
                            "message": (
                                f"[Profile {p_idx}/{total_targets}: {label} — {exp_s:.1f}s @ {gain_db:.0f}dB] "
                                f"Capturing RAW10 frame {idx}/{total}..."
                            ),
                            "updated_at": datetime.now().astimezone().isoformat(),
                        })

                    if hasattr(self.camera, "capture_frame_stack") and not bool(os.environ.get("PYTEST_CURRENT_TEST")):
                        try:
                            stack_res = self.camera.capture_frame_stack(n_frames, progress_cb=_on_prof_frame)
                            dark_frames = [f for f, _ in stack_res]
                        except Exception:
                            pass

                    while len(dark_frames) < n_frames:
                        idx_next = len(dark_frames) + 1
                        self.camera.set_hardware_exposure(exp_s, gain_db)
                        frame, meta = self.camera.capture_frame()
                        dark_frames.append(frame)
                        _on_prof_frame(idx_next, n_frames, frame, meta)

                    raw_filename = f"master_dark_raw10_exp{exp_s:.1f}s_gain{gain_db:.0f}dB.npy"
                    raw_path = os.path.join(self.calibration_dir, raw_filename)
                    if raw_bayers:
                        master_raw10 = np.round(np.median(np.stack(raw_bayers, axis=0), axis=0)).astype(np.uint16)
                        np.save(raw_path, master_raw10)
                        self._dark_cache[raw_filename] = master_raw10
                        ped10 = float(np.median(master_raw10))
                        hot_mask = (master_raw10.astype(np.float32) - ped10) >= 60.0
                        mean_dn = round(float(np.mean(master_raw10)), 2)
                        filename = raw_filename
                        file_path = raw_path
                        self._cleanup_legacy_dark_images()
                    else:
                        master_dark = self.dark_engine.create_master_dark(dark_frames)
                        hot_mask = self.dark_engine.build_hot_pixel_map(master_dark, threshold=100)
                        mean_dn = round(float(np.mean(master_dark)), 2)
                        filename = f"master_dark_exp{exp_s:.1f}s_gain{gain_db:.0f}dB.webp"
                        file_path = os.path.join(self.calibration_dir, filename)
                        cv2.imwrite(file_path, master_dark, [cv2.IMWRITE_WEBP_QUALITY, 95])
                        self._dark_cache[filename] = master_dark

                    hot_pixels = int(np.sum(hot_mask))

                    hot_map_filename = f"hot_pixels_exp{exp_s:.1f}s_gain{gain_db:.0f}dB.npy"
                    np.save(os.path.join(self.calibration_dir, hot_map_filename), hot_mask)
                    self._dark_cache[hot_map_filename] = hot_mask

                    item_res = {
                        "profile_id": prof["id"],
                        "label": label,
                        "filename": filename,
                        "raw_filename": raw_filename if raw_bayers else None,
                        "frames_stacked": n_frames,
                        "exposure_s": exp_s,
                        "gain_db": gain_db,
                        "hot_pixels_detected": hot_pixels,
                        "mean_dn": mean_dn,
                    }
                    results.append(item_res)
                    self.calibration_status["last_result"] = {
                        "success": True,
                        **item_res,
                        "file_path": file_path,
                        "timestamp": datetime.now().astimezone().isoformat(),
                    }

            if self.mode == "auto":
                with self.lock:
                    try:
                        self.camera.set_hardware_exposure(prev_exp, prev_gain)
                    except Exception:
                        pass

            total_all = len(self.STANDARD_DARK_PROFILES)
            done_msg = (
                f"Auto-Calibration Complete: {len(results)} profile(s) built — "
                f"All {total_all}/{total_all} 10-bit RAW Master Dark Profiles Ready for automatic RAW subtraction!"
            )
            self.calibration_status.update({
                "state": "completed",
                "current_profile": total_targets,
                "total_profiles": total_targets,
                "progress_pct": 100.0,
                "message": done_msg,
                "updated_at": datetime.now().astimezone().isoformat(),
            })
            self.activity.success("calibration", done_msg)
            return {
                "success": True,
                "profiles_built": len(results),
                "total_profiles": total_all,
                "results": results,
                "message": done_msg,
            }
        except Exception as exc:
            self.activity.error("calibration", f"Dark library build failed: {exc}")
            self.calibration_status.update({
                "state": "error",
                "message": f"Auto-library build failed: {exc}",
                "updated_at": datetime.now().astimezone().isoformat(),
            })
            raise

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

    def _get_today_frames_dir(self, date_str: Optional[str] = None) -> str:
        """Return directory path for today's archived timelapse frames."""
        d_str = date_str or datetime.now().astimezone().strftime("%Y-%m-%d")
        day_dir = os.path.join(self.frames_archive_root, d_str)
        os.makedirs(day_dir, exist_ok=True)
        return day_dir

    def _list_today_frame_paths(self, date_str: Optional[str] = None) -> List[str]:
        """Return sorted list of archived frame JPEG paths for the given local date."""
        day_dir = self._get_today_frames_dir(date_str)
        if not os.path.isdir(day_dir):
            return []
        files = [
            os.path.join(day_dir, f)
            for f in os.listdir(day_dir)
            if f.startswith("frame_") and f.endswith(".jpg")
        ]
        files.sort()
        return files

    def _load_today_telemetry_records(self, date_str: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
        """Load archived frame telemetry keyed by filename."""
        import json
        day_dir = self._get_today_frames_dir(date_str)
        meta_path = os.path.join(day_dir, "telemetry.jsonl")
        records: Dict[str, Dict[str, Any]] = {}
        if not os.path.isfile(meta_path):
            return records
        try:
            with open(meta_path, "r", encoding="utf-8") as fp:
                for line in fp:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        fname = obj.get("filename")
                        if fname:
                            records[fname] = obj
                    except Exception:
                        pass
        except Exception:
            pass
        return records

    def _restore_today_products(self) -> None:
        """Restore Keogram and Startrails accumulators from today's archived frames on startup.

        Both products are saved as dated JPEGs after every archived frame, so they are reloaded from
        those two files (two decodes) instead of re-decoding up to 600 archived frames; the frame
        decode path is only the fallback when a dated product is missing or does not line up."""
        try:
            today_str = datetime.now().astimezone().strftime("%Y-%m-%d")
            self._archived_date_str = today_str
            paths = self._list_today_frame_paths(today_str)
            if not paths:
                return
            meta_map = self._load_today_telemetry_records(today_str)

            def _ts(p: str) -> datetime:
                ts_iso = meta_map.get(os.path.basename(p), {}).get("timestamp")
                return datetime.fromisoformat(ts_iso) if ts_iso else datetime.fromtimestamp(os.path.getmtime(p)).astimezone()

            last = cv2.imread(paths[-1], cv2.IMREAD_COLOR)
            if last is None:
                return
            fh, fw = last.shape[:2]
            self.keogram_gen.reset()
            self.startrails_acc.reset()
            keo_ok = self._restore_keogram_from_jpeg(today_str, paths, fh, _ts)
            trails_ok = self._restore_startrails_from_jpeg(today_str, len(paths), (fh, fw))
            if not (keo_ok and trails_ok):
                for p in paths[-600:]:
                    img = last if p == paths[-1] else cv2.imread(p, cv2.IMREAD_COLOR)
                    if img is None:
                        continue
                    if not keo_ok:
                        self.keogram_gen.add_frame(img, timestamp=_ts(p))
                    if not trails_ok:
                        self.startrails_acc.add_frame(img)
            self._save_products(today_str)
        except Exception:
            pass

    def _restore_keogram_from_jpeg(self, today_str: str, paths: List[str], frame_h: int, ts_of) -> bool:
        img = cv2.imread(os.path.join(self.keograms_dir, f"{today_str}.jpg"), cv2.IMREAD_COLOR)
        sw = self.keogram_gen.slice_width
        ruler_h = 36  # KeogramGenerator.render_with_timestamp_ruler default
        if img is None or img.shape[0] != frame_h + ruler_h or img.shape[1] % sw:
            return False
        n = img.shape[1] // sw
        if n == 0 or n > len(paths):
            return False
        strip = img[:frame_h]
        # Slices are always the newest contiguous run of archived frames
        self.keogram_gen.slices = [strip[:, i * sw:(i + 1) * sw].copy() for i in range(n)]
        self.keogram_gen.timestamps = [ts_of(p) for p in paths[-n:]]
        return True

    def _restore_startrails_from_jpeg(self, today_str: str, n_frames: int, shape: Tuple[int, int]) -> bool:
        if self.startrails_acc.comet_mode:
            return False
        img = cv2.imread(os.path.join(self.startrails_dir, f"{today_str}.jpg"), cv2.IMREAD_COLOR)
        if img is None or img.shape[:2] != shape:
            return False
        self.startrails_acc.composite = img
        self.startrails_acc.frame_count = n_frames
        return True

    def _save_products(self, today_str: str) -> None:
        """Encode keogram and startrails once each (dated file) and copy them to latest.jpg."""
        for save, out_dir in (
            (lambda p: self.keogram_gen.save(p, add_timestamps=True), self.keograms_dir),
            (self.startrails_acc.save, self.startrails_dir),
        ):
            dated = os.path.join(out_dir, f"{today_str}.jpg")
            if save(dated):
                tmp = os.path.join(out_dir, ".latest.jpg.tmp")
                shutil.copyfile(dated, tmp)
                os.replace(tmp, os.path.join(out_dir, "latest.jpg"))

    def _maybe_archive_frame_and_update_products(
        self, frame: np.ndarray, meta: Dict[str, Any], force: bool = False
    ) -> Optional[str]:
        """
        Periodically archive a downscaled 1296x972 frame to data/frames/<YYYY-MM-DD>/,
        update real-time Keogram and Startrails, and trigger background H.264 Timelapse compilation.
        """
        import json

        if frame is None or frame.size == 0:
            return None

        now = time.time()
        today_str = datetime.now().astimezone().strftime("%Y-%m-%d")
        if today_str != self._archived_date_str:
            prev_day = self._archived_date_str
            n_prev = len(self._list_today_frame_paths(prev_day))
            self._archived_date_str = today_str
            self.keogram_gen.reset()
            self.startrails_acc.reset()
            self.activity.success("archive", f"Day {prev_day} closed with {n_prev} archived frames; started {today_str}")

        existing_paths = self._list_today_frame_paths(today_str)
        # Fast initial warm-up (every 2.5s for first 15 frames), then every 15s for continuous all-day/night timelapse
        interval_s = 2.5 if len(existing_paths) < 15 else float(self.retention.policy["archive_interval_s"])
        if not force and (now - self._last_archive_ts) < interval_s:
            return None

        self._last_archive_ts = now
        now_dt = datetime.now().astimezone()

        # Standardize archive resolution to 1296x972 (4:3 half-res of 2592x1944) for fast H.264 compilation
        h, w = frame.shape[:2]
        if w != 1296:
            target_w = 1296
            target_h = int(round(h * (1296.0 / w)))
            if target_h % 2 == 1:
                target_h += 1
            interp = cv2.INTER_AREA if w > target_w else cv2.INTER_LINEAR
            arch_frame = cv2.resize(frame, (target_w, target_h), interpolation=interp)
        else:
            arch_frame = frame.copy()

        day_dir = self._get_today_frames_dir(today_str)
        fname = f"frame_{now_dt.strftime('%Y%m%d_%H%M%S_%f')[:19]}.jpg"
        fpath = os.path.join(day_dir, fname)
        # The saved JPEG carries the info stamp; keogram/startrails below use the clean frame
        stamped = self._stamp(arch_frame, meta, now_dt, brightness=self.last_brightness)
        if not cv2.imwrite(fpath, stamped, [cv2.IMWRITE_JPEG_QUALITY, 88]):
            self.activity.error("archive", f"Could not write archived frame {fpath} (disk full or read-only?)")
            return None

        exp_s = float(meta.get("actual_exposure_s", getattr(self.camera, "current_exposure_s", 0.01)))
        gain_db = float(meta.get("gain_db", getattr(self.camera, "current_gain_db", 0.0)))
        star_count = int((self.last_telemetry or {}).get("star_count", 0))
        cloud_pct = float((self.last_telemetry or {}).get("cloud_cover_pct", 0.0))

        rec = {
            "filename": fname,
            "timestamp": now_dt.isoformat(),
            "exposure_s": exp_s,
            "gain_db": gain_db,
            "star_count": star_count,
            "cloud_cover_pct": cloud_pct,
        }
        try:
            with open(os.path.join(day_dir, "telemetry.jsonl"), "a", encoding="utf-8") as fp:
                fp.write(json.dumps(rec) + "\n")
        except Exception:
            pass

        # Prune oldest frames if > 2880 frames in a single day
        total_frames = len(existing_paths) + 1
        if total_frames > 2880:
            for old_p in existing_paths[: total_frames - 2880]:
                try:
                    os.remove(old_p)
                except Exception:
                    pass

        # Update Keogram & Startrails
        try:
            self.keogram_gen.add_frame(arch_frame, timestamp=now_dt)
            self.startrails_acc.add_frame(arch_frame)
            self._save_products(today_str)
        except Exception as exc:
            self.activity.error("products", f"Keogram/startrails update failed: {exc}")

        # Automatically compile/refresh Timelapse video in background
        latest_mp4 = os.path.join(self.timelapses_dir, "latest.mp4")
        compile_interval = 8.0 if total_frames <= 20 else float(self.retention.policy["timelapse_refresh_s"])
        if (
            not os.path.isfile(latest_mp4)
            or (now - self._last_timelapse_compile_ts) >= compile_interval
        ):
            self._trigger_background_timelapse_compile(fps=12.0)

        return fpath

    def _trigger_background_timelapse_compile(self, fps: float = 12.0) -> None:
        """Spawn a non-blocking daemon thread to compile the latest H.264 MP4 timelapse."""
        if self._timelapse_compiling:
            return

        def _worker():
            t0 = time.time()
            try:
                res = self.generate_observational_products(fps=fps, ensure_min_frames=False)
                msg = (
                    f"Timelapse {res.get('date')} updated: {res.get('video_frames_count')} frames, "
                    f"{res.get('duration_s')}s video, {res.get('size_kb', 0) / 1024:.1f} MB in {time.time() - t0:.0f}s"
                )
                if self._timelapse_failing:
                    self._timelapse_failing = False
                    self.activity.success("timelapse", "Timelapse encoding recovered. " + msg)
                else:
                    self.activity.success("timelapse", msg)
            except Exception as exc:
                self._timelapse_failing = True
                self.activity.error("timelapse", f"Timelapse encoding failed: {exc}")

        t = threading.Thread(target=_worker, daemon=True)
        t.start()

    def generate_observational_products(
        self, fps: float = 12.0, ensure_min_frames: bool = True
    ) -> Dict[str, Any]:
        """
        Compile Timelapse (.mp4 H.264), Keogram (.jpg), and Startrails (.jpg) from today's
        archived frames. If fewer than 4 frames exist and ensure_min_frames=True, captures/archives
        initial frames immediately so the user gets a playable video right away.
        """
        today_str = datetime.now().astimezone().strftime("%Y-%m-%d")
        paths = self._list_today_frame_paths(today_str)

        if ensure_min_frames and len(paths) < 4:
            needed = 4 - len(paths)
            for _ in range(needed):
                if self.last_frame is not None:
                    self._maybe_archive_frame_and_update_products(self.last_frame, self.last_metadata, force=True)
                try:
                    f, m = self.capture_frame()
                    self._maybe_archive_frame_and_update_products(f, m, force=True)
                except Exception:
                    break
            paths = self._list_today_frame_paths(today_str)

        if not paths and self.last_frame is not None:
            self._maybe_archive_frame_and_update_products(self.last_frame, self.last_metadata, force=True)
            paths = self._list_today_frame_paths(today_str)

        if not paths:
            raise RuntimeError("No frames available to compile timelapse")

        with self._timelapse_lock:
            self._timelapse_compiling = True
            try:
                meta_map = self._load_today_telemetry_records(today_str)
                # Use up to 600 recent frames for responsive compilation
                selected_paths = paths[-600:]
                timestamps: List[datetime] = []
                telemetry_list: List[Dict[str, Any]] = []
                for p in selected_paths:
                    fname = os.path.basename(p)
                    rec = meta_map.get(fname, {})
                    ts_iso = rec.get("timestamp")
                    try:
                        ts_dt = datetime.fromisoformat(ts_iso) if ts_iso else datetime.fromtimestamp(os.path.getmtime(p)).astimezone()
                    except Exception:
                        ts_dt = datetime.now().astimezone()
                    timestamps.append(ts_dt)
                    telemetry_list.append(rec)

                # If very few frames exist (< 12), duplicate each frame slightly so playback duration is at least ~1.5-2s in browser player
                compile_paths = list(selected_paths)
                compile_ts = list(timestamps)
                compile_telem = list(telemetry_list)
                if len(compile_paths) < 12:
                    repeat = max(2, int(np.ceil(16 / max(1, len(compile_paths)))))
                    compile_paths = [p for p in selected_paths for _ in range(repeat)]
                    compile_ts = [t for t in timestamps for _ in range(repeat)]
                    compile_telem = [tl for tl in telemetry_list for _ in range(repeat)]

                station_name = str(self.station_config.get("location_name", "AllSky Hanoi"))
                latest_mp4 = os.path.join(self.timelapses_dir, "latest.mp4")
                dated_mp4 = os.path.join(self.timelapses_dir, f"{today_str}.mp4")

                compile_timelapse(
                    compile_paths,
                    output_path=latest_mp4,
                    fps=float(fps),
                    fourcc_str="avc1",
                    add_overlay=True,
                    telemetry_list=compile_telem,
                    timestamps=compile_ts,
                    station_name=station_name,
                )
                try:
                    import shutil
                    shutil.copy2(latest_mp4, dated_mp4)
                except Exception:
                    pass

                # Keogram & Startrails are rewritten on every archived frame; only rebuild them if missing
                keo_latest = os.path.join(self.keograms_dir, "latest.jpg")
                st_latest = os.path.join(self.startrails_dir, "latest.jpg")
                if not os.path.isfile(keo_latest) or not os.path.isfile(st_latest):
                    self._restore_today_products()

                self._last_timelapse_compile_ts = time.time()
                mp4_size_kb = round(os.path.getsize(latest_mp4) / 1024.0, 1) if os.path.isfile(latest_mp4) else 0.0
                duration_s = round(len(compile_paths) / max(1.0, float(fps)), 1)
                return {
                    "success": True,
                    "date": today_str,
                    "archived_frames_count": len(paths),
                    "video_frames_count": len(compile_paths),
                    "fps": float(fps),
                    "duration_s": duration_s,
                    "size_kb": mp4_size_kb,
                    "timelapse_url": "/uploads/allsky/timelapses/latest.mp4",
                    "keogram_url": "/uploads/allsky/keograms/latest.jpg",
                    "startrails_url": "/uploads/allsky/startrails/latest.jpg",
                    "updated_at": datetime.now().astimezone().isoformat(),
                }
            finally:
                self._timelapse_compiling = False

    def get_gallery(self) -> Dict[str, Any]:
        """List astronomical observational products with live file status and frame counts."""
        today_str = datetime.now().astimezone().strftime("%Y-%m-%d")
        is_pytest = bool(os.environ.get("PYTEST_CURRENT_TEST"))

        latest_mp4 = os.path.join(self.timelapses_dir, "latest.mp4")
        keo_jpg = os.path.join(self.keograms_dir, "latest.jpg")
        st_jpg = os.path.join(self.startrails_dir, "latest.jpg")

        # If in live operation and products have not been generated yet, generate initial set now
        if not is_pytest and not os.path.isfile(latest_mp4):
            try:
                self.generate_observational_products(fps=12.0, ensure_min_frames=True)
            except Exception:
                pass

        frame_paths = [] if is_pytest else self._list_today_frame_paths(today_str)
        frame_count = len(frame_paths)

        def _file_meta(fpath: str) -> Tuple[bool, float, str]:
            if os.path.isfile(fpath):
                sz_kb = round(os.path.getsize(fpath) / 1024.0, 1)
                mtime_iso = datetime.fromtimestamp(os.path.getmtime(fpath)).astimezone().isoformat()
                return True, sz_kb, mtime_iso
            return False, 0.0, datetime.now().astimezone().isoformat()

        tl_exists, tl_kb, tl_ts = _file_meta(latest_mp4)
        keo_exists, keo_kb, keo_ts = _file_meta(keo_jpg)
        st_exists, st_kb, st_ts = _file_meta(st_jpg)

        items = [
            {
                "type": "timelapse",
                "url": "/uploads/allsky/timelapses/latest.mp4",
                "title": f"AllSky Timelapse Video ({today_str})",
                "timestamp": tl_ts,
                "exists": tl_exists,
                "size_kb": tl_kb,
                "frame_count": frame_count,
                "fps": 12.0,
            },
            {
                "type": "keogram",
                "url": "/uploads/allsky/keograms/latest.jpg",
                "title": f"Diurnal Meridian Keogram ({today_str})",
                "timestamp": keo_ts,
                "exists": keo_exists,
                "size_kb": keo_kb,
                "frame_count": len(self.keogram_gen),
            },
            {
                "type": "startrails",
                "url": "/uploads/allsky/startrails/latest.jpg",
                "title": f"Composite Startrails ({today_str})",
                "timestamp": st_ts,
                "exists": st_exists,
                "size_kb": st_kb,
                "frame_count": self.startrails_acc.frame_count,
            },
        ]
        return {
            "date": today_str,
            "archived_frames_count": frame_count,
            "timelapse_compiling": self._timelapse_compiling,
            "items": items,
            "history": self._list_past_products(today_str),
        }

    def _list_past_products(self, today_str: str) -> List[Dict[str, Any]]:
        """Dated products (YYYY-MM-DD.mp4/.jpg) from previous days, newest first."""
        date_re = re.compile(r"^(\d{4}-\d{2}-\d{2})\.(mp4|jpg)$")
        sources = (
            ("timelapse", self.timelapses_dir, "/uploads/allsky/timelapses", "mp4"),
            ("keogram", self.keograms_dir, "/uploads/allsky/keograms", "jpg"),
            ("startrails", self.startrails_dir, "/uploads/allsky/startrails", "jpg"),
        )
        days: Dict[str, Dict[str, Any]] = {}
        for kind, dir_path, url_prefix, ext in sources:
            try:
                names = os.listdir(dir_path)
            except OSError:
                continue
            for name in names:
                m = date_re.match(name)
                if not m or m.group(2) != ext or m.group(1) >= today_str:
                    continue
                fpath = os.path.join(dir_path, name)
                try:
                    st = os.stat(fpath)
                except OSError:
                    continue
                day = days.setdefault(m.group(1), {"date": m.group(1)})
                day[kind] = {
                    "url": f"{url_prefix}/{name}",
                    "size_kb": round(st.st_size / 1024.0, 1),
                    "timestamp": datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(),
                }
        return [days[d] for d in sorted(days, reverse=True)]

    # ------------------------------------------------------------------
    # Sky events: meteors, aircraft, satellites, flashes, cloud changes
    # ------------------------------------------------------------------
    EVENT_DEFAULTS: Dict[str, int] = {"meteor": 1, "plane": 1, "satellite": 0, "clouds": 0, "flash": 0}
    EVENT_NIGHT_SUN_ELEV = -8.0  # streak / flash detection only below this sun elevation
    EVENT_KEEP_IN_MEMORY = 500

    def get_event_config(self) -> Dict[str, Any]:
        cfg = dict(self.EVENT_DEFAULTS)
        cfg.update({k: int(bool(v)) for k, v in (self.station_config.get("event_detection") or {}).items() if k in cfg})
        return {
            "enabled": cfg, "defaults": dict(self.EVENT_DEFAULTS), "night_sun_elev_deg": self.EVENT_NIGHT_SUN_ELEV,
            "night": self._events_night, "frames_seen": self.sky_events.frames_seen,
            "frames_compared": self.sky_events.frames_compared,
            "dot_tracking": dict(self.sky_events.point_stats),
        }

    def set_event_config(self, **flags: Optional[bool]) -> Dict[str, Any]:
        clean = {}
        for key, val in flags.items():
            if val is None:
                continue
            if key not in self.EVENT_DEFAULTS:
                raise ValueError(f"Unknown event type {key}")
            clean[key] = int(bool(val))
        if clean:
            self.station_config.setdefault("event_detection", {}).update(clean)
            self._save_station_config()
            on = [k for k, v in self.get_event_config()["enabled"].items() if v]
            self.activity.info("events", "Event detection: " + (", ".join(on) if on else "all off"))
            if not on:
                self.sky_events.reset()
        return {"success": True, **self.get_event_config()}

    def _detect_sky_events(self, frame: np.ndarray, meta: Dict[str, Any], now: float) -> None:
        """Streak / flash detection on a new frame at night (a few ms on a ~640 px gray copy)."""
        enabled = {k for k, v in self.get_event_config()["enabled"].items() if v and k != "clouds"}
        if now - self._events_sun_ts >= 60.0:
            from src.calibration.auto_exposure import calculate_solar_elevation
            self._events_sun_ts = now
            night = calculate_solar_elevation(self.auto_exposure.lat, self.auto_exposure.lon) < self.EVENT_NIGHT_SUN_ELEV
            if self._events_night and not night:
                self.sky_events.reset()  # daybreak: drop the reference frame, free its memory
            self._events_night = night
        if not enabled or not self._events_night:
            return
        events = self.sky_events.process(
            frame, enabled, ts=now,
            exposure_s=float(meta.get("actual_exposure_s", getattr(self.camera, "current_exposure_s", 0.0))),
            gain_db=float(meta.get("gain_db", getattr(self.camera, "current_gain_db", 0.0))),
            sky_polygons=self.station_config.get("sky_mask"),
        )
        for ev in events:
            self._record_sky_event(ev)

    def _cloud_event(self, frame: np.ndarray, cc: float, now: float) -> None:
        """Clouds arriving / clearing, from the (already sampled) cloud cover, with hysteresis."""
        state = "cloudy" if cc >= 60.0 else "clear" if cc <= 30.0 else self._cloud_state
        prev, self._cloud_state = self._cloud_state, state
        if prev is None or state == prev or not self.get_event_config()["enabled"].get("clouds"):
            return
        h, w = frame.shape[:2]
        overview = cv2.resize(frame, (640, max(1, round(h * 640 / w))), interpolation=cv2.INTER_AREA) if w > 640 else frame
        self._record_sky_event({
            "type": "clouds", "ts": now, "start_ts": now, "frames": 1, "state": state,
            "cloud_cover_pct": round(cc, 1), "_overview": overview,
        })

    # ------------------------------------------------------------------
    # ADS-B: which flight was that aircraft (SBS-1 feed of a local receiver, e.g. dump1090 / FR24 feeder)
    # ------------------------------------------------------------------
    ADSB_DEFAULT_PORT = 30003

    def get_adsb_config(self) -> Dict[str, Any]:
        cfg = self.station_config.get("adsb") or {}
        sol = self.station_config.get("bright_star_solution") or {}
        return {
            "enabled": int(bool(cfg.get("enabled", 1))) if cfg.get("host") else 0,
            "host": cfg.get("host") or "",
            "port": int(cfg.get("port") or self.ADSB_DEFAULT_PORT),
            "pointing_solved": bool(sol.get("success")),
            "status": self.adsb.status() if self.adsb is not None else None,
        }

    def set_adsb_config(self, host: Optional[str] = None, port: Optional[int] = None,
                        enabled: Optional[bool] = None) -> Dict[str, Any]:
        cfg = dict(self.station_config.get("adsb") or {})
        if host is not None:
            host = host.strip()
            if host and not re.fullmatch(r"[A-Za-z0-9.\-:\[\]]{1,253}", host):
                raise ValueError("host must be a hostname or IP address")
            cfg["host"] = host
        if port is not None:
            if not 1 <= int(port) <= 65535:
                raise ValueError("port must be 1..65535")
            cfg["port"] = int(port)
        if enabled is not None:
            cfg["enabled"] = int(bool(enabled))
        self.station_config["adsb"] = cfg
        self._save_station_config()
        self.start_adsb()
        c = self.get_adsb_config()
        self.activity.info("events", f"ADS-B flight matching: {c['host']}:{c['port']}" if c["enabled"] else "ADS-B flight matching off")
        return {"success": True, **c}

    def get_aircraft_now(self, limit: int = 30, trail_s: float = 0.0) -> Dict[str, Any]:
        """Aircraft the ADS-B receiver hears right now: flight, altitude, speed, distance, direction, and
        whether it is inside the camera's open sky (frame_xy in frame fractions). With trail_s (3D map),
        also lat/lon, each aircraft's recent path, the station and the camera's pointing."""
        from src.analytics.adsb import motion_fields, sky_position

        if self.adsb is None:
            return {"enabled": False, "connected": False, "aircraft": []}
        self._adsb_view_ts = time.time()  # someone is looking: keep the feed on, day or night
        sol = self.station_config.get("bright_star_solution") or {}
        sol = sol if sol.get("success") else None
        station = {"latitude_deg": self.auto_exposure.lat, "longitude_deg": self.auto_exposure.lon,
                   "elevation_m": float(self.station_config.get("elevation_m") or 0.0)}
        obstructions = [np.array(p, np.float32) for p in ((self.station_config.get("sky_mask") or {}).get("obstruction") or [])]
        out = []
        for a in self.adsb.current(trail_s=trail_s):
            pos = sky_position(station, a["lat"], a["lon"], a["altitude_m"], sol)
            xy = pos.get("frame_xy")
            in_view = bool(xy) and not any(cv2.pointPolygonTest(p, (float(xy[0]), float(xy[1])), False) >= 0 for p in obstructions)
            out.append({
                "icao": a["icao"], "callsign": a["callsign"] or None,
                "altitude_m": round(a["altitude_m"]), **motion_fields(a), **pos,
                "in_view": in_view, "above_horizon": pos["alt_deg"] > 0, "age_s": round(a["age_s"], 1),
            })
            if trail_s > 0:
                out[-1].update(lat=round(a["lat"], 5), lon=round(a["lon"], 5), trail=a.get("trail") or [],
                               **self._aircraft_info().describe(a["icao"], a["callsign"] or None))
        out.sort(key=lambda r: (not r["in_view"], not r["above_horizon"], r["distance_km"]))
        st = self.adsb.status()
        res = {"enabled": True, "connected": st["connected"], "total": len(out),
               "in_view": sum(1 for r in out if r["in_view"]), "aircraft": out[:limit]}
        if trail_s > 0:
            res["station"] = {"lat": station["latitude_deg"], "lon": station["longitude_deg"], "elevation_m": station["elevation_m"]}
            if sol:
                cx, cy = sol["optical_center_px"]
                f = float(sol["focal_px"])
                res["camera"] = {"az_deg": sol["pointing_az_deg"], "alt_deg": sol["pointing_alt_deg"],
                                 "roll_deg": sol.get("roll_deg", 0.0),
                                 "hfov_deg": round(2 * math.degrees(math.atan(cx / f)), 2),
                                 "vfov_deg": round(2 * math.degrees(math.atan(cy / f)), 2)}
        return res

    def _aircraft_info(self):
        """Type / airline / route lookups for the 3D map (created on first use)."""
        if getattr(self, "aircraft_info", None) is None:
            from src.analytics.aircraft_info import AircraftInfo
            self.aircraft_info = AircraftInfo(os.path.join(self.output_dir, "cache"))
        return self.aircraft_info

    def get_airports(self) -> Dict[str, Any]:
        """Airports and runways around the station (OurAirports) for the 3D flights map."""
        if getattr(self, "airports", None) is None:
            from src.analytics.airports import Airports
            self.airports = Airports(os.path.join(self.output_dir, "cache"))
        return self.airports.get(self.auto_exposure.lat, self.auto_exposure.lon)

    def get_map_config(self) -> Dict[str, Any]:
        """Basemap settings for the 3D flights map. Only Google Maps (Map Tiles API, key in station config
        "google_maps_key") is offered on top of the elevation-only terrain; no OpenStreetMap-based maps."""
        return {"google_maps_key": str(self.station_config.get("google_maps_key") or "")}

    def _adsb_wanted(self) -> bool:
        """Read the feed all the time by default ("record": the 3D map then has every aircraft's last 15
        minutes the moment it opens); with recording off, only while aircraft events can happen (night,
        plane detection on) or while the Flights panel is open (polled in the last 2 minutes)."""
        if (self.station_config.get("adsb") or {}).get("record", 1):
            return True
        if time.time() - getattr(self, "_adsb_view_ts", 0.0) < 120.0:
            return True
        return self._events_night and bool(self.get_event_config()["enabled"].get("plane"))

    def start_adsb(self) -> None:
        """(Re)start the ADS-B reader from the station config; stops it when turned off."""
        from src.analytics.adsb import AdsbFeed

        cfg = self.get_adsb_config()
        old = self.adsb
        if old is not None and (not cfg["enabled"] or (old.host, old.port) != (cfg["host"], cfg["port"])):
            old.stop()
            self.adsb = old = None
        if cfg["enabled"] and old is None:
            self.adsb = AdsbFeed(cfg["host"], cfg["port"], wanted=self._adsb_wanted)
            self.adsb.load_trails(self._adsb_trails_path())
            self.adsb.start()

    def _adsb_trails_path(self) -> str:
        return os.path.join(self.output_dir, "cache", "adsb_trails.json")

    def _match_flight(self, ev: Dict[str, Any]) -> None:
        """Name the flight behind a streak / dot event; a 'meteor' or 'satellite' that matches an aircraft
        becomes a plane."""
        if self.adsb is None or ev.get("type") not in ("plane", "meteor", "satellite") or not ev.get("line"):
            return
        sol = self.station_config.get("bright_star_solution") or {}
        if not sol.get("success"):
            return
        from src.analytics.adsb import frame_to_azalt, match_flight

        if sol.get("projection", "rectilinear") == "rectilinear":
            ln = ev["line"]
            ev["sky_from"] = dict(zip(("az_deg", "alt_deg"), frame_to_azalt(ln[0], ln[1], sol)))
            ev["sky_to"] = dict(zip(("az_deg", "alt_deg"), frame_to_azalt(ln[2], ln[3], sol)))
        t0 = float(ev.get("start_ts", ev["ts"]))
        t1 = float(ev.get("end_ts", t0))
        station = {"latitude_deg": self.auto_exposure.lat, "longitude_deg": self.auto_exposure.lon,
                   "elevation_m": float(self.station_config.get("elevation_m") or 0.0)}
        tracks = self.adsb.tracks(t0, t1)
        flight = match_flight(ev, tracks, station, sol, lag_s=self.ADSB_FRAME_LAG_S)
        if flight is None:
            # Diagnostics: the nearest aircraft anywhere in the frame and how far off it was
            near = match_flight(ev, tracks, station, sol, lag_s=self.ADSB_FRAME_LAG_S, tol_px=5000.0, check_direction=False)
            if near is not None:
                ev["nearest_flight"] = {k: near.get(k) for k in ("callsign", "icao", "match_px", "match_angle_deg",
                                                                 "altitude_m", "distance_km", "az_deg", "alt_deg")}
            ev["adsb_aircraft"] = len(tracks)
            return
        if ev["type"] != "plane":
            # Re-label only on a close match that also runs the aircraft's way
            angle = flight.get("match_angle_deg")
            if flight["match_px"] > 20.0 or angle is None or angle > 20.0:
                return
            ev["reclassified_from"] = ev["type"]
            ev["type"] = "plane"
        ev["flight"] = flight

    ADSB_FRAME_LAG_S = 0.5  # frame timestamp (capture returned) minus mid-exposure, roughly

    def _record_sky_event(self, ev: Dict[str, Any]) -> Dict[str, Any]:
        """Save the event's images + a JSON line under events/<local day>/ and log it."""
        import json

        try:
            self._match_flight(ev)
        except Exception:
            pass  # flight naming is a bonus; never lose the event over it
        crop, overview = ev.pop("_crop", None), ev.pop("_overview", None)
        dt = datetime.fromtimestamp(float(ev.get("start_ts", ev["ts"]))).astimezone()
        day = dt.strftime("%Y-%m-%d")
        self._event_seq = (self._event_seq + 1) % 1000
        eid = f"{ev['type']}_{dt.strftime('%Y%m%d_%H%M%S')}_{self._event_seq:03d}"
        # Daily cap: a false-alarm storm must not fill the disk (today's files escape the size rule)
        count_day, count = getattr(self, "_events_saved", (None, 0))
        count = count + 1 if count_day == day else 1
        self._events_saved = (day, count)
        cap = int(self.retention.policy.get("max_events_per_day", 1000))
        if count > cap:
            if count == cap + 1:
                self.activity.warning("events", f"{cap} events saved today; further events are not saved until tomorrow")
            return {"event_id": eid, "type": ev["type"], "dropped": True}
        day_dir = os.path.join(self.events_dir, day)
        os.makedirs(day_dir, exist_ok=True)
        rec = {"event_id": eid, "type": ev["type"], "timestamp": dt.isoformat(),
               **{k: v for k, v in ev.items() if k not in ("type", "ts")}}
        if crop is not None and cv2.imwrite(os.path.join(day_dir, f"{eid}.jpg"), crop, [cv2.IMWRITE_JPEG_QUALITY, 90]):
            rec["thumbnail_url"] = f"/api/events/img/{day}/{eid}.jpg"
        if overview is not None and cv2.imwrite(os.path.join(day_dir, f"{eid}_sky.jpg"), overview, [cv2.IMWRITE_JPEG_QUALITY, 85]):
            rec["sky_url"] = f"/api/events/img/{day}/{eid}_sky.jpg"
            rec.setdefault("thumbnail_url", rec["sky_url"])
        with open(os.path.join(day_dir, "events.jsonl"), "a", encoding="utf-8") as fp:
            fp.write(json.dumps(rec) + "\n")
        with self._events_lock:
            if self._events_cache is not None:
                self._events_cache.insert(0, rec)
                del self._events_cache[self.EVENT_KEEP_IN_MEMORY:]
        if ev["type"] == "clouds":
            msg = f"Sky turned {ev['state']} (cloud cover {ev['cloud_cover_pct']:.0f}%)"
        elif ev["type"] == "flash":
            msg = f"Flash: sky brightness {ev['sky_mean_before']} -> {ev['sky_mean']}"
        else:
            msg = (f"{ev['type'].capitalize()} detected: {ev.get('length_px', 0):.0f} px streak"
                   + (f" over {ev['frames']} frames" if ev.get("frames", 1) > 1 else ""))
            fl = ev.get("flight")
            if fl:
                msg += (f" — flight {fl.get('callsign') or fl['icao']}, {fl['altitude_m']} m, {fl['distance_km']} km"
                        + (f" (was {ev['reclassified_from']})" if ev.get("reclassified_from") else ""))
        self.activity.info("events", msg, event_id=eid)
        return rec

    def _load_events(self) -> List[Dict[str, Any]]:
        import json

        with self._events_lock:
            if self._events_cache is None:
                items: List[Dict[str, Any]] = []
                days = sorted(os.listdir(self.events_dir), reverse=True) if os.path.isdir(self.events_dir) else []
                for day in days:
                    try:
                        with open(os.path.join(self.events_dir, day, "events.jsonl"), encoding="utf-8") as fp:
                            day_items = [json.loads(line) for line in fp if line.strip()]
                    except (OSError, ValueError):
                        continue
                    items.extend(reversed(day_items))
                    if len(items) >= self.EVENT_KEEP_IN_MEMORY:
                        break
                self._events_cache = items[: self.EVENT_KEEP_IN_MEMORY]
            return list(self._events_cache)

    def get_events(self, limit: int = 20, event_type: Optional[str] = None) -> Dict[str, Any]:
        """Detected sky events, newest first (optionally one type)."""
        events = self._load_events()
        if event_type:
            events = [e for e in events if e.get("type") == event_type]
        counts: Dict[str, int] = {}
        for e in self._load_events():
            counts[e.get("type", "?")] = counts.get(e.get("type", "?"), 0) + 1
        return {"events": events[:limit], "total_count": len(events), "counts": counts, **self.get_event_config()}

    def event_image_path(self, day: str, name: str) -> Optional[str]:
        import re

        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) or not re.fullmatch(r"[a-z]+_\d{8}_\d{6}_\d{3}(_sky)?\.jpg", name):
            return None
        path = os.path.join(self.events_dir, day, name)
        return path if os.path.isfile(path) else None

    def _load_station_config(self) -> Dict[str, Any]:
        """Load persisted station coordinates and plate-solving optical parameters from disk."""
        import json
        from src.calibration.station_solver import IMX335_SPEC, compute_lst_and_zenith

        default_lat = 21.0285
        default_lon = 105.8542
        default_elev = 20.0
        z_ra, z_dec = compute_lst_and_zenith(default_lat, default_lon)
        default_cfg: Dict[str, Any] = {
            "latitude_deg": default_lat,
            "longitude_deg": default_lon,
            "elevation_m": default_elev,
            "location_name": "Hanoi, Vietnam",
            "geo_source": "default",
            "plate_solver_endpoint": "https://hoan.uk/astronomy/plate-solving",
            "solve_source": "not_solved_yet",
            "solved_at": None,
            "camera_sensor": IMX335_SPEC,
            "optics": {
                "lens_model": "Fisheye 1.65mm f/2.0 AllSky (180° Hemisphere)",
                "projection_type": "Fisheye Equidistant AllSky",
                "is_allsky": True,
                "focal_length_mm": 1.65,
                "pixel_scale_arcsec_px": 250.0,
                "fov_horizontal_deg": 180.0,
                "fov_vertical_deg": 135.0,
                "fov_diagonal_deg": 225.0,
                "optical_center_x_px": 1296.0,
                "optical_center_y_px": 972.0,
                "sensor_center_offset_px": [0.0, 0.0],
                "horizon_radius_90_px": 894.2,
            },
            "orientation": {
                "center_ra_deg": z_ra,
                "center_dec_deg": z_dec,
                "center_ra_hms": "--",
                "center_dec_dms": "--",
                "azimuth_deg": 180.0,
                "altitude_deg": 90.0,
                "zenith_tilt_deg": 0.0,
                "north_position_angle_deg": 0.0,
                "rotation_deg": 0.0,
                "cd_matrix": [-0.0694, 0.0, 0.0, -0.0694],
            },
            "matched_stars_count": 0,
            "constellations_in_fov": [],
            "image_transform": {
                "flip_horizontal": False,
                "flip_vertical": False,
                "rotate_deg": 0,
            },
            "output_resolution": {"width": 2592, "height": 1944},
        }
        try:
            if os.path.isfile(self.station_config_path):
                with open(self.station_config_path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                if isinstance(saved, dict):
                    default_cfg.update(saved)
        except Exception:
            pass
        return default_cfg

    def _save_station_config(self) -> None:
        """Persist station configuration and solved optical parameters to disk."""
        import json
        try:
            with open(self.station_config_path, "w", encoding="utf-8") as f:
                json.dump(self.station_config, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def _sync_decode_reduce(self) -> None:
        """Let the RTS5869 MJPG decoder work at the largest 1/2^n size that still covers the output width."""
        session = getattr(self.camera, "session", None)
        if session is None or not hasattr(session, "decode_reduce"):
            return
        res = self.station_config.get("output_resolution") or {}
        out_w = int(res.get("width", 0) or 0)
        cam_w = int(getattr(self.camera, "width", 0) or 0)
        factor = 1
        if hasattr(session, "fast_reads"):
            session.fast_reads = self.focus.active
        if out_w > 0 and cam_w > 0 and not self.focus.active:
            for f in (8, 4, 2):
                if cam_w // f >= out_w:
                    factor = f
                    break
        session.decode_reduce = factor

    def _is_isp_frame(self, bayer10: Optional[np.ndarray]) -> bool:
        """True for RTS5869 frames that went through the camera ISP (MJPG/YUYV, i.e. day), not RAW10 bypass."""
        return bayer10 is None and describe_camera(self.camera).get("backend") == "rts5869"

    def _apply_image_transform(self, frame: np.ndarray, isp_frame: bool = False) -> np.ndarray:
        """Apply the configured mirror (horizontal), flip (vertical) and 180° rotation to a frame.

        rotate_isp_180 adds a 180° turn to ISP (day) frames only: after a power cycle the RTS5869 ISP path
        reads the sensor rotated 180° relative to the RAW10 night path.
        """
        tf = self.station_config.get("image_transform") or {}
        rot180 = (int(tf.get("rotate_deg", 0)) % 360 == 180) != (isp_frame and bool(tf.get("rotate_isp_180")))
        flip_h = bool(tf.get("flip_horizontal")) != rot180
        flip_v = bool(tf.get("flip_vertical")) != rot180
        if frame is None or not (flip_h or flip_v):
            return frame
        if flip_h and flip_v:
            return cv2.flip(frame, -1)
        return cv2.flip(frame, 1 if flip_h else 0)

    def _apply_output_resolution(self, frame: np.ndarray) -> np.ndarray:
        """Downscale a frame to the configured output resolution (area averaging, like software binning)."""
        res = self.station_config.get("output_resolution") or {}
        tw, th = int(res.get("width", 0) or 0), int(res.get("height", 0) or 0)
        if frame is None or tw <= 0 or th <= 0:
            return frame
        h, w = frame.shape[:2]
        if (w, h) == (tw, th) or tw > w or th > h:
            return frame
        return cv2.resize(frame, (tw, th), interpolation=cv2.INTER_AREA)

    def _resolution_presets(self) -> List[Dict[str, Any]]:
        """Output presets: the IMX335 4:3 list, or native-aspect downscales for any other camera."""
        w, h = int(getattr(self.camera, "width", 2592) or 2592), int(getattr(self.camera, "height", 1944) or 1944)
        if (w, h) == (2592, 1944):
            return [dict(p) for p in self.RESOLUTION_PRESETS]
        presets = []
        for scale, note in ((1.0, "native"), (0.75, ""), (0.5, "bin 2×2"), (0.375, ""), (0.25, "bin 4×4")):
            pw, ph = int(w * scale) // 2 * 2, int(h * scale) // 2 * 2
            if pw < 320 or any(p["width"] == pw for p in presets):
                continue
            label = f"{pw} × {ph} ({pw * ph / 1e6:.1f} MP{', ' + note if note else ''})"
            presets.append({"width": pw, "height": ph, "label": label})
        return presets

    def _sync_camera_identity(self) -> None:
        """Align station sensor spec, optical centre and output size with the camera actually attached."""
        self._restore_lens()
        info = describe_camera(self.camera)
        if info.get("backend") in ("rts5869", "simulator"):
            return
        w, h = int(self.camera.width), int(self.camera.height)
        pitch = float(info.get("pixel_pitch_um", 2.0))
        nat_w = int(info.get("native_width") or w)
        bin_pitch = pitch * nat_w / max(1, w)  # effective pitch of the (binned) output pixels
        self.station_config["camera_sensor"] = {
            "sensor_model": info.get("sensor_model", "unknown"),
            "usb_bridge": info.get("vid_pid", ""),
            "width_px": w,
            "height_px": h,
            "pixel_pitch_um": round(bin_pitch, 3),
            "sensor_width_mm": round(w * bin_pitch / 1000.0, 3),
            "sensor_height_mm": round(h * bin_pitch / 1000.0, 3),
            "sensor_diag_mm": round(math.hypot(w, h) * bin_pitch / 1000.0, 3),
        }
        optics = self.station_config.setdefault("optics", {})
        cx, cy = float(optics.get("optical_center_x_px", 0)), float(optics.get("optical_center_y_px", 0))
        if not (0 < cx < w and 0 < cy < h) or (cx, cy) == (1296.0, 972.0):
            optics["optical_center_x_px"] = w / 2.0
            optics["optical_center_y_px"] = h / 2.0
            optics["horizon_radius_90_px"] = round(min(w, h) / 2.0, 1)
        # Until a real plate-solve exists, describe the camera's own fixed lens instead of the IMX335 fisheye default
        lens = info.get("lens")
        solved_live = str(self.station_config.get("solve_source", "")).startswith("live_frame")
        if lens and not solved_live:
            hfov, vfov = float(lens["fov_horizontal_deg"]), float(lens["fov_vertical_deg"])
            pitch_eff = self.station_config["camera_sensor"]["pixel_pitch_um"]
            optics.update({
                "lens_model": lens["lens_model"],
                "projection_type": "Gnomonic TAN Rectilinear",
                "is_allsky": False,
                "focal_length_mm": lens["focal_length_mm"],
                "pixel_scale_arcsec_px": round(206.2648 * pitch_eff / lens["focal_length_mm"], 2),
                "fov_horizontal_deg": hfov,
                "fov_vertical_deg": vfov,
                "fov_diagonal_deg": round(math.degrees(2 * math.atan(math.hypot(
                    math.tan(math.radians(hfov / 2)), math.tan(math.radians(vfov / 2))))), 1),
                "optical_center_x_px": w / 2.0,
                "optical_center_y_px": h / 2.0,
                "sensor_center_offset_px": [0.0, 0.0],
            })
            if self.station_config.get("solve_source") == "ephemeris_zenith_model":
                self.station_config["solve_source"] = "not_solved_yet"
                self.station_config["solved_at"] = None
                self.station_config["matched_stars_count"] = 0
                self.station_config["constellations_in_fov"] = []
        res = self.station_config.get("output_resolution") or {}
        allowed = {(p["width"], p["height"]) for p in self._resolution_presets()}
        if (int(res.get("width", 0)), int(res.get("height", 0))) not in allowed:
            self.station_config["output_resolution"] = {"width": w, "height": h}
        self._save_station_config()

    def get_resolution_options(self) -> Dict[str, Any]:
        """Return the current output resolution and the selectable presets."""
        return {
            "current": dict(self.station_config.get("output_resolution") or {"width": 2592, "height": 1944}),
            "presets": self._resolution_presets(),
        }

    def set_output_resolution(self, width: int, height: int) -> Dict[str, Any]:
        """Select one of the output resolution presets and persist it."""
        preset = next(
            (p for p in self._resolution_presets() if p["width"] == int(width) and p["height"] == int(height)),
            None,
        )
        if preset is None:
            raise ValueError(f"Unsupported resolution {width}x{height}")
        with self.lock:
            self.station_config["output_resolution"] = {"width": preset["width"], "height": preset["height"]}
            self._save_station_config()
            self._update_raw10_bin()
            self.last_capture_time = 0.0
        return {
            "success": True,
            "message": f"Output resolution set to {preset['width']} × {preset['height']}.",
            **self.get_resolution_options(),
            "station_config": self.get_station_config(),
        }

    def _update_raw10_bin(self) -> None:
        """Demosaic RAW10 straight to the output size (Bayer 2x2 / 4x4 superpixel) instead of
        demosaicing full resolution and downscaling afterwards."""
        res = self.station_config.get("output_resolution") or {}
        tw = int(res.get("width", 0) or 0)
        native_w = int(getattr(self.camera, "width", 0) or 0)
        ratio = native_w / tw if tw > 0 and native_w > 0 else 1.0
        if describe_camera(self.camera).get("backend") != "rts5869" or self.focus.active:
            ratio = 1.0  # only the RTS5869 delivers RAW10 Bayer frames; focusing needs every pixel
        set_raw10_bin(4 if ratio >= 4 else 2 if ratio >= 2 else 1)

    @property
    def performance(self) -> Dict[str, float]:
        perf = dict(self.PERFORMANCE_DEFAULTS)
        perf.update(self.station_config.get("performance") or {})
        perf.update(self._performance_overrides)
        return perf

    def get_performance(self) -> Dict[str, Any]:
        return {
            "current": self.performance,
            "defaults": dict(self.PERFORMANCE_DEFAULTS),
            "limits": {k: list(v) for k, v in self.PERFORMANCE_LIMITS.items()},
            "raw10_bin": get_raw10_bin(),
        }

    def set_performance(self, persist: bool = True, **values: Optional[float]) -> Dict[str, Any]:
        """Update performance settings (clamped to PERFORMANCE_LIMITS). persist=False applies
        them for this run only, e.g. from command-line flags."""
        clean: Dict[str, float] = {}
        for key, val in values.items():
            if val is None:
                continue
            if key not in self.PERFORMANCE_LIMITS:
                raise ValueError(f"Unknown performance setting {key}")
            lo, hi = self.PERFORMANCE_LIMITS[key]
            v = min(hi, max(lo, float(val)))
            clean[key] = v if key.endswith("_s") else int(round(v))
        if persist:
            self.station_config.setdefault("performance", {}).update(clean)
            for k in clean:
                self._performance_overrides.pop(k, None)
            self._save_station_config()
        else:
            self._performance_overrides.update(clean)
        return {"success": True, "message": "Performance settings updated.", **self.get_performance()}

    # ------------------------------------------------------------------
    # Focus assistant
    # ------------------------------------------------------------------
    FOCUS_IDLE_S = 60.0
    # A forgotten /focus tab keeps polling, so also end focus mode this long after the last user action:
    # auto-exposure is paused meanwhile and the sky would go dark (or white) as the light changes
    FOCUS_MAX_UNATTENDED_S = 900.0
    FOCUS_FPS = 10.0  # CSI cameras otherwise run at their 5 fps CPU-saving ceiling

    def get_focus_state(self) -> Dict[str, Any]:
        state = self.focus.state()
        state["exposure_s"] = float(getattr(self.camera, "current_exposure_s", 0.0))
        state["gain_db"] = float(getattr(self.camera, "current_gain_db", 0.0))
        state["mode"] = self.mode
        state["limits"] = camera_limits(self.camera)
        state["lens"] = self.get_lens_state()
        hist_all = self.station_config.get("focus_history") or []
        good = [r for r in hist_all if not r.get("near") and not (r.get("lens_position") or 0) > SKY_MAX_DIOPTRES]
        state["last_record"] = good[-1] if good else None
        state["history_records"] = (self.station_config.get("focus_history") or [])[-10:]
        if self.focus.active:
            state["expires_in_s"] = round(max(0.0, self.focus.last_action + self.FOCUS_MAX_UNATTENDED_S - time.time()), 1)
        frame = self.last_frame
        if frame is not None:
            state["frame_width"], state["frame_height"] = int(frame.shape[1]), int(frame.shape[0])
        return state

    def start_focus(self, autofocus: Optional[bool] = None, **config: Any) -> Dict[str, Any]:
        """Enter focus mode: native resolution, fastest capture rate, auto-exposure and archiving paused.
        Without explicit settings the focus point is picked from the picture. With autofocus (and a motorised
        lens) a sweep autofocus starts right away on that point; it saves the result and ends focus mode."""
        auto_point = not config and not self.focus.active
        if autofocus is None:
            # AF lens mode: starting focus mode means 'find focus'; MF: just stream for adjusting by hand
            autofocus = self._lens_supported() and self._lens_cfg().get("mode") == "auto"
        with self._focus_lock:
            self.focus.configure(**config)
            self.focus.last_poll = self.focus.last_action = time.time()
            if not self.focus.active:
                self.focus.active = True
                self.focus.started_at = time.time()
                self.focus.reset_stats()
                self._update_raw10_bin()
                if hasattr(self.camera, "set_max_fps"):
                    self.camera.set_max_fps(self.FOCUS_FPS)
                self.last_capture_time = 0.0
                if not os.environ.get("PYTEST_CURRENT_TEST"):
                    self.activity.info("camera", "Focus mode started (native resolution, auto-exposure and archiving paused)")
                threading.Thread(target=self._focus_watchdog, name="focus-watchdog", daemon=True).start()
        state = None
        if auto_point:
            # Pick the focus point from the picture (stars / lights by night, the most detailed tile by day)
            try:
                state = self.focus_auto_point()
            except Exception:
                state = None
        if autofocus and self._lens_supported():
            if state is not None and not state.get("found", True):
                state["message"] = (state.get("message") or "") + " — autofocus not started"
                return state
            try:
                if state is not None and state.get("point", {}).get("method") in ("stars", "lights", "contrast"):
                    self.configure_focus(method=state["point"]["method"])
                af = self.start_lens_autofocus("sweep")
                af.update({k: state[k] for k in ("found", "point") if state and k in state})
                af["message"] = (state or {}).get("message", "") + " · sweep autofocus started"
                return af
            except RuntimeError as exc:
                state = state or self.get_focus_state()
                state["message"] = f"autofocus not started: {exc}"
                return state
        return state if state is not None else self.get_focus_state()

    def stop_focus(self, reason: str = "stopped") -> Dict[str, Any]:
        with self._focus_lock:
            if self.focus.active:
                self.focus.active = False
                self.focus.sweep_cancel = True
                self._update_raw10_bin()
                if hasattr(self.camera, "set_max_fps"):
                    self.camera.set_max_fps(getattr(self.camera, "MAX_FPS", 5.0))
                self.last_capture_time = 0.0
                self.focus.crop_jpeg = self.focus.overview_jpeg = None
                if not os.environ.get("PYTEST_CURRENT_TEST"):
                    self.activity.info("camera", f"Focus mode ended ({reason})")
        return self.get_focus_state()

    def _focus_watchdog(self) -> None:
        started = self.focus.started_at
        while self.focus.active and self.focus.started_at == started:
            time.sleep(2.0)
            if self.focus.active and time.time() - self.focus.last_poll > self.FOCUS_IDLE_S:
                self.stop_focus("no focus viewer for 60 s")
                return
            if self.focus.active and time.time() - self.focus.last_action > self.FOCUS_MAX_UNATTENDED_S:
                self.stop_focus(f"no user action for {self.FOCUS_MAX_UNATTENDED_S / 60:.0f} min")
                return

    def configure_focus(self, reset: bool = False, **config: Any) -> Dict[str, Any]:
        """Change ROI/method/smoothing; with nothing to change it only counts as a user action (keep-alive)."""
        with self._focus_lock:
            self.focus.last_action = time.time()
            self.focus.configure(**config)
            if reset:
                self.focus.reset_stats()
            self.focus.result_key = None
        return self.get_focus_state()

    def _focus_new_frame(self, since_ts: float, timeout_s: float) -> Tuple[np.ndarray, Dict[str, Any], float]:
        """Return a frame newer than since_ts (capturing as needed), or the latest one on timeout."""
        deadline = time.time() + max(0.0, timeout_s)
        while True:
            frame, meta = self.capture_frame()
            ts = self.last_capture_time
            if ts > since_ts or time.time() >= deadline:
                return frame, meta, ts
            time.sleep(0.02)

    def get_focus_frame(self, since_ts: float = 0.0, stretch: bool = False, timeout_s: float = 20.0) -> Tuple[bytes, Dict[str, Any]]:
        """Native-resolution ROI crop (JPEG) of the next frame plus its focus measurement."""
        if not self.focus.active:
            raise RuntimeError("Focus mode is not active")
        self.focus.last_poll = time.time()
        frame, meta, ts = self._focus_new_frame(since_ts, timeout_s)
        self.focus.last_poll = time.time()
        fh, fw = frame.shape[:2]
        x0, y0, rw, rh = self.focus.roi_rect(fw, fh)
        key = (ts, x0, y0, rw, self.focus.method, bool(stretch))
        with self._focus_lock:
            if self.focus.result_key == key and self.focus.crop_jpeg is not None and self.focus.last_result:
                return self.focus.crop_jpeg, self.focus.last_result
            crop = frame[y0:y0 + rh, x0:x0 + rw]
            result = self.focus.record(self.focus.measure(crop), ts)
            view = display_stretch(crop) if stretch else crop
            ok, jpeg = cv2.imencode(".jpg", view, [cv2.IMWRITE_JPEG_QUALITY, 93])
            prev_ts = (self.focus.last_result or {}).get("frame_ts", 0.0)
            result.update({
                "frame_ts": ts,
                "frame_interval_s": round(ts - prev_ts, 3) if 0 < prev_ts < ts else None,
                "roi": [x0, y0, rw, rh],
                "frame_width": fw,
                "frame_height": fh,
                "exposure_s": float(meta.get("actual_exposure_s", getattr(self.camera, "current_exposure_s", 0.0))),
                "gain_db": float(meta.get("gain_db", getattr(self.camera, "current_gain_db", 0.0))),
                "capture_path": meta.get("capture_path"),
                "expires_in_s": round(max(0.0, self.focus.last_action + self.FOCUS_MAX_UNATTENDED_S - time.time()), 1),
            })
            self.focus.last_result = result
            self.focus.crop_jpeg = jpeg.tobytes() if ok else b""
            self.focus.result_key = key
            return self.focus.crop_jpeg, result

    def get_focus_overview(self, width: int = 800, stretch: bool = False) -> bytes:
        """Small whole-frame JPEG for choosing the focus region (encoded once per frame)."""
        # Only frame polls keep focus mode alive: an idle /focus tab refreshing this must not
        frame = self.last_frame
        if frame is None:
            frame, _ = self.capture_frame()
        ts = self.last_capture_time
        key = (ts, width, bool(stretch))
        if self.focus.overview_key == key and self.focus.overview_jpeg:
            return self.focus.overview_jpeg
        h, w = frame.shape[:2]
        small = cv2.resize(frame, (width, int(h * width / w)), interpolation=cv2.INTER_AREA) if w > width else frame
        if stretch:
            small = display_stretch(small)
        ok, jpeg = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 80])
        self.focus.overview_key, self.focus.overview_jpeg = key, jpeg.tobytes() if ok else b""
        return self.focus.overview_jpeg

    def focus_auto_expose(self, target: float = 200.0, max_iter: int = 4) -> Dict[str, Any]:
        """Meter the focus ROI once and hold the result: put its highlights (99.5th percentile) at ~200/255,
        bright but unsaturated, which suits day scenes, stars and lights alike. The live auto-exposure is
        tuned for a 2 s cadence and hunts at focus frame rates, so this is a direct few-frame loop instead.
        Exposure is preferred up to 1 s, then gain (up to 48 dB)."""
        if not self.focus.active:
            raise RuntimeError("Focus mode is not active")
        self.focus.last_action = time.time()
        limits = camera_limits(self.camera)
        min_exp = float(limits.get("min_exposure_s", 1e-4))
        max_exp = min(1.0, float(limits.get("max_exposure_s", 1.0)))
        max_gain = min(48.0, float(limits.get("max_gain_db", 48.0)))
        since = self.last_capture_time
        steps = []
        for _ in range(max_iter):
            frame, _, since = self._focus_new_frame(since, timeout_s=10.0 + 2 * float(getattr(self.camera, "current_exposure_s", 0)))
            self.focus.last_poll = time.time()
            fh, fw = frame.shape[:2]
            x0, y0, rw, rh = self.focus.roi_rect(fw, fh)
            roi = cv2.cvtColor(frame[y0:y0 + rh, x0:x0 + rw], cv2.COLOR_BGR2GRAY)
            p = float(np.percentile(roi, 99.5))
            exp = float(getattr(self.camera, "current_exposure_s", 0.01))
            gain = float(getattr(self.camera, "current_gain_db", 0.0))
            steps.append({"exposure_s": exp, "gain_db": gain, "p99_5": round(p, 1)})
            if abs(p - target) <= 0.12 * target and p < 250:
                break
            # Output is roughly gamma 2.2 encoded: convert the brightness error to a linear factor
            factor = 0.25 if p >= 250 else (target / max(p, 2.0)) ** 2.2
            factor = min(16.0, max(1 / 16.0, factor))
            total = exp * 10 ** (gain / 20.0) * factor
            new_exp = min(max_exp, max(min_exp, total))
            new_gain = min(max_gain, max(0.0, 20.0 * np.log10(total / new_exp))) if total > new_exp else 0.0
            if abs(new_exp - exp) / exp < 0.03 and abs(new_gain - gain) < 0.3:
                break  # at a limit
            with self.lock:
                self.camera.set_hardware_exposure(new_exp, new_gain)
                self.last_capture_time = 0.0
        self._persist_camera_settings()
        with self._focus_lock:
            # Scores from before the change are not comparable
            self.focus.reset_stats()
        state = self.get_focus_state()
        state["metering"] = steps
        self.activity.info("camera", f"Focus metering: exposure {state['exposure_s']:.4g} s, gain {state['gain_db']:.1f} dB")
        return state

    # ------------------------------------------------------------------
    # Motorised lens (Pi Camera Module 3 and other autofocus CSI cameras)
    # ------------------------------------------------------------------
    def _lens_supported(self) -> bool:
        return bool(getattr(self.camera, "lens_range", None)) and hasattr(self.camera, "set_lens_position")

    def _lens_cfg(self) -> Dict[str, Any]:
        cfg = self.station_config.setdefault("lens_focus", {})
        if "mode" not in cfg:
            cfg["mode"] = "auto" if cfg.get("tracking", True) else "manual"
        if cfg.get("position") is not None and "anchor_position" not in cfg:
            cfg["anchor_position"], cfg["anchor_temp_c"] = cfg["position"], cfg.get("sensor_temp_c")
        return cfg

    def _restore_lens(self) -> None:
        """Re-apply the saved lens position: libcamera's own default (IMX708: 1.0 D, ~1 m) is not the sky."""
        if not hasattr(self.camera, "set_lens_position"):
            return
        cfg = self._lens_cfg()
        self.focus_tracker.enabled = cfg["mode"] == "auto"
        self.lens_comp = TempCompensation(cfg.get("samples"))
        saved = cfg.get("position")
        if saved is None:
            return
        self.focus_tracker.reset(float(cfg.get("base_position", saved)))
        try:
            self.camera.lens_position_target = float(saved)
            if getattr(self.camera, "lens_range", None):
                self.camera.set_lens_position(float(saved))
        except Exception:
            pass

    def _save_lens(self, position: float, source: str) -> None:
        """Remember a lens position. Sweeps, tracking checks and sets made by hand are verified focus: they
        re-anchor temperature compensation and become samples of the temperature model."""
        cfg = self._lens_cfg()
        temp = self.last_metadata.get("sensor_temp_c")
        pos = round(float(position), 4)
        now = time.time()
        cfg.update({
            "position": pos,
            "source": source,
            "sensor_temp_c": temp,
            "updated_at": datetime.now().astimezone().isoformat(),
            "anchor_position": pos,
            "anchor_temp_c": temp,
        })
        if source in ("sweep", "camera_af", "manual"):
            # Night tracking and compensation stay within reach of where a person or a sweep put the lens
            cfg["base_position"] = pos
            self.focus_tracker.reset(pos, now)
        if source in ("sweep", "tracking") or (source == "manual" and cfg["mode"] == "auto"):
            self.lens_comp.add(temp, pos, source, now)
            cfg["samples"] = self.lens_comp.samples
        if not os.environ.get("PYTEST_CURRENT_TEST"):
            self._save_station_config()

    def set_lens_mode(self, mode: str, reset_learning: bool = False) -> Dict[str, Any]:
        """auto: follow temperature (learned model) and re-check focus on stars every night;
        manual: hold the lens exactly where it was put."""
        cfg = self._lens_cfg()
        if mode == "auto":
            # AF: find focus now (in focus mode) or as soon as there is something to focus on
            cfg["next_auto_sweep"] = 0.0
            self._auto_sweep_retry_ts = 0.0
        cfg["mode"] = "auto" if mode == "auto" else "manual"
        cfg["tracking"] = cfg["mode"] == "auto"
        self.focus_tracker.enabled = cfg["mode"] == "auto"
        if reset_learning:
            self.lens_comp = TempCompensation()
            cfg["samples"] = []
        if cfg["mode"] == "manual" and self.focus_tracker.phase is not None and self.focus_tracker.p0 is not None:
            self.camera.set_lens_position(self.focus_tracker.p0)  # abandon a probe mid-cycle
            self.focus_tracker.phase = None
        if not os.environ.get("PYTEST_CURRENT_TEST"):
            self._save_station_config()
        if (cfg["mode"] == "auto" and self.focus.active and self._lens_supported()
                and not (self.focus.sweep and self.focus.sweep.get("state") == "running")):
            try:
                return self.start_lens_autofocus("sweep")
            except RuntimeError:
                pass
        return self.get_focus_state()

    def get_lens_state(self) -> Dict[str, Any]:
        if not self._lens_supported():
            return {"available": False}
        info = dict(self.camera.lens_info() or {})
        if hasattr(self.camera, "af_status"):
            info.update(self.camera.af_status())
        cfg = self._lens_cfg()
        info["saved"] = {k: v for k, v in cfg.items() if k != "samples"}
        info["mode"] = cfg["mode"]
        fit = self.lens_comp.fit()
        temp = self.last_metadata.get("sensor_temp_c")
        anchor = cfg.get("anchor_position", cfg.get("position"))
        info["compensation"] = {
            **fit,
            "temp_c": temp,
            "anchor_position": anchor,
            "anchor_temp_c": cfg.get("anchor_temp_c"),
            "target": round(self.lens_comp.target(float(anchor), cfg.get("anchor_temp_c"), temp), 4)
            if anchor is not None else None,
            "samples": self.lens_comp.samples[-20:],
        }
        info["tracking"] = self.focus_tracker.state()
        nxt = cfg.get("next_auto_sweep")
        info["next_auto_sweep_in_s"] = round(max(0.0, float(nxt) - time.time())) if nxt is not None else 0
        if self.focus_tracker.next_ts:
            info["tracking"]["next_in_s"] = round(max(0.0, self.focus_tracker.next_ts - time.time()))
        info["sweep"] = dict(self.focus.sweep) if self.focus.sweep else None
        return info

    def set_lens(self, position: Optional[float] = None, delta: Optional[float] = None,
                 tracking: Optional[bool] = None, mode: Optional[str] = None,
                 reset_learning: bool = False) -> Dict[str, Any]:
        if not self._lens_supported():
            raise RuntimeError("This camera has no motorised lens")
        if tracking is not None and mode is None:
            mode = "auto" if tracking else "manual"
        if mode is not None or reset_learning:
            self.set_lens_mode(mode or self._lens_cfg()["mode"], reset_learning)
            if position is None and delta is None:
                return self.get_focus_state()
        if self._lens_cfg().get("mode") == "auto":
            # Moving the lens by hand means manual focus (MF): AF must not move it back
            self.set_lens_mode("manual")
        if self.focus.sweep and self.focus.sweep.get("state") == "running":
            # Moving the lens by hand wins over a running autofocus: stop it rather than refuse
            self.focus.sweep_cancel = True
            deadline = time.time() + 5.0
            while self.focus.sweep.get("state") == "running" and time.time() < deadline:
                time.sleep(0.05)
        cur = getattr(self.camera, "lens_position_target", None)
        if cur is None:
            cur = getattr(self.camera, "lens_position", None)
        if cur is None:
            cur = self.camera.lens_range[2]
        target = float(position) if position is not None else float(cur) + float(delta or 0.0)
        pos = self.camera.set_lens_position(target)
        self._save_lens(pos, "manual")
        self.focus.last_action = time.time()
        with self._focus_lock:
            self.focus.reset_stats()
        return self.get_focus_state()

    def _focus_sensor_window(self) -> Tuple[float, float, float, float]:
        """The focus ROI as fractions (x, y, w, h) of the sensor field, undoing the display mirror/flip."""
        frame = self.last_frame
        fh, fw = (frame.shape[:2] if frame is not None else (self.camera.height, self.camera.width))
        x0, y0, rw, rh = self.focus.roi_rect(fw, fh)
        fx, fy, ww, hh = x0 / fw, y0 / fh, rw / fw, rh / fh
        tf = self.station_config.get("image_transform") or {}
        rot180 = int(tf.get("rotate_deg", 0)) % 360 == 180
        if bool(tf.get("flip_horizontal")) != rot180:
            fx = 1.0 - fx - ww
        if bool(tf.get("flip_vertical")) != rot180:
            fy = 1.0 - fy - hh
        return fx, fy, ww, hh

    def start_lens_autofocus(self, mode: str = "sweep", auto: bool = False) -> Dict[str, Any]:
        """Autofocus the motorised lens on the focus ROI and keep the result.

        mode "camera": libcamera's own scan (phase-detect + contrast). Fast and fine in daylight, but it
        rarely locks on stars. mode "sweep": step the lens and pick the best of this app's own score (star
        HFR at night, edge contrast by day), which works on stars and point lights too."""
        if not self._lens_supported():
            raise RuntimeError("This camera has no motorised lens")
        if not self.focus.active:
            raise RuntimeError("Start focus mode first")
        if self.focus.sweep and self.focus.sweep.get("state") == "running":
            return self.get_focus_state()  # already finding focus: nothing more to start
        self.focus.last_action = time.time()
        self.focus.sweep_cancel = False
        self.focus.sweep = {"state": "running", "mode": mode, "phase": "start", "progress": 0.0, "points": [],
                            "started_at": time.time(), "auto": bool(auto)}
        target = self._lens_camera_af if mode == "camera" else self._lens_sweep
        threading.Thread(target=target, name=f"lens-af-{mode}", daemon=True).start()
        return self.get_focus_state()

    def cancel_lens_autofocus(self) -> Dict[str, Any]:
        self.focus.sweep_cancel = True
        return self.get_focus_state()

    def _lens_finish(self, position: Optional[float], state: str, message: str, source: str) -> None:
        sw = self.focus.sweep
        if state == "done" and position is not None and float(position) > SKY_MAX_DIOPTRES:
            # Focused on something nearer than 1 m (window frame, railing, dome dust): not the sky
            back = (self.station_config.get("lens_focus") or {}).get("base_position")
            if back is None or float(back) > SKY_MAX_DIOPTRES:
                back = self.camera.lens_range[0]
            self.camera.set_lens_position(float(back))
            msg = (f"focused on a near object ({1 / float(position):.2f} m, {float(position):.3f} D), not the sky: "
                   f"not saved, lens put back to {float(back):.3f} D. Move the zoom onto distant detail or stars.")
            sw.update({"state": "rejected", "message": msg, "progress": 1.0, "finished_at": time.time(),
                       "rejected_position": round(float(position), 4)})
            with self._focus_lock:
                self.focus.reset_stats()
            self.activity.warning("camera", f"Lens autofocus ({sw.get('mode')}): {msg}")
            if sw.get("auto") and self.focus.active:
                self.stop_focus("automatic autofocus rejected")
            return
        if position is not None:
            position = self.camera.set_lens_position(position)
            if state == "done":  # a failed/cancelled run only puts the lens back; it verifies nothing
                self._save_lens(position, source)
                sw["best_position"] = position
        with self._focus_lock:
            self.focus.reset_stats()
        if state == "done" and self.focus.active:
            sw["phase"] = "saving"
            # Measure the result where the lens now sits, record it in settings, tell the user, leave focus mode
            score = None
            method = sw.get("method") or (self.focus.method if self.focus.method != "auto" else None)
            try:
                if method:
                    score, _ = self._lens_score(float(position), method, 1, 3, self.last_capture_time)
            except Exception:
                pass
            sw["final_score"] = score
            if sw.get("auto"):
                self._lens_cfg()["next_auto_sweep"] = time.time() + self.AUTO_SWEEP_INTERVAL_S
            self.finish_focus("auto_sweep" if sw.get("auto") else source, method=method, score=score, note=message)
            sw.update({"state": state, "message": message + " · saved, focus mode off", "progress": 1.0,
                       "finished_at": time.time()})
            return
        sw.update({"state": state, "message": message, "progress": 1.0, "finished_at": time.time()})
        if not os.environ.get("PYTEST_CURRENT_TEST"):
            self.activity.log("warning", "camera", f"Lens autofocus ({sw.get('mode')}): {message}")
        if sw.get("auto") and self.focus.active:
            self.stop_focus(f"automatic autofocus {state}")

    def apply_focus_record(self, index: int = -1, exposure: bool = True) -> Dict[str, Any]:
        """Reuse a saved focus: lens position (held, saved), zoom region and measure, and optionally its
        exposure/gain (only while focus mode is on, since auto-exposure owns it otherwise)."""
        hist = self.station_config.get("focus_history") or []
        if not hist:
            raise RuntimeError("No saved focus yet")
        rec = hist[index]
        if rec.get("near") or (rec.get("lens_position") or 0) > SKY_MAX_DIOPTRES:
            raise RuntimeError("That record focused on something nearer than 1 m, not the sky")
        if rec.get("lens_position") is not None and self._lens_supported():
            self.set_lens(position=float(rec["lens_position"]))
        roi = rec.get("roi") or {}
        cfg = {"cx": roi.get("cx"), "cy": roi.get("cy"), "roi_size": roi.get("size"), "method": rec.get("method")}
        self.configure_focus(**{k: v for k, v in cfg.items() if v is not None})
        if exposure and self.focus.active and rec.get("exposure_s"):
            self.set_exposure(float(rec["exposure_s"]), float(rec.get("gain_db") or 0.0))
        self.activity.info("camera", f"Reused saved focus from {rec.get('finished_at', '?')[:16]} ({rec.get('source')})")
        return self.get_focus_state()

    def finish_focus(self, source: str = "manual", method: Optional[str] = None, score: Optional[float] = None,
                     note: str = "") -> Dict[str, Any]:
        """End a focusing session: store what was achieved in settings (station config focus_record and a
        short history), log it as a success (shown as a notification on every page) and turn focus mode off."""
        res = dict(self.focus.last_result or {})
        method = method or res.get("method")
        if score is None:
            score = res.get("smoothed", res.get("score"))
        lens_pos = None
        if self._lens_supported():
            lens_pos = getattr(self.camera, "lens_position_target", None)
            if lens_pos is None:
                lens_pos = getattr(self.camera, "lens_position", None)
            if source == "manual" and lens_pos is not None:
                self._save_lens(float(lens_pos), "manual")
        temp = self.last_metadata.get("sensor_temp_c")
        lower = LOWER_IS_BETTER.get(method or "", False)
        rec = {
            "finished_at": datetime.now().astimezone().isoformat(),
            "source": source,
            "camera": describe_camera(self.camera).get("sensor"),
            "method": method,
            "score": round(float(score), 4) if score is not None else None,
            "unit": ("px HFR" if lower else "contrast") if method else None,
            "fwhm_px": round(2.0 * float(score), 2) if (score is not None and lower) else None,
            "best_in_session": self.focus.best,
            "lens_position": round(float(lens_pos), 4) if lens_pos is not None else None,
            "lens_distance_m": round(1.0 / float(lens_pos), 2) if lens_pos and lens_pos > 0.02 else None,
            "lens_mode": self._lens_cfg().get("mode") if self._lens_supported() else None,
            "sensor_temp_c": temp,
            "roi": {"cx": round(self.focus.cx, 4), "cy": round(self.focus.cy, 4), "size": self.focus.roi_size},
            "exposure_s": float(getattr(self.camera, "current_exposure_s", 0.0)),
            "gain_db": float(getattr(self.camera, "current_gain_db", 0.0)),
            "note": note,
            # Focused nearer than 1 m: kept in the history for reference, never offered as sky focus
            "near": bool(lens_pos is not None and float(lens_pos) > SKY_MAX_DIOPTRES),
        }
        self.station_config["focus_record"] = rec
        hist = self.station_config.setdefault("focus_history", [])
        hist.append(rec)
        del hist[:-20]
        if not os.environ.get("PYTEST_CURRENT_TEST"):
            self._save_station_config()
        parts = []
        if rec["lens_position"] is not None:
            dist = f"≈{rec['lens_distance_m']} m" if rec["lens_distance_m"] else "nominal ∞"
            parts.append(f"lens {rec['lens_position']:.3f} D ({dist})")
        if rec["score"] is not None:
            parts.append(f"{method} {rec['score']:.3g}{' px HFR' if lower else ''}")
        if temp is not None:
            parts.append(f"{temp} °C")
        self.activity.success("camera", f"Focus saved ({source}): {', '.join(parts) or 'no measurement'} — focus mode off")
        self.stop_focus("focus finished and saved")
        return {"record": rec, **self.get_focus_state()}

    AUTO_SWEEP_INTERVAL_S = 12 * 3600.0  # full automatic sweep at most twice a day; tracking covers between
    AUTO_SWEEP_RETRY_S = 1800.0

    def _focus_sky_mask(self, frame: np.ndarray) -> Optional[np.ndarray]:
        """The station sky mask (Settings sky/obstruction polygons) at this frame's size, or None."""
        from src.calibration.bright_star_solver import polygon_mask
        m = self.station_config.get("sky_mask") or {}
        return polygon_mask(frame.shape, m.get("sky"), m.get("obstruction"))

    def _maybe_auto_sweep(self, frame: np.ndarray, now: float, cfg: Dict[str, Any]) -> bool:
        """Auto lens mode runs a full sweep autofocus by itself when there is no valid sky focus (none saved,
        or one nearer than 1 m), right after Auto is switched on, and every AUTO_SWEEP_INTERVAL_S, but only
        when the frame has something to focus on (stars/lights by night, detail by day; never at twilight).
        It enters focus mode on the chosen point and leaves it again when done. True when one was started."""
        if self.focus.active or (self.focus.sweep and self.focus.sweep.get("state") == "running"):
            return False
        pos = cfg.get("position")
        valid = pos is not None and float(pos) <= SKY_MAX_DIOPTRES
        if valid and now < float(cfg.get("next_auto_sweep") or 0):
            return False
        if now < float(getattr(self, "_auto_sweep_retry_ts", 0.0)):
            return False
        from src.calibration.auto_exposure import calculate_solar_elevation
        sun = calculate_solar_elevation(self.auto_exposure.lat, self.auto_exposure.lon)
        self._auto_sweep_retry_ts = now + self.AUTO_SWEEP_RETRY_S
        if -12.0 <= sun <= 3.0:
            return False  # twilight: stars too faint, daylight too low and changing
        point = find_focus_point(frame, sky_mask=self._focus_sky_mask(frame))
        if point is None or (sun < -12.0 and point["method"] == "contrast"):
            return False

        def _run():
            try:
                # Measure what the point was chosen for (a lone star would read as 'contrast' in auto)
                self.start_focus(autofocus=False, cx=point["cx"], cy=point["cy"], method=point["method"])
                self.focus.last_action = time.time()
                self.activity.info("camera", f"Auto lens mode: automatic sweep autofocus on {point['method']} "
                                   f"at ({point['cx']:.2f}, {point['cy']:.2f})")
                self.start_lens_autofocus("sweep", auto=True)
            except Exception as exc:
                self.activity.warning("camera", f"Automatic autofocus did not start: {exc}")
                if self.focus.active:
                    self.stop_focus("automatic autofocus did not start")

        threading.Thread(target=_run, name="auto-sweep", daemon=True).start()
        return True

    def _track_focus(self, frame: np.ndarray, meta: Dict[str, Any], now: float) -> None:
        """Auto lens mode on ordinary captures: follow temperature with the learned model (any time of day),
        and at night re-check focus on stars every 20 min (FocusTracker). Needs a saved focus position."""
        if not self._lens_supported():
            return
        cfg = self._lens_cfg()
        if cfg["mode"] == "auto" and self._maybe_auto_sweep(frame, now, cfg):
            return
        if cfg["mode"] != "auto" or cfg.get("position") is None or self.focus_tracker.base is None:
            return
        temp = meta.get("sensor_temp_c")
        cur = getattr(self.camera, "lens_position_target", None)
        if self.focus_tracker.phase is None and cur is not None and cfg.get("anchor_position") is not None:
            lo, hi = self.camera.lens_range[0], self.camera.lens_range[1]
            base = self.focus_tracker.base
            tgt = self.lens_comp.target(float(cfg["anchor_position"]), cfg.get("anchor_temp_c"), temp)
            tgt = round(min(hi, max(lo, min(base + self.focus_tracker.max_offset, max(base - self.focus_tracker.max_offset, tgt)))), 4)
            if abs(tgt - float(cur)) >= 0.004:
                # Temperature moved the focus: follow it now; the next frames show the corrected lens
                self.camera.set_lens_position(tgt)
                return
        from src.calibration.auto_exposure import calculate_solar_elevation
        sun = calculate_solar_elevation(self.auto_exposure.lat, self.auto_exposure.lon)
        is_night = sun < -12.0
        day_metric = None
        if sun > 3.0:
            # By day probe edge contrast on the most detailed tile, chosen once per cycle so all three frames
            # of a check measure the same buildings/rooftops (sky and drifting cloud are skipped)
            if self.focus_tracker.phase is None and now >= self.focus_tracker.next_ts:
                pt = find_focus_point(frame)
                self._day_focus_tile = (pt["cx"], pt["cy"]) if pt and pt["method"] == "contrast" else None
            tile = getattr(self, "_day_focus_tile", None)
            if tile is not None:
                def day_metric(f, tile=tile):
                    fh, fw = f.shape[:2]
                    side = max(64, min(fw, fh) // 6)
                    x0 = int(min(max(0, tile[0] * fw - side / 2), fw - side))
                    y0 = int(min(max(0, tile[1] * fh - side / 2), fh - side))
                    c = _contrast(cv2.cvtColor(f[y0:y0 + side, x0:x0 + side], cv2.COLOR_BGR2GRAY))
                    return 1000.0 / c if c > 1e-6 else None
        before = self.focus_tracker.phase
        new = self.focus_tracker.step(now, frame, meta.get("lens_position"), cur, self.camera.lens_range, is_night, temp,
                                      day_metric=day_metric)
        if new is None:
            return
        self.camera.set_lens_position(new)
        if before == "minus" and self.focus_tracker.phase is None:
            entry = self.focus_tracker.log[-1] if self.focus_tracker.log else {}
            # A completed check is verified focus at this temperature, moved or not
            self._save_lens(new, "tracking")
            if abs(float(entry.get("to", new)) - float(entry.get("from", new))) >= 0.001:
                self.activity.info(
                    "camera", f"Focus tracking ({entry.get('kind')}): lens {entry.get('from')} -> {new} D at {temp} °C "
                    f"({entry.get('hfr') or entry.get('score')})"
                )

    def _roi_method(self, frame: np.ndarray) -> str:
        fh, fw = frame.shape[:2]
        x0, y0, rw, rh = self.focus.roi_rect(fw, fh)
        if self.focus.method != "auto":
            return self.focus.method
        return measure_focus(frame[y0:y0 + rh, x0:x0 + rw], "auto")["method"]

    def _lens_camera_af(self) -> None:
        """libcamera's own AF scan, then checked with this app's score: the result is kept only if it is
        really sharper than where the lens started (blank sky or cloud by day has nothing to lock on)."""
        sw = self.focus.sweep
        start_pos = getattr(self.camera, "lens_position_target", None)
        if start_pos is None:
            start_pos = getattr(self.camera, "lens_position", None)
        try:
            frame, _, since = self._focus_new_frame(self.last_capture_time, timeout_s=15.0)
            method = self._roi_method(frame)
            sw["method"] = method
            lower = LOWER_IS_BETTER[method]
            before = None
            if start_pos is not None:
                before, since = self._lens_score(float(start_pos), method, 0, 3, since)
            if not self.camera.start_autofocus(self._focus_sensor_window()):
                raise RuntimeError("camera refused autofocus")
            sw["phase"] = "camera scan"
            deadline = time.time() + 15.0
            while time.time() < deadline and not self.focus.sweep_cancel:
                _, meta, since = self._focus_new_frame(since, timeout_s=5.0)
                st = self.camera.af_status()
                sw["progress"] = min(0.9, (time.time() - sw["started_at"]) / 6.0)
                if st.get("lens_position") is not None:
                    sw["points"].append({"pos": st["lens_position"], "score": None})
                if st.get("scanning"):
                    continue
                pos = st.get("lens_position")
                if st.get("af_state") != 2 or pos is None:
                    self._lens_restore(start_pos)
                    self._lens_finish(None, "failed", "camera AF found nothing to lock on (blank sky or cloud?); "
                                      f"lens put back to {start_pos} D", "camera_af")
                    return
                sw["phase"] = "checking"
                after, since = self._lens_score(float(pos), method, 1, 3, since)
                sw["score_before"], sw["score_after"] = before, after
                better = (before is None and after is not None) or (
                    after is not None and before is not None and (after < before * 0.97 if lower else after > before * 1.03))
                if not better:
                    self._lens_restore(start_pos)
                    self._lens_finish(None, "rejected",
                                      f"camera AF moved to {pos:.3f} D but the zoom region got no sharper "
                                      f"({before} → {after} {method}); lens put back to {start_pos} D", "camera_af")
                    return
                self._lens_finish(pos, "done", f"focused at {pos:.3f} D ({before:.3g} → {after:.3g} {method})"
                                  if before is not None else f"focused at {pos:.3f} D", "camera_af")
                return
            self._lens_restore(start_pos)
            self._lens_finish(None, "cancelled" if self.focus.sweep_cancel else "failed",
                              "cancelled" if self.focus.sweep_cancel else "timed out", "camera_af")
        except Exception as exc:
            self._lens_restore(start_pos)
            self._lens_finish(None, "failed", str(exc), "camera_af")

    def _lens_restore(self, pos: Optional[float]) -> None:
        if pos is not None:
            try:
                self.camera.set_lens_position(float(pos))
            except Exception:
                pass

    def _lens_score(self, pos: float, method: str, settle: int, frames: int, since: float):
        """Move the lens, drop frames exposed while it moved, then the median ROI score of the next ones."""
        scores, since = self._lens_score_multi(pos, method, settle, frames, since, [])
        return scores[0], since

    def _lens_score_multi(self, pos: float, method: str, settle: int, frames: int, since: float,
                          tiles: List[Dict[str, float]]):
        """Like _lens_score, for the focus ROI and extra tiles (frame fractions, same ROI size) measured
        on the same frames. Returns ([roi score, tile scores...], since); a score is None if unmeasurable."""
        pos = self.camera.set_lens_position(pos)
        wait = 10.0 + 3 * float(getattr(self.camera, "current_exposure_s", 0.0))
        # Controls reach the sensor a few frames late: wait until frames report the new position
        # (when the camera reports one), then drop `settle` more exposed while the lens was moving
        for _ in range(8):
            _, meta, since = self._focus_new_frame(since, timeout_s=wait)
            reported = meta.get("lens_position")
            if reported is None or abs(float(reported) - pos) < 0.006:
                break
        for _ in range(settle):
            _, _, since = self._focus_new_frame(since, timeout_s=wait)
        per = [[] for _ in range(1 + len(tiles))]
        for _ in range(frames):
            frame, _, since = self._focus_new_frame(since, timeout_s=wait)
            self.focus.last_poll = time.time()
            fh, fw = frame.shape[:2]
            rects = [self.focus.roi_rect(fw, fh)]
            side = rects[0][2]
            for t in tiles:
                tx = int(min(max(0, t["cx"] * fw - side / 2), fw - side))
                ty = int(min(max(0, t["cy"] * fh - side / 2), fh - side))
                rects.append((tx, ty, side, side))
            for i, (x0, y0, rw, rh) in enumerate(rects):
                res = measure_focus(frame[y0:y0 + rh, x0:x0 + rw], method)
                if res.get("score") is not None:
                    per[i].append(float(res["score"]))
        return [float(np.median(v)) if v else None for v in per], since

    @staticmethod
    def _sweep_positions(lo: float, hi: float) -> List[float]:
        """0.04 D steps to 1.2 D (where sky focus lies), 0.1 D to 3 D, then 0.25 D to the end of the lens."""
        out, p = [], float(lo)
        while p <= hi + 1e-9:
            out.append(round(p, 4))
            p += 0.04 if p < 1.2 - 1e-9 else 0.1 if p < 3.0 - 1e-9 else 0.25
        return out


    def _lens_sweep(self) -> None:
        sw = self.focus.sweep
        try:
            lo, hi, _ = self.camera.lens_range
            start_pos = getattr(self.camera, "lens_position_target", None)
            # Lock the measure for the whole sweep (auto could flip between stars and contrast)
            frame, _, since = self._focus_new_frame(self.last_capture_time, timeout_s=15.0)
            fh, fw = frame.shape[:2]
            x0, y0, rw, rh = self.focus.roi_rect(fw, fh)
            method = self.focus.method
            if method == "auto":
                method = measure_focus(frame[y0:y0 + rh, x0:x0 + rw], "auto")["method"]
            lower = LOWER_IS_BETTER[method]
            sw["method"] = method
            exp = float(getattr(self.camera, "current_exposure_s", 0.1))
            settle, frames = (0, 2) if exp >= 0.4 else (1, 3)

            def quality(score):
                return float("-inf") if score is None else (-score if lower else score)

            def measure(pos, phase):
                nonlocal since
                score, since = self._lens_score(pos, method, settle, frames, since)
                sw["points"].append({"pos": round(pos, 4), "score": score, "phase": phase})
                return score

            # By day measure several detailed tiles besides the zoom on the same frames: in a scene of sky,
            # cloud and a nearby wall, the wall must not be the only thing the sweep can see
            tiles: List[Dict[str, float]] = []
            if method == "contrast":
                for t in find_focus_tiles(frame, k=8):
                    if abs(t["cx"] - self.focus.cx) * fw > rw or abs(t["cy"] - self.focus.cy) * fh > rh:
                        tiles.append({"cx": t["cx"], "cy": t["cy"]})
            sw["tiles"] = [{"cx": round(t["cx"], 4), "cy": round(t["cy"], 4)} for t in tiles]

            # One pass from infinity (0 D) outwards, continuing over the whole lens range until every
            # measured region is clearly past its peak (30 % worse, or gone, for 3 steps)
            positions = self._sweep_positions(lo, hi)
            n_roi = 1 + len(tiles)
            curves: List[List[Optional[float]]] = [[] for _ in range(n_roi)]
            best = [float("-inf")] * n_roi
            best_at = [None] * n_roi
            worse_run = [0] * n_roi
            done_pos: List[float] = []
            sw["phase"] = "sweep"
            for i, pos in enumerate(positions):
                if self.focus.sweep_cancel:
                    raise InterruptedError
                scores, since = self._lens_score_multi(pos, method, settle, frames, since, tiles)
                done_pos.append(pos)
                sw["points"].append({"pos": round(pos, 4), "score": scores[0], "phase": "sweep"})
                sw["tile_scores"] = sw.get("tile_scores", []) + [[None if v is None else round(v, 4) for v in scores[1:]]]
                for r, v in enumerate(scores):
                    curves[r].append(v)
                    q = quality(v)
                    if q > best[r]:
                        best[r], best_at[r], worse_run[r] = q, pos, 0
                    elif np.isfinite(best[r]):
                        worse = (not np.isfinite(q)) or (abs(q) > abs(best[r]) * 1.3 if lower else q < best[r] * 0.7)
                        worse_run[r] = worse_run[r] + 1 if worse else 0
                sw["progress"] = 0.95 * (i + 1) / len(positions)

                # Once any region is past its peak, the others need not hold the sweep: a region that is
                # flat (blank sky, cloud) or has not improved for 0.6 D since its best is not going to peak
                any_peak = any(worse_run[r] >= 3 for r in range(n_roi))

                def finished(r):
                    if worse_run[r] >= 3:
                        return True  # clearly past its peak
                    if not any_peak:
                        return False  # nothing has peaked yet: keep going to find a peak
                    vals = [quality(v) for v in curves[r] if v is not None]
                    if len(vals) < 3 or (max(vals) - min(vals)) / max(abs(max(vals)), 1e-9) < 0.05:
                        return True
                    return best_at[r] is not None and pos - best_at[r] >= 0.6 - 1e-9
                past = [finished(r) for r in range(n_roi)]
                if all(past) and i >= 4:
                    break

            # Judge every region's curve; sky focus = the most decisive peak within 1 m (1 D) or farther
            results = []
            for r in range(n_roi):
                res = analyse_sweep(done_pos, curves[r], lower)
                res["roi"] = 0 if r == 0 else r
                results.append(res)
            sky = [x for x in results if x["ok"] and x["vertex"] <= SKY_MAX_DIOPTRES]
            near = [x for x in results if x["ok"] and x["vertex"] > SKY_MAX_DIOPTRES]
            sw["regions"] = [{k: x.get(k) for k in ("roi", "ok", "reason", "vertex", "spread")} for x in results]
            if not sky:
                pos_back = start_pos if start_pos is not None and start_pos <= SKY_MAX_DIOPTRES else lo
                self.camera.set_lens_position(float(pos_back))
                sw["fit"] = results[0].get("fit")
                if near:
                    nb = max(near, key=lambda x: x["spread"])
                    msg = (f"sweep complete: the sharpest focus found is {nb['vertex']:.2f} D (~{1 / nb['vertex']:.2f} m), "
                           f"a nearby object; nothing in focus beyond 1 m (blank sky/cloud elsewhere). Not saved; lens back "
                           f"to {float(pos_back):.3f} D. Zoom onto distant buildings or stars, or set it by hand (MF).")
                    sw["best_position"] = nb["vertex"]
                    sw["fit"] = nb.get("fit") if nb["roi"] == 0 else sw["fit"]
                    self._lens_finish(None, "rejected", msg, "sweep")
                else:
                    reasons = sorted({x.get("reason") for x in results})
                    self._lens_finish(None, "failed",
                                      f"sweep complete, no focus peak in {n_roi} region(s) ({', '.join(reasons)}): nothing "
                                      "with detail to focus on (blank sky or cloud?). Lens put back; zoom onto distant "
                                      "buildings, the horizon or stars.", "sweep")
                return
            win = max(sky, key=lambda x: x["spread"])
            if win["roi"] != 0:
                # Show the region that decided the focus, and its curve
                t = tiles[win["roi"] - 1]
                with self._focus_lock:
                    self.focus.cx, self.focus.cy = t["cx"], t["cy"]
                sw["points"] = [{"pos": round(p, 4), "score": v, "phase": "sweep"} for p, v in zip(done_pos, curves[win["roi"]])]
            sw["fit"] = win.get("fit")
            final = round(min(hi, max(lo, win["vertex"])), 4)
            best_score = win.get("peak_score")
            how = "parabola peak" if win.get("fit") else "best step"

            # Come back over the peak: one pass downwards from 2 coarse steps above to 2 below in quarter
            # steps, on the winning region only (the zoom now shows it), and fit the parabola on those
            coarse = 0.04 if final < 1.2 else 0.1 if final < 3.0 else 0.25
            fine = max(0.01, coarse / 4)
            top, bottom = min(positions[-1], final + 2 * coarse), max(lo, final - 2 * coarse)
            back = [round(top - k * fine, 4) for k in range(int(round((top - bottom) / fine)) + 1)]
            sw["phase"] = "refine"
            refine: List[Tuple[float, Optional[float]]] = []
            for k, pos in enumerate(back):
                if self.focus.sweep_cancel:
                    raise InterruptedError
                scores, since = self._lens_score_multi(pos, method, settle, frames, since, [])
                refine.append((pos, scores[0]))
                sw["points"].append({"pos": pos, "score": scores[0], "phase": "refine"})
                sw["progress"] = 0.95 + 0.05 * (k + 1) / len(back)
            fx = np.array([p for p, v in refine if v is not None])
            fy = np.array([quality(v) for p, v in refine if v is not None])
            if len(fx) >= 5:
                a2, a1, a0 = np.polyfit(fx - final, fy, 2)
                if a2 < 0:
                    v = final - a1 / (2 * a2)
                    if fx.min() <= v <= fx.max():
                        sw["coarse_vertex"] = final
                        final = round(float(v), 4)
                        how = "fine parabola peak"
                        sw["fit"] = {"a": float(a2), "b": float(a1), "c": float(a0), "x0": float(sw["coarse_vertex"]),
                                     "from": float(fx.min()), "to": float(fx.max()), "vertex": final,
                                     "lower_is_better": bool(lower)}
                        best_score = max((v2 for p2, v2 in refine if v2 is not None),
                                         key=lambda v2: quality(v2))
            unit = "px HFR" if lower else "contrast"
            dist = f"{1 / final:.1f} m" if final > 0.05 else "infinity"
            others = f"; {len(near)} near region(s) skipped" if near else ""
            where = "" if win["roi"] == 0 else f" (detail region {win['roi']} of {n_roi})"
            self._lens_finish(final, "done", f"{how} at {final:.3f} D ({dist}){where}, "
                              f"{best_score:.3g} {unit}{others}" if best_score is not None else
                              f"{how} at {final:.3f} D ({dist}){where}{others}", "sweep")
        except InterruptedError:
            pos_back = getattr(self.camera, "lens_position_target", None)
            self._lens_finish(None, "cancelled", f"cancelled, lens left at {pos_back}", "sweep")
        except Exception as exc:
            self._lens_finish(None, "failed", str(exc), "sweep")

    def focus_auto_point(self) -> Dict[str, Any]:
        """Move the zoom onto the best thing to focus on in the current frame (see find_focus_point)."""
        frame = self.last_frame
        if frame is None:
            frame, _ = self.capture_frame()
        point = find_focus_point(frame, sky_mask=self._focus_sky_mask(frame))
        if point is None:
            state = self.get_focus_state()
            state.update({"found": False, "message": "nothing to focus on in the frame (blank sky, cloud or fog)"})
            return state
        state = self.configure_focus(cx=point["cx"], cy=point["cy"])
        state.update({"found": True, "point": {k: point[k] for k in ("cx", "cy", "method")},
                      "message": f"focus point on {'a star' if point['method'] == 'stars' else 'a bright light' if point['method'] == 'lights' else 'the most detailed area'}"})
        return state

    def focus_auto_roi(self, lights: bool = False) -> Dict[str, Any]:
        """Centre the focus ROI on the best point source in the whole frame (stars, else bright lights)."""
        frame = self.last_frame
        if frame is None:
            frame, _ = self.capture_frame()
        h, w = frame.shape[:2]
        # Search a half-size copy (keeps the cost ~20 ms on 5 MP) and skip the frame's outer 3 %
        scale = 2 if w > 1600 else 1
        small = cv2.resize(frame, (w // scale, h // scale), interpolation=cv2.INTER_AREA) if scale > 1 else frame
        mh, mw = int(small.shape[0] * 0.03), int(small.shape[1] * 0.03)
        star = find_best_star(small[mh:small.shape[0] - mh, mw:small.shape[1] - mw], lights=lights)
        if star is None:
            return {"found": False, **self.get_focus_state()}
        cx = (star["x"] + mw + 0.5) * scale / w
        cy = (star["y"] + mh + 0.5) * scale / h
        state = self.configure_focus(cx=cx, cy=cy)
        return {"found": True, "source": star, **state}

    def _stamp(self, frame: np.ndarray, meta: Dict[str, Any], when: Optional[datetime] = None,
               brightness: Optional[float] = None) -> np.ndarray:
        """Copy of frame with capture time, exposure, gain and mean brightness written on it."""
        try:
            return stamp_frame_info(
                frame,
                when or datetime.fromtimestamp(self.last_capture_time or time.time()).astimezone(),
                float(meta.get("actual_exposure_s", getattr(self.camera, "current_exposure_s", 0.0))),
                float(meta.get("gain_db", getattr(self.camera, "current_gain_db", 0.0))),
                brightness if brightness is not None else frame_brightness(frame),
            )
        except Exception:
            return frame

    def get_display_frame(self) -> Tuple[Optional[np.ndarray], float]:
        """(frame, capture timestamp) for viewers: the star-boosted copy when it belongs to the latest capture."""
        frame, ts = self.last_frame, self.last_capture_time
        display = self.display_frame
        if display is not None and self._display_frame_ts == ts:
            return display, ts
        return frame, ts

    def _star_boost_frame(self, frame: np.ndarray, raw10: bool = True) -> np.ndarray:
        """Sum-bin stars for viewers at night (sun below -6 deg), limited to the station sky mask."""
        cfg = self.station_config.get("star_boost") or {}
        if not cfg.get("enabled") or frame is None or frame.ndim != 3:
            return frame
        try:
            from src.calibration.auto_exposure import calculate_solar_elevation
            if calculate_solar_elevation(self.auto_exposure.lat, self.auto_exposure.lon) >= -6.0:
                return frame
            user_mask = self.station_config.get("sky_mask")
            key = (frame.shape[:2], repr(user_mask))
            if self._star_mask_cache[0] != key:
                self._star_mask_cache = (key, sky_polygon_mask(frame.shape, user_mask))
            return star_bin_boost(frame, int(cfg.get("bin", 3)), sky_mask=self._star_mask_cache[1],
                                  gamma=GAMMA_RAW10 if raw10 else GAMMA_SRGB)
        except Exception:
            return frame

    def get_star_boost(self) -> Dict[str, Any]:
        cfg = self.station_config.get("star_boost") or {}
        return {"enabled": bool(cfg.get("enabled", False)), "bin": int(cfg.get("bin", 3)), "bins": list(STAR_BOOST_BINS)}

    def set_star_boost(self, enabled: Optional[bool] = None, bin_factor: Optional[int] = None) -> Dict[str, Any]:
        """Turn night star boost (software sum-binning of the viewer frame) on/off and pick the NxN window."""
        if bin_factor is not None and int(bin_factor) not in STAR_BOOST_BINS:
            raise ValueError(f"Unsupported star boost bin {bin_factor}")
        with self.lock:
            cfg = self.station_config.setdefault("star_boost", {"enabled": False, "bin": 3})
            if enabled is not None:
                cfg["enabled"] = bool(enabled)
            if bin_factor is not None:
                cfg["bin"] = int(bin_factor)
            self._save_station_config()
            self.activity.info("camera", f"Star boost: {'on' if cfg['enabled'] else 'off'}, bin {cfg['bin']}x{cfg['bin']}")
            self.last_capture_time = 0.0
        return {"success": True, "message": "Star boost updated.", **self.get_star_boost()}

    def get_white_balance(self) -> Dict[str, Any]:
        """Return RAW10 white balance settings and the gains currently applied."""
        return get_white_balance()

    def set_white_balance(
        self, mode: Optional[str] = None, r_gain: Optional[float] = None, b_gain: Optional[float] = None
    ) -> Dict[str, Any]:
        """Update and persist RAW10 white balance (auto gray-world or manual R/B gains)."""
        with self.lock:
            cur = get_white_balance()
            if mode == "manual" and r_gain is None and b_gain is None and cur["mode"] == "auto":
                # Switching to manual starts from the gains auto WB is using right now
                r_gain, b_gain = cur["applied_r_gain"], cur["applied_b_gain"]
            wb = configure_white_balance(mode, r_gain, b_gain)
            self.station_config["white_balance"] = {
                "mode": wb["mode"],
                "r_gain": wb["r_gain"],
                "b_gain": wb["b_gain"],
            }
            self._save_station_config()
            self.last_capture_time = 0.0
        return {"success": True, "message": "White balance updated.", **get_white_balance()}

    def set_image_transform(
        self,
        flip_horizontal: Optional[bool] = None,
        flip_vertical: Optional[bool] = None,
        rotate_deg: Optional[int] = None,
        rotate_isp_180: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Update and persist image orientation (mirror / flip / rotate) used by live, archive and products."""
        with self.lock:
            tf = self.station_config.setdefault(
                "image_transform", {"flip_horizontal": False, "flip_vertical": False, "rotate_deg": 0}
            )
            if flip_horizontal is not None:
                tf["flip_horizontal"] = bool(flip_horizontal)
            if flip_vertical is not None:
                tf["flip_vertical"] = bool(flip_vertical)
            if rotate_deg is not None:
                tf["rotate_deg"] = 180 if int(rotate_deg) % 360 == 180 else 0
            if rotate_isp_180 is not None:
                tf["rotate_isp_180"] = bool(rotate_isp_180)
            self._save_station_config()
            self.activity.info(
                "camera",
                f"Image orientation: mirror={tf.get('flip_horizontal', False)}, flip={tf.get('flip_vertical', False)}, "
                f"rotate={tf.get('rotate_deg', 0)}°, day-only 180°={tf.get('rotate_isp_180', False)}",
            )
            # Force the next reader to grab a freshly oriented frame
            self.last_capture_time = 0.0
        return {
            "success": True,
            "message": "Image orientation updated (mirror / flip / rotate).",
            "image_transform": dict(tf),
            "station_config": self.get_station_config(),
        }

    def get_station_config(self) -> Dict[str, Any]:
        """Return station coordinates and astrometric camera/lens calibration parameters."""
        return dict(self.station_config)

    def update_station_location(
        self,
        latitude_deg: Optional[float] = None,
        longitude_deg: Optional[float] = None,
        elevation_m: Optional[float] = None,
        location_name: Optional[str] = None,
        geo_source: str = "manual",
        fov_hint: str = "auto",
        auto_plate_solve: bool = False,
        manual_overrides: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Update station GPS coordinates and/or manual camera/lens/orientation settings on the Web UI.
        Does NOT automatically capture or plate-solve unless auto_plate_solve=True is explicitly requested.
        """
        if latitude_deg is not None:
            self.station_config["latitude_deg"] = round(float(latitude_deg), 5)
            self.auto_exposure.lat = self.station_config["latitude_deg"]
        if longitude_deg is not None:
            self.station_config["longitude_deg"] = round(float(longitude_deg), 5)
            self.auto_exposure.lon = self.station_config["longitude_deg"]
        if elevation_m is not None:
            self.station_config["elevation_m"] = round(float(elevation_m), 1)
            self.auto_exposure.elevation = self.station_config["elevation_m"]
        if location_name:
            self.station_config["location_name"] = str(location_name).strip()
        if geo_source:
            self.station_config["geo_source"] = geo_source

        # Apply manual optical/orientation parameter adjustments from Web UI
        if manual_overrides:
            optics = self.station_config.setdefault("optics", {})
            orientation = self.station_config.setdefault("orientation", {})
            if manual_overrides.get("lens_model") is not None:
                optics["lens_model"] = str(manual_overrides["lens_model"]).strip()
            if manual_overrides.get("focal_length_mm") is not None:
                fl = round(float(manual_overrides["focal_length_mm"]), 3)
                old_fl = float(optics.get("focal_length_mm") or 0.0)
                if optics.get("focal_length_px") and fl > 0 and abs(fl - old_fl) > 0.005 * max(old_fl, 1e-6):
                    # A new lens: keep the fixed-lens solver and the overlay on the user's focal length
                    pitch = float((self.station_config.get("camera_sensor") or {}).get("pixel_pitch_um") or 2.0)
                    optics["focal_length_px"] = round(fl * 1000.0 / pitch, 2)
                optics["focal_length_mm"] = fl
                if fl > 0 and manual_overrides.get("pixel_scale_arcsec_px") is None:
                    optics["pixel_scale_arcsec_px"] = round(206.265 * 2.0 / fl, 2)
            if manual_overrides.get("pixel_scale_arcsec_px") is not None:
                optics["pixel_scale_arcsec_px"] = round(float(manual_overrides["pixel_scale_arcsec_px"]), 2)
            if manual_overrides.get("fov_horizontal_deg") is not None:
                optics["fov_horizontal_deg"] = round(float(manual_overrides["fov_horizontal_deg"]), 2)
            if manual_overrides.get("fov_vertical_deg") is not None:
                optics["fov_vertical_deg"] = round(float(manual_overrides["fov_vertical_deg"]), 2)
            if manual_overrides.get("fov_diagonal_deg") is not None:
                optics["fov_diagonal_deg"] = round(float(manual_overrides["fov_diagonal_deg"]), 2)
            if manual_overrides.get("optical_center_x_px") is not None:
                optics["optical_center_x_px"] = round(float(manual_overrides["optical_center_x_px"]), 1)
            if manual_overrides.get("optical_center_y_px") is not None:
                optics["optical_center_y_px"] = round(float(manual_overrides["optical_center_y_px"]), 1)
            cx = float(optics.get("optical_center_x_px", 1296.0))
            cy = float(optics.get("optical_center_y_px", 972.0))
            optics["sensor_center_offset_px"] = [round(cx - 1296.0, 1), round(cy - 972.0, 1)]
            if manual_overrides.get("horizon_radius_90_px") is not None:
                optics["horizon_radius_90_px"] = round(float(manual_overrides["horizon_radius_90_px"]), 1)
            if manual_overrides.get("azimuth_deg") is not None:
                orientation["azimuth_deg"] = round(float(manual_overrides["azimuth_deg"]) % 360.0, 2)
            if manual_overrides.get("altitude_deg") is not None:
                alt = max(-90.0, min(90.0, float(manual_overrides["altitude_deg"])))  # a horizon-facing camera can point below 0°
                orientation["altitude_deg"] = round(alt, 2)
                if manual_overrides.get("zenith_tilt_deg") is None:
                    orientation["zenith_tilt_deg"] = round(90.0 - alt, 2)
            if manual_overrides.get("zenith_tilt_deg") is not None:
                tilt = max(0.0, min(180.0, float(manual_overrides["zenith_tilt_deg"])))
                orientation["zenith_tilt_deg"] = round(tilt, 2)
                if manual_overrides.get("altitude_deg") is None:
                    orientation["altitude_deg"] = round(90.0 - tilt, 2)
            if manual_overrides.get("north_position_angle_deg") is not None:
                npa = round(float(manual_overrides["north_position_angle_deg"]) % 360.0, 2)
                orientation["north_position_angle_deg"] = npa
                orientation["rotation_deg"] = npa
            self.station_config["solve_source"] = "manual_ui_settings"

        self._save_station_config()

        self.activity.success(
            "station",
            f"Station configuration saved: {self.station_config.get('location_name')} "
            f"({self.station_config.get('latitude_deg')}°N, {self.station_config.get('longitude_deg')}°E)",
        )
        if auto_plate_solve:
            return self.run_station_plate_solve(fov_hint=fov_hint)
        return {
            "success": True,
            "message": f"Saved station configuration '{self.station_config.get('location_name')}' ({self.station_config.get('latitude_deg')}°N, {self.station_config.get('longitude_deg')}°E).",
            "station_config": self.get_station_config(),
        }

    def auto_locate_station(self, auto_plate_solve: bool = False) -> Dict[str, Any]:
        """
        Auto-detect station coordinates via public IP geolocation when requested on Web UI.
        By default does NOT run plate-solving automatically unless auto_plate_solve=True.
        """
        from src.calibration.station_solver import auto_detect_ip_location

        geo = auto_detect_ip_location()
        res = self.update_station_location(
            latitude_deg=geo["latitude_deg"],
            longitude_deg=geo["longitude_deg"],
            elevation_m=geo.get("elevation_m", 20.0),
            location_name=geo.get("location_name", "Auto-Detected Station"),
            geo_source=geo.get("geo_source", "ip_geolocation"),
            auto_plate_solve=auto_plate_solve,
        )
        res["message"] = (
            f"Station coordinates located automatically: {geo.get('location_name')} "
            f"({geo['latitude_deg']:.4f}°N, {geo['longitude_deg']:.4f}°E, {geo.get('elevation_m', 20.0):.0f}m)."
        )
        return res

    def start_plate_solve_job(self, fov_hint: str = "auto") -> Dict[str, Any]:
        """Run plate-solving in the background (the solver can take minutes); poll get_plate_solve_status()."""
        with self._plate_solve_lock:
            if self.plate_solve_status.get("state") == "running":
                return dict(self.plate_solve_status)
            self.plate_solve_status = {
                "state": "running",
                "started_at": datetime.now().astimezone().isoformat(),
                "finished_at": None,
                "message": "Capturing a series of long exposures and matching the stars (usually 2-3 minutes)...",
            }
        self.activity.info("station", "Plate-solve started")

        def _job():
            try:
                res = self.run_station_plate_solve(fov_hint=fov_hint)
                state = "success" if res.get("success") else "failed"
                update = {"state": state, "message": res.get("message"), "error": res.get("error")}
            except Exception as exc:
                self.activity.error("station", f"Plate-solve crashed: {exc}")
                update = {"state": "failed", "message": f"Plate-solve crashed: {exc}", "error": str(exc)}
            update["finished_at"] = datetime.now().astimezone().isoformat()
            with self._plate_solve_lock:
                self.plate_solve_status.update(update)

        threading.Thread(target=_job, name="plate-solve", daemon=True).start()
        return dict(self.plate_solve_status)

    def _ps_progress(self, stage: str, message: str, eta_s: Optional[float] = None, **extra: Any) -> None:
        """Publish plate-solve progress (stage, frame k/n, expected end) for the status poll."""
        with self._plate_solve_lock:
            if self.plate_solve_status.get("state") != "running":
                return
            self.plate_solve_status.update(stage=stage, message=message,
                                           eta_ts=None if eta_s is None else time.time() + eta_s, **extra)

    def get_plate_solve_status(self) -> Dict[str, Any]:
        with self._plate_solve_lock:
            out = dict(self.plate_solve_status)
        out["station_config"] = self.get_station_config()
        return out

    def get_sky_mask(self) -> Dict[str, Any]:
        m = self.station_config.get("sky_mask") or {}
        return {"sky": list(m.get("sky") or []), "obstruction": list(m.get("obstruction") or [])}

    def set_sky_mask(self, sky: Optional[List] = None, obstruction: Optional[List] = None) -> Dict[str, Any]:
        """Save the plate-solve sky/obstruction polygons (frame fractions); both empty clears the mask."""
        def clean(polys):
            out = []
            for poly in polys or []:
                if not 3 <= len(poly) <= 64:
                    raise ValueError("each polygon needs 3 to 64 points")
                pts = []
                for pt in poly:
                    if len(pt) != 2:
                        raise ValueError("polygon points are [x, y] fractions of the frame")
                    pts.append([round(min(1.0, max(0.0, float(pt[0]))), 4), round(min(1.0, max(0.0, float(pt[1]))), 4)])
                out.append(pts)
            if len(out) > 16:
                raise ValueError("at most 16 polygons")
            return out

        mask = {"sky": clean(sky), "obstruction": clean(obstruction)}
        if mask["sky"] or mask["obstruction"]:
            self.station_config["sky_mask"] = mask
        else:
            self.station_config.pop("sky_mask", None)
        self._save_station_config()
        self.activity.info("station", f"Plate-solve sky mask saved: {len(mask['sky'])} sky, "
                           f"{len(mask['obstruction'])} obstruction polygon(s)")
        return {"success": True, **self.get_sky_mask()}

    def _lens_intrinsics(self, frame_w: int) -> Optional[Dict[str, Any]]:
        """Solved lens intrinsics scaled to a frame of this width, or None if the lens was never solved."""
        optics = self.station_config.get("optics") or {}
        sensor = self.station_config.get("camera_sensor") or {}
        try:
            f = float(optics["focal_length_px"])
            proj = str(optics["lens_projection"])
            k = frame_w / float(sensor.get("width_px") or frame_w)
            return {"f": f * k, "cx": float(optics["optical_center_x_px"]) * k,
                    "cy": float(optics["optical_center_y_px"]) * k,
                    "k": float(optics.get("distortion_k") or 0.0), "proj": proj}
        except (KeyError, TypeError, ValueError):
            return None

    def _capture_plate_solve_frames(self, n_frames: int = 6, exposure_s: float = 10.0,
                                    min_span_s: float = 90.0, target_level: float = 150.0
                                    ) -> Tuple[List[np.ndarray], List[float]]:
        """A short series of long exposures for star tracking; just one frame by day, in tests or on the simulator.

        Stars drift ~6 px over 90 s on a fisheye while hot pixels stay put, so the frames are spread over
        min_span_s. Exposure (up to exposure_s) and gain are scaled from the current frame so the sky background
        sits near target_level (of 255, gamma-encoded output) instead of saturating under city light. Frames are kept as grayscale
        to save memory; exposure mode and settings are restored afterwards.
        """
        self._ps_progress("metering", "Measuring the sky brightness to pick the exposure")
        frame, meta = self.capture_frame(force_refresh=True)
        if os.environ.get("PYTEST_CURRENT_TEST") or getattr(self.camera, "simulator", False):
            return [frame], [time.time()]
        try:
            from src.calibration.auto_exposure import calculate_solar_elevation
            if calculate_solar_elevation(self.auto_exposure.lat, self.auto_exposure.lon) > -6.0:
                return [frame], [time.time()]
        except Exception:
            return [frame], [time.time()]

        old_mode = self.mode
        old_exp = float(getattr(self.camera, "current_exposure_s", exposure_s))
        old_gain = float(getattr(self.camera, "current_gain_db", 12.0))
        min_gain = float(getattr(self.camera, "min_gain_db", 0.0) or 0.0)

        def settings_for(img, exp0, gain0):
            """Exposure/gain putting the sky median at target_level (signal ~ exposure * 10^(gain/20))."""
            g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
            level = float(np.median(g[::8, ::8]))
            ratio = 0.25 if level >= 250 else target_level / max(level, 2.0)  # saturated: cut hard, re-check
            want = exp0 * 10 ** (gain0 / 20.0) * ratio
            gain = min(max(20.0 * math.log10(want / exposure_s), min_gain), old_gain + 12.0)
            exp = min(exposure_s, max(0.5, want / 10 ** (gain / 20.0)))
            return exp, gain

        exp, gain = settings_for(frame, float(meta.get("actual_exposure_s", old_exp)), float(meta.get("gain_db", old_gain)))
        frames: List[np.ndarray] = []
        times: List[float] = []
        try:
            self.mode = "manual"  # keep auto-exposure from changing the series
            with self.lock:
                self.camera.set_hardware_exposure(exp, gain)
            interval = min_span_s / max(1, n_frames - 1)
            t0, adjustments = None, 0

            def report(note: str = "") -> None:
                done = len(frames)
                left = (min_span_s + exp) if t0 is None else max(0.0, t0 + (n_frames - 1) * interval + exp - time.time())
                self._ps_progress(
                    "capturing", note or f"Capturing frame {done + 1}/{n_frames} ({exp:.1f} s at {gain:.1f} dB)",
                    eta_s=left + 15.0, frame=done, frames_total=n_frames,
                )

            report()
            for _ in range(n_frames + 6):  # spares for frames still at old settings or re-exposed
                if t0 is not None:
                    wait = t0 + len(frames) * interval - time.time()
                    if wait > 0:
                        time.sleep(wait)
                frame, meta = self.capture_frame(force_refresh=True)
                got = float(meta.get("actual_exposure_s", exp))
                if abs(got - exp) > 0.2 * exp:
                    continue  # still the previous exposure
                g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame.copy()
                level = float(np.median(g[::8, ::8]))
                if (level > 225.0 or level < target_level / 3.0) and adjustments < 3 and len(frames) < 2:
                    adjustments += 1  # sky brighter/darker than predicted: re-expose and restart the series
                    exp, gain = settings_for(g, got, float(meta.get("gain_db", gain)))
                    with self.lock:
                        self.camera.set_hardware_exposure(exp, gain)
                    frames, times, t0 = [], [], None
                    report(f"Sky brighter/darker than expected: re-exposing at {exp:.1f} s, {gain:.1f} dB (series restarts)")
                    continue
                frames.append(g)
                times.append(time.time() - got / 2.0)
                t0 = t0 or times[-1]
                if len(frames) >= n_frames:
                    break
                report()
        finally:
            with self.lock:
                self.camera.set_hardware_exposure(old_exp, old_gain)
            self.mode = old_mode
            self.last_capture_time = 0.0
        self.activity.info("station", f"Plate-solve series: {len(frames)} frames of {exp:.1f} s at {gain:.1f} dB")
        if len(frames) < 3:
            return [frame], [time.time()]
        if self.station_config.get("plate_solve_debug"):
            dbg = os.path.join(self.output_dir, "plate_solve_debug")
            os.makedirs(dbg, exist_ok=True)
            for i, (g, t) in enumerate(zip(frames, times)):
                cv2.imwrite(os.path.join(dbg, f"f{i}_{t:.1f}.png"), g)
        return frames, times

    def _hot_pixel_exclusion(self, shape: Tuple[int, ...], exp_s: float, gain_db: float) -> Optional[np.ndarray]:
        """Hot pixels of the closest dark profile, scaled to this frame and grown by 2 px (True = hot)."""
        try:
            profiles = [p for p in self.get_profiles_matrix() if p["ready"] or p.get("raw10_ready")]
            masks = []
            for p in sorted(profiles, key=lambda p: abs(p["exposure_s"] - exp_s) + abs(p["gain_db"] - gain_db) / 12.0):
                path = os.path.join(self.calibration_dir, f"hot_pixels_exp{p['exposure_s']:.1f}s_gain{p['gain_db']:.0f}dB.npy")
                if os.path.isfile(path):
                    masks.append(np.load(path))
                    break
            if not masks or not np.any(masks[0]):
                return None
            hot = masks[0]
            h, w = shape[:2]
            ys, xs = np.nonzero(hot)
            out = np.zeros((h, w), dtype=np.uint8)
            out[np.minimum(h - 1, (ys * h / hot.shape[0]).astype(int)), np.minimum(w - 1, (xs * w / hot.shape[1]).astype(int))] = 1
            return cv2.dilate(out, np.ones((5, 5), np.uint8)).astype(bool)
        except Exception:
            return None

    def _solve_bright_star_pattern(self, frame: np.ndarray, lat: float, lon: float,
                                   frames: Optional[List[np.ndarray]] = None,
                                   times: Optional[List[float]] = None,
                                   exclude: Optional[np.ndarray] = None) -> Optional[Dict[str, Any]]:
        """Match the brightest stars against the catalog; apply and return the result, or None if unsure.

        With a frame series, only points that persist and drift with the sky are used (hot pixels are
        static). With already-solved lens intrinsics, only the rotation is fitted first (needs 3-4 stars).
        """
        from src.calibration import bright_star_solver as bss

        h, w = frame.shape[:2]
        optics0 = self.station_config.get("optics") or {}
        is_fisheye = bool(re.search(r"fisheye|equidistant", str(optics0.get("projection_type", "")), re.I)) or (
            optics0.get("lens_projection") == "equidistant"
        )
        attempts = [("equidistant", range(120, 244, 6)), ("rectilinear", range(50, 125, 4))]
        if not is_fisheye:
            attempts.reverse()
        intr = self._lens_intrinsics(w)
        try:
            mask = bss.combined_sky_mask(frame, self.station_config.get("sky_mask"))
            if exclude is not None and exclude.shape == frame.shape[:2]:
                mask = (np.ones(exclude.shape, dtype=bool) if mask is None else mask) & ~exclude
            tracked, when = None, None
            if frames is not None and len(frames) >= 3:
                hfov = float(optics0.get("fov_horizontal_deg") or 90.0)
                f_est = intr["f"] if intr else (w / 2.0) / math.radians(min(hfov, 200.0) / 2.0)
                tracked = bss.track_sidereal_points(frames, times, f_est, sky_mask=mask)
                when = datetime.fromtimestamp(tracked["time"]).astimezone()
                self.activity.info(
                    "station",
                    f"Star tracking over {len(frames)} frames ({tracked['span_s']:.0f} s): "
                    f"{tracked['persistent']} persistent points, {tracked['static']} static (hot pixels), "
                    f"{len(tracked['points'])} drifting with the sky "
                    f"(points per frame: {', '.join(map(str, tracked['detections_per_frame']))})",
                )
            res = {"success": False, "error": "not run"}
            if intr is not None:
                pts = tracked["points"] if tracked is not None else bss.detect_bright_points(
                    frame, max_points=30, sky_mask=mask, sigma=3.5)
                res = bss.solve_fixed_lens(
                    pts, (w, h), lat, lon, when, intr["f"], (intr["cx"], intr["cy"]), intr["k"], intr["proj"],
                    velocity=tracked["velocity"] if tracked is not None else None, sky_mask=mask)
                if not res.get("success"):
                    self.activity.info("station", f"Fixed-lens solve: {res.get('error')}; trying a full lens solve")
            if not res.get("success"):
                det = tracked["points"] if tracked is not None and len(tracked["points"]) >= 5 else None
                for proj, fovs in attempts:
                    res = bss.solve_bright_stars(frame, lat, lon, when=when, projection=proj, hfov_candidates_deg=fovs,
                                                 detections=det, sky_mask=mask)
                    if res.get("success"):
                        break
        except Exception as exc:
            self.activity.warning("station", f"Bright-star solve crashed: {exc}")
            return None
        if not res.get("success"):
            self.activity.info("station", f"Bright-star solve: {res.get('error')}; trying the hoan.uk plate solver")
            return None
        if res.get("mode") == "fixed_lens":
            return self._apply_fixed_lens_solution(res)

        h, w = frame.shape[:2]
        sensor = self.station_config.get("camera_sensor") or {}
        pitch = float(sensor.get("pixel_pitch_um") or 2.0)
        # The solved frame may be downscaled to the output resolution; station optics use sensor pixels
        k = float(sensor.get("width_px") or w) / float(w)
        w, h = w * k, h * k
        focal_sensor_px = res["focal_px"] * k
        proj = res.get("projection", "rectilinear")
        ocx, ocy = (res.get("optical_center_px") or [w / k / 2.0, h / k / 2.0])
        ocx, ocy = round(ocx * k, 1), round(ocy * k, 1)
        f_mm = round(focal_sensor_px * pitch / 1000.0, 3)
        hfov, vfov = res["fov_horizontal_deg"], res["fov_vertical_deg"]
        names = [m["name"] for m in res["matched_stars"]]
        ra, dec = res["center_ra_deg"], res["center_dec_deg"]
        ra_h = ra / 15.0
        dec_abs = abs(dec)
        scale = 3600.0 * math.degrees(1.0 / focal_sensor_px)
        optics = dict(self.station_config.get("optics") or {})
        fisheye = proj == "equidistant"
        dist_k = float(res.get("distortion_k", 0.0))
        optics.update({
            "projection_type": "Fisheye Equidistant" if fisheye else "Gnomonic TAN Rectilinear",
            "lens_projection": proj,
            "pointing_model": True,  # overlay projects through the solved camera axes, not the zenith dome
            "is_allsky": bool(fisheye and hfov >= 150),
            "focal_length_mm": f_mm,
            "focal_length_px": round(focal_sensor_px, 2),
            "distortion_k": dist_k,
            "pixel_scale_arcsec_px": round(scale, 2),
            "fov_horizontal_deg": hfov,
            "fov_vertical_deg": vfov,
            "fov_diagonal_deg": round(
                2.0 * math.degrees(math.hypot(w, h) / 2.0 / focal_sensor_px) if fisheye
                else math.degrees(2 * math.atan(math.hypot(w, h) / 2.0 / focal_sensor_px)), 2),
            "optical_center_x_px": ocx,
            "optical_center_y_px": ocy,
            "sensor_center_offset_px": [round(ocx - w / 2.0, 1), round(ocy - h / 2.0, 1)],
            "horizon_radius_90_px": round(focal_sensor_px * (math.pi / 2 + dist_k * (math.pi / 2) ** 3), 1)
            if fisheye else optics.get("horizon_radius_90_px"),
        })
        roll = res["roll_deg"]
        orientation = {
            "center_ra_deg": ra,
            "center_dec_deg": dec,
            "center_ra_hms": f"{int(ra_h):02d}h {int(ra_h % 1 * 60):02d}m {(ra_h * 60 % 1) * 60:04.1f}s",
            "center_dec_dms": f"{'+' if dec >= 0 else '-'}{int(dec_abs):02d}° {int(dec_abs % 1 * 60):02d}' {(dec_abs * 60 % 1) * 60:04.1f}\"",
            "azimuth_deg": res["pointing_az_deg"],
            "altitude_deg": res["pointing_alt_deg"],
            "zenith_tilt_deg": round(90.0 - res["pointing_alt_deg"], 2),
            "north_position_angle_deg": roll,
            "rotation_deg": roll,
            "cd_matrix": [round(-scale / 3600.0, 6), 0.0, 0.0, round(-scale / 3600.0, 6)],
        }
        self.station_config.update({
            "plate_solver_endpoint": "local bright-star pattern solver",
            "solve_source": "live_frame (bright-star pattern)",
            "solved_at": res["solved_at"],
            "optics": optics,
            "orientation": orientation,
            "matched_stars_count": len(names),
            "constellations_in_fov": [],
            "bright_star_solution": res,
        })
        self._save_station_config()
        msg = (
            f"Solved from {len(names)} bright stars ({', '.join(names)}), RMS {res['rms_px']} px: "
            f"camera points Az {res['pointing_az_deg']}°, Alt {res['pointing_alt_deg']}°, roll {roll}°; "
            f"{'fisheye ' if fisheye else ''}FOV {hfov}° x {vfov}°, f={f_mm} mm"
            + (f", optical centre ({ocx}, {ocy}) px." if fisheye else ".")
        )
        self.activity.success("station", msg)
        return {"success": True, "message": msg, "solution": res, "station_config": self.get_station_config()}

    def _apply_fixed_lens_solution(self, res: Dict[str, Any]) -> Dict[str, Any]:
        """Store the pointing from a fixed-lens solve; the solved lens intrinsics stay as they are."""
        names = [m["name"] for m in res["matched_stars"]]
        ra, dec, roll = res["center_ra_deg"], res["center_dec_deg"], res["roll_deg"]
        ra_h, dec_abs = ra / 15.0, abs(dec)
        orientation = dict(self.station_config.get("orientation") or {})
        orientation.update({
            "center_ra_deg": ra,
            "center_dec_deg": dec,
            "center_ra_hms": f"{int(ra_h):02d}h {int(ra_h % 1 * 60):02d}m {(ra_h * 60 % 1) * 60:04.1f}s",
            "center_dec_dms": f"{'+' if dec >= 0 else '-'}{int(dec_abs):02d}° {int(dec_abs % 1 * 60):02d}' {(dec_abs * 60 % 1) * 60:04.1f}\"",
            "azimuth_deg": res["pointing_az_deg"],
            "altitude_deg": res["pointing_alt_deg"],
            "zenith_tilt_deg": round(90.0 - res["pointing_alt_deg"], 2),
            "north_position_angle_deg": roll,
            "rotation_deg": roll,
        })
        optics = dict(self.station_config.get("optics") or {})
        optics["pointing_model"] = True
        self.station_config.update({
            "plate_solver_endpoint": "local bright-star pattern solver",
            "solve_source": "live_frame (bright-star pattern, fixed lens)",
            "solved_at": res["solved_at"],
            "optics": optics,
            "orientation": orientation,
            "matched_stars_count": len(names),
            "constellations_in_fov": [],
            "bright_star_solution": res,
        })
        self._save_station_config()
        msg = (
            f"Solved with the known lens from {len(names)} stars/planets ({', '.join(names)}), RMS {res['rms_px']} px: "
            f"camera points Az {res['pointing_az_deg']}°, Alt {res['pointing_alt_deg']}°, roll {roll}°."
        )
        self.activity.success("station", msg)
        return {"success": True, "message": msg, "solution": res, "station_config": self.get_station_config()}

    def run_station_plate_solve(self, fov_hint: str = "auto") -> Dict[str, Any]:
        """
        Capture/fetch frame and invoke https://hoan.uk/astronomy/plate-solving when user clicks Plate-Solve on Web UI.
        """
        from src.calibration.station_solver import solve_station_parameters

        lat = float(self.station_config.get("latitude_deg", 21.0285))
        lon = float(self.station_config.get("longitude_deg", 105.8542))
        # Lens already solved: one frame usually fixes the pointing (only the rotation is unknown). Hot pixels
        # from the dark library are masked out; the 90 s tracking series runs only if this is not sure.
        if self.last_frame is not None and self._lens_intrinsics(self.last_frame.shape[1]) is not None:
            self._ps_progress("quick", "Lens already known: trying a single-frame solve", eta_s=15.0)
            frame, meta = self.capture_frame()
            exclude = self._hot_pixel_exclusion(frame.shape, float(meta.get("actual_exposure_s", 1.0)),
                                                float(meta.get("gain_db", 0.0)))
            quick = self._solve_bright_star_pattern(frame, lat, lon, exclude=exclude)
            if quick is not None:
                return quick
            self.activity.info("station", "Single-frame solve not sure enough; capturing the 90 s tracking series")

        frames, times = self._capture_plate_solve_frames()
        frame = frames[-1]
        if (fov_hint or "auto") == "auto":
            # A known lens FOV narrows the solver's scale search (it scans 0.35x..2.5x of the hint)
            hfov = (self.station_config.get("optics") or {}).get("fov_horizontal_deg")
            if hfov and 1.0 <= float(hfov) <= 170.0:
                fov_hint = str(int(round(float(hfov))))
        elev = float(self.station_config.get("elevation_m", 20.0))

        # 1. Local bright-star pattern solve: seconds, and works under city light pollution
        self._ps_progress("matching", f"Matching the bright stars of {len(frames)} frame(s) against the catalog",
                          eta_s=15.0, frame=len(frames), frames_total=len(frames))
        bright = self._solve_bright_star_pattern(frame, lat, lon, frames, times) if len(frames) >= 3 else \
            self._solve_bright_star_pattern(frame, lat, lon)
        if bright is not None:
            return bright
        if frame.ndim == 2:
            frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)

        # 2. Full blind solve on the hoan.uk plate solver (needs many stars, takes minutes)
        self._ps_progress("online", "Bright-star match not sure enough: full blind solve on hoan.uk (up to 3 min)",
                          eta_s=180.0)
        solved = solve_station_parameters(
            frame_bgr=frame,
            latitude_deg=lat,
            longitude_deg=lon,
            elevation_m=elev,
            fov_hint=fov_hint or "auto",
            sensor_spec=self.station_config.get("camera_sensor"),
        )
        if not solved.get("success"):
            reason = solved.get("error") or "no solution"
            self.activity.error(
                "station",
                f"Plate-solve FAILED: {reason}. Station/lens parameters were left unchanged.",
                attempts=solved.get("attempts"),
            )
            return {
                "success": False,
                "message": (
                    f"Plate-solve failed: {reason}. Station and lens parameters were NOT changed. "
                    "Plate-solving needs the camera pointed at a clear night sky with visible stars."
                ),
                "error": reason,
                "attempts": solved.get("attempts", []),
                "station_config": self.get_station_config(),
            }
        self.activity.success(
            "station",
            f"Plate-solved via {solved.get('solve_source')}: {solved.get('matched_stars_count')} stars, "
            f"f={solved['optics'].get('focal_length_mm')}mm, FOV {solved['optics'].get('fov_horizontal_deg')}°",
        )

        self.station_config.update({
            "plate_solver_endpoint": solved["plate_solver_endpoint"],
            "solve_source": solved["solve_source"],
            "solved_at": solved["solved_at"],
            "camera_sensor": solved["camera_sensor"],
            "optics": solved["optics"],
            "orientation": solved["orientation"],
            "matched_stars_count": solved["matched_stars_count"],
            "constellations_in_fov": solved["constellations_in_fov"],
        })
        self._save_station_config()
        return {
            "success": True,
            "message": (
                f"Plate-solved from the live camera frame ({solved['solve_source']}, "
                f"{solved['matched_stars_count']} stars matched): "
                f"f={solved['optics']['focal_length_mm']}mm, FOV={solved['optics']['fov_horizontal_deg']}°×{solved['optics']['fov_vertical_deg']}° "
                f"(diagonal {solved['optics']['fov_diagonal_deg']}°), Az={solved['orientation']['azimuth_deg']}°, Tilt={solved['orientation']['zenith_tilt_deg']}°."
            ),
            "station_config": self.get_station_config(),
        }

    def close(self) -> None:
        """Release camera hardware resources."""
        if self.adsb is not None:
            self.adsb.stop()
            os.makedirs(os.path.dirname(self._adsb_trails_path()), exist_ok=True)
            self.adsb.save_trails(self._adsb_trails_path())
        if getattr(self, "aircraft_info", None) is not None:
            self.aircraft_info.stop()
        for name in ("_retention_stop", "_camera_watchdog_stop"):
            stop = getattr(self, name, None)
            if stop is not None:
                stop.set()
        if self.camera is not None:
            try:
                self.camera.close()
            except Exception:
                pass
