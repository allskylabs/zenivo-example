#!/usr/bin/env python3
"""
Unified CLI Entry Point for IMX335 AllSky Observatory System.
Launches the FastAPI async ASGI server, real-time streaming engine,
and camera capture background service.
"""

import sys
import os
import argparse
import logging
import signal
import time

# Ensure project root is on sys.path
PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

os.environ["ALLSKY_RUNNER"] = "1"


def _tune_malloc() -> None:
    """Reuse large frame buffers instead of mmap/munmap per allocation (each fresh 15 MB frame array
    otherwise costs ~3,700 page faults of kernel zeroing). Measured: ~80x fewer faults, ~half the sys time."""
    try:
        import ctypes

        libc = ctypes.CDLL("libc.so.6")
        libc.mallopt(-3, 256 * 1024 * 1024)  # M_MMAP_THRESHOLD
        libc.mallopt(-1, 512 * 1024 * 1024)  # M_TRIM_THRESHOLD
        # Requests are served from a thread pool, so frames got allocated in up to one 64 MB arena per
        # worker thread, each pinned at its own peak by the trim threshold above (3 x ~37 MB on a Pi 4).
        # One shared arena keeps a single peak; malloc lock contention is nil at a frame every 2 s.
        libc.mallopt(-8, 1)  # M_ARENA_MAX
    except Exception:
        pass


_tune_malloc()

# One worker thread is plenty for a frame every few seconds: OpenCV's default pool (one thread per
# core, TBB on distro builds) spin-waits after every call. Measured per capture cycle: 44 -> 16 ms CPU
# on a 32-core laptop, 88 -> 76 ms on a Pi 4. Override with ALLSKY_CV_THREADS.
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

import cv2

try:
    cv2.setNumThreads(int(os.environ.get("ALLSKY_CV_THREADS", "1")))
except Exception:
    pass

import uvicorn
from src.hardware.driver import get_camera_driver
from src.api.service import AllSkyService
from src.api.main import create_app


def parse_args():
    parser = argparse.ArgumentParser(
        description="AllSky Camera Edge System (IMX335/RTS5869, Raspberry Pi cameras, UVC webcams)."
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Network interface to bind (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="TCP port for Edge Web API and Dashboard (default: 8080)",
    )
    parser.add_argument(
        "--device",
        type=str,
        default=None,
        help="V4L2 camera device node (default: auto-detected RTS5869 or /dev/video0)",
    )
    parser.add_argument(
        "--camera",
        type=str,
        default=os.environ.get("ALLSKY_CAMERA", "auto"),
        choices=["auto", "rts5869", "picamera", "v4l2", "simulator"],
        help="Camera backend: auto (RTS5869 > Pi CSI camera > USB/V4L2 webcam > simulator), "
        "rts5869, picamera (libcamera/Picamera2), v4l2 (any UVC webcam), simulator",
    )
    parser.add_argument(
        "--camera-index",
        type=int,
        default=0,
        help="libcamera camera number for --camera picamera (default: 0)",
    )
    parser.add_argument(
        "--resolution",
        type=str,
        default=None,
        help="Capture size WIDTHxHEIGHT (default: IMX335 2592x1944, Pi camera full-FOV binned mode, webcam max)",
    )
    parser.add_argument(
        "--simulator",
        action="store_true",
        help="Force simulated IMX335 controller fallback (for testing/CI environments)",
    )
    parser.add_argument(
        "--exposure",
        type=float,
        default=0.2,
        help="Initial starting camera exposure duration in seconds before Auto loop takes over (default: 0.2s)",
    )
    parser.add_argument(
        "--gain",
        type=float,
        default=12.0,
        help="Initial starting sensor gain in dB before Auto loop takes over (default: 12.0 dB)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="info",
        choices=["debug", "info", "warning", "error"],
        help="Application logging verbosity (default: info)",
    )
    parser.add_argument(
        "--sync-target",
        type=str,
        default="http://localhost:3005",
        help="Target hoan.uk server URL for live frame/telemetry sync (default: http://localhost:3005)",
    )
    parser.add_argument(
        "--sync-interval",
        type=float,
        default=60.0,
        help="Seconds between frame/telemetry pushes to the sync target (default: 60)",
    )
    parser.add_argument(
        "--sync-max-width",
        type=int,
        default=1600,
        help="Downscale synced frames to at most this width in pixels (default: 1600)",
    )
    parser.add_argument(
        "--min-capture-interval",
        type=float,
        default=None,
        help="Minimum seconds between camera captures while live viewers are connected; "
        "raise it to cut CPU on small hosts. Overrides Settings > Performance for this run "
        "(default: the saved setting, 1.0)",
    )
    parser.add_argument(
        "--prune-dir",
        action="append",
        default=[],
        metavar="PATH:DAYS",
        help="Extra directory to clean: delete dated sub-folders / files older than DAYS "
        "(e.g. the hoan.uk ingest archive). Repeatable.",
    )
    parser.add_argument(
        "--no-access-log",
        action="store_true",
        help="Disable per-request HTTP access logging",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable uvicorn hot reloading for development",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # Configure logging
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format=log_format,
    )
    logger = logging.getLogger("allsky.runner")

    # Persistent, size-rotated logs under data/logs (journald on the Pi is RAM-only)
    from logging.handlers import RotatingFileHandler
    from src.activity import init_activity_log

    log_dir = os.path.join(PROJECT_ROOT, "data", "logs")
    os.makedirs(log_dir, exist_ok=True)
    file_handler = RotatingFileHandler(
        os.path.join(log_dir, "allsky.log"), maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter(log_format))
    for name in ("allsky", "imx335", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.addHandler(file_handler)
        if lg.level == logging.NOTSET or lg.level > logging.INFO:
            lg.setLevel(logging.INFO)
    activity = init_activity_log(log_dir)
    activity.info("system", f"AllSky service starting (camera={args.camera}, port {args.port})")

    logger.info("Initializing IMX335 AllSky Camera System...")
    logger.info(f"Target bind: http://{args.host}:{args.port}")

    # Set environment simulator flag if passed
    if args.simulator:
        os.environ["ALLSKY_SIMULATOR"] = "1"

    # Close any default singleton created during module import before opening hardware
    AllSkyService.reset_instance()

    # Initialize camera driver
    logger.info("Connecting to camera subsystem...")
    size = {}
    if args.resolution:
        w, h = args.resolution.lower().split("x")
        size = {"width": int(w), "height": int(h)}
    camera = get_camera_driver(
        prefer_simulator=args.simulator,
        device_path=args.device,
        camera_type=args.camera,
        camera_index=args.camera_index,
        **size,
    )
    if not getattr(camera, "is_open", False):
        camera.open()

    from src.calibration.auto_exposure import calculate_solar_elevation
    init_exp = args.exposure
    init_gain = args.gain
    if init_exp <= 0.25 and init_gain == 12.0 and calculate_solar_elevation(21.0285, 105.8542) >= -2.0:
        init_exp = 0.0006
        init_gain = 0.0
    logger.info(
        f"Configuring initial exposure to {init_exp:.4f}s, gain {init_gain:.1f} dB..."
    )
    camera.set_hardware_exposure(init_exp, init_gain)

    # Initialize service and FastAPI application
    service = AllSkyService.get_instance(camera=camera)
    if not args.simulator and args.camera != "simulator":
        # Real camera wanted: if it was not usable at startup (permissions not ready at boot, USB unplugged),
        # keep retrying in the background and switch over automatically
        service.start_camera_watchdog(
            dict(device_path=args.device, camera_type=args.camera, camera_index=args.camera_index, **size)
        )
    if args.min_capture_interval is not None:
        service.set_performance(persist=False, min_capture_interval_s=args.min_capture_interval)
    restored = service.restore_camera_settings()
    if restored:
        logger.info(
            f"Restored saved manual exposure {restored['exposure_s']:.4f}s, gain {restored['gain_db']:.1f} dB"
        )
    app = create_app(camera=camera)

    if args.prune_dir:
        extra = []
        for spec in args.prune_dir:
            path, _, days = spec.rpartition(":")
            extra.append({"path": os.path.abspath(path), "keep_days": float(days)})
        service.configure_storage(persist=False, extra_dirs=extra)
        logger.info(f"Extra retention directories: {extra}")

    # Start background sync thread pushing live frames & telemetry to hoan.uk
    stop_sync = False
    if args.sync_target:
        import threading
        from src.sync.client import SyncClient

        import cv2

        def _sync_jpeg() -> bytes:
            frame, _ = service.capture_frame()
            if frame is None or frame.shape[1] <= args.sync_max_width:
                return service.get_snapshot_jpeg()
            h, w = frame.shape[:2]
            small = cv2.resize(frame, (args.sync_max_width, int(h * args.sync_max_width / w)), interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 82])
            return buf.tobytes() if ok else service.get_snapshot_jpeg()

        def _sync_worker():
            client = SyncClient(base_url=args.sync_target)
            failing = False
            ever_ok = False
            ok_count = 0
            last_summary = time.time()
            while not stop_sync:
                try:
                    jpeg_bytes = _sync_jpeg()
                    telem = service.get_telemetry()
                    client.send_frame(
                        jpeg_bytes,
                        exposure_sec=float(telem.get("exposure_s", args.exposure)),
                        gain_db=float(telem.get("gain_db", args.gain)),
                    )
                    client.send_telemetry(telem)
                    ok_count += 1
                    if not ever_ok:
                        ever_ok = True
                        activity.success("sync", f"First frame + telemetry upload to {args.sync_target} OK")
                    if failing:
                        failing = False
                        activity.success("sync", f"Upload to {args.sync_target} recovered")
                except Exception as exc:
                    if not failing:
                        activity.error("sync", f"Upload to {args.sync_target} failed: {exc}")
                    else:
                        activity.warning("sync", f"Upload still failing: {exc}")
                    failing = True
                if time.time() - last_summary >= 3600:
                    if ok_count:
                        activity.info("sync", f"{ok_count} frame + telemetry uploads to {args.sync_target} in the last hour")
                    ok_count = 0
                    last_summary = time.time()
                for _ in range(max(1, int(args.sync_interval / 0.25))):
                    if stop_sync:
                        break
                    time.sleep(0.25)

        sync_thread = threading.Thread(target=_sync_worker, daemon=True)
        sync_thread.start()
        logger.info(f"Background sync active -> {args.sync_target}")

    logger.info("Starting AllSky Observatory Web Server (FastAPI + Uvicorn)...")
    try:
        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            log_level=args.log_level,
            access_log=not args.no_access_log,
            # Live packets are mostly JPEG already: per-message deflate only burns CPU on both ends
            ws_per_message_deflate=False,
            proxy_headers=True,
            forwarded_allow_ips="*",
        )
    except (KeyboardInterrupt, SystemExit):
        logger.info("Received termination signal. Shutting down AllSky service...")
    except Exception as exc:
        activity.error("system", f"Web server crashed: {exc}")
        raise
    finally:
        activity.info("system", "AllSky service stopped")
        stop_sync = True
        service.close()
        logger.info("Clean shutdown complete.")


if __name__ == "__main__":
    main()
