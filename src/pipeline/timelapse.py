"""
Timelapse Video Compilation Pipeline for IMX335 AllSky Observatory.
Compiles sequential astronomical frames into video with telemetry & timestamp metadata overlay,
providing dual-path encoding: preferred FFmpeg with automatic fallback to OpenCV VideoWriter.
"""

import glob
import os
import shutil
import subprocess
import time
from datetime import datetime
from typing import List, Optional, Tuple, Union, Dict, Any
import numpy as np
import cv2
from src.storage import frame_codec
from src.defaults import DEFAULT_STATION_NAME


def render_metadata_overlay(
    frame: np.ndarray,
    timestamp: Optional[datetime] = None,
    telemetry: Optional[Dict[str, Any]] = None,
    station_name: str = DEFAULT_STATION_NAME,
    banner_height: int = 32,
) -> np.ndarray:
    """
    Render a clean telemetry and timestamp overlay banner onto an allsky frame.

    Args:
        frame: Input image (H, W, 3) BGR.
        timestamp: Observation timestamp.
        telemetry: Optional dictionary containing exposure, gain, star count, etc.
        station_name: Name of the observing station.
        banner_height: Height of the overlay banner in pixels.

    Returns:
        Annotated image copy.
    """
    annotated = frame.copy()
    h, w = annotated.shape[:2]

    # Draw bottom banner box
    cv2.rectangle(
        annotated,
        (0, h - banner_height),
        (w, h),
        (0, 0, 0),
        cv2.FILLED,
    )

    # Format text parts
    ts_str = (timestamp or datetime.now()).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    parts = [station_name, ts_str]

    if telemetry:
        if "exposure_s" in telemetry:
            exp_val = float(telemetry["exposure_s"])
            if exp_val < 0.01:
                parts.append(f"Exp: {exp_val * 1000.0:.2f}ms")
            elif exp_val < 0.1:
                parts.append(f"Exp: {exp_val * 1000.0:.1f}ms")
            else:
                parts.append(f"Exp: {exp_val:.1f}s")
        if "gain_db" in telemetry:
            parts.append(f"Gain: {telemetry['gain_db']:.0f}dB")
        if "star_count" in telemetry:
            parts.append(f"Stars: {telemetry['star_count']}")
        if "cloud_cover_pct" in telemetry:
            parts.append(f"Clouds: {telemetry['cloud_cover_pct']:.0f}%")
        elif "cloud_cover" in telemetry:
            parts.append(f"Clouds: {telemetry['cloud_cover']:.0f}%")
        if telemetry.get("events"):  # only when the caller asks for the overlay (custom video); stored frames stay clean
            parts.append("Event: " + ", ".join(sorted(set(map(str, telemetry["events"])))))

    banner_text = " | ".join(parts)

    cv2.putText(
        annotated,
        banner_text,
        (12, h - 10),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.42,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )

    return annotated


_HW_ENCODER_OK: Optional[bool] = None  # h264_v4l2m2m (Raspberry Pi VideoCore) usable?
_HW_ENCODER_CHECKED = 0.0
_HW_RETRY_S = 6 * 3600.0  # a refused hardware encoder (e.g. CMA memory exhausted) is retried this rarely


def _hw_encoder_usable(ffmpeg_bin: str) -> bool:
    """Probe h264_v4l2m2m with a tiny real encode: it can be listed yet unusable when CMA memory is short."""
    global _HW_ENCODER_OK, _HW_ENCODER_CHECKED
    now = time.time()
    if _HW_ENCODER_OK is not None and (_HW_ENCODER_OK or now - _HW_ENCODER_CHECKED < _HW_RETRY_S):
        return _HW_ENCODER_OK
    _HW_ENCODER_CHECKED = now
    ok = False
    if os.path.exists("/dev/video11"):
        try:
            res = subprocess.run(
                [ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=size=64x64:rate=4",
                 "-frames:v", "2", "-c:v", "h264_v4l2m2m", "-f", "null", "-"],
                capture_output=True, timeout=15,
            )
            ok = res.returncode == 0
        except Exception:
            ok = False
    _HW_ENCODER_OK = ok
    return ok


def _mark_hw_encoder_failed() -> None:
    global _HW_ENCODER_OK, _HW_ENCODER_CHECKED
    _HW_ENCODER_OK, _HW_ENCODER_CHECKED = False, time.time()


def _ffmpeg_video_args(ffmpeg_bin: str, crf: int, allow_hw: bool = True) -> List[str]:
    """Cheapest encoder that still yields browser-playable H.264: Pi hardware encoder, else x264 veryfast."""
    if allow_hw and _hw_encoder_usable(ffmpeg_bin):
        return ["-c:v", "h264_v4l2m2m", "-b:v", "6M"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-threads", "2", "-crf", str(crf)]


def compile_timelapse(
    frames_or_paths: List[Union[np.ndarray, str]],
    output_path: str,
    fps: float = 24.0,
    fourcc_str: str = "avc1",
    add_overlay: bool = False,
    telemetry_list: Optional[List[Dict[str, Any]]] = None,
    timestamps: Optional[List[datetime]] = None,
    station_name: str = DEFAULT_STATION_NAME,
    crf: int = 23,
    target_size: Optional[tuple] = None,
) -> str:
    """
    Compile a sequence of frames or image paths into an MP4 video file.
    Attempts hardware/FFmpeg encoding if ffmpeg binary exists, otherwise falls back
    to OpenCV VideoWriter (preferring browser-compatible H.264 'avc1').

    Args:
        frames_or_paths: List of numpy image arrays or file paths.
        output_path: Target video file path (.mp4).
        fps: Playback frame rate (default: 24.0).
        fourcc_str: OpenCV fourcc codec string (default: "avc1").
        add_overlay: Whether to apply metadata overlay banner on frames.
        telemetry_list: Optional list of telemetry dicts matching each frame.
        timestamps: Optional list of timestamps matching each frame.
        station_name: Station identifier for banner.
        crf: Constant Rate Factor for FFmpeg encoding (default: 23).
        target_size: Optional (width, height) to scale every frame to (default: first frame size).

    Returns:
        Path to generated video file.
    """
    if not frames_or_paths:
        raise ValueError("Cannot compile timelapse from empty frame sequence")

    abs_out = os.path.abspath(output_path)
    out_dir = os.path.dirname(abs_out)
    os.makedirs(out_dir, exist_ok=True)
    base_name, ext = os.path.splitext(os.path.basename(abs_out))
    tmp_path = os.path.join(out_dir, f".{base_name}_tmp_{os.getpid()}{ext or '.mp4'}")
    # Temp files left by a killed or crashed earlier process (other pid) are never finished: remove them
    for stale in glob.glob(os.path.join(out_dir, f".{glob.escape(base_name)}_tmp_*")):
        if stale != tmp_path:
            try:
                os.remove(stale)
            except OSError:
                pass

    # Determine dimensions and prepare first frame
    def load_frame(item: Union[np.ndarray, str]) -> Optional[np.ndarray]:
        if isinstance(item, str):
            # A truncated JPEG (power cut, full disk) is skipped rather than failing the whole timelapse
            return frame_codec.read_frame(item)
        elif isinstance(item, np.ndarray):
            if item.ndim == 2:
                return cv2.cvtColor(item, cv2.COLOR_GRAY2BGR)
            return item
        else:
            raise TypeError(f"Unsupported frame type: {type(item)}")

    first_frame = next((f for f in (load_frame(it) for it in frames_or_paths) if f is not None), None)
    if first_frame is None:
        raise ValueError("Cannot compile timelapse: none of the frames could be read")
    h, w = first_frame.shape[:2]
    if target_size:
        w, h = int(target_size[0]), int(target_size[1])

    def prepared_frames():
        for i, item in enumerate(frames_or_paths):
            f = load_frame(item)
            if f is None:
                continue
            if f.shape[:2] != (h, w):
                f = cv2.resize(f, (w, h), interpolation=cv2.INTER_AREA)
            if add_overlay:
                t = timestamps[i] if timestamps and i < len(timestamps) else None
                tel = telemetry_list[i] if telemetry_list and i < len(telemetry_list) else None
                f = render_metadata_overlay(f, timestamp=t, telemetry=tel, station_name=station_name)
            yield f

    # Check for FFmpeg in system PATH
    ffmpeg_bin = shutil.which("ffmpeg")
    ffmpeg_success = False

    if ffmpeg_bin is not None:
        # Hardware encoder first when usable; if it refuses this stream, x264 straight away (never in-process)
        for allow_hw in (True, False):
            video_args = _ffmpeg_video_args(ffmpeg_bin, crf, allow_hw=allow_hw)
            hw = "h264_v4l2m2m" in video_args
            proc = None
            try:
                cmd = [
                    *(["nice", "-n", "15"] if shutil.which("nice") else []),
                    ffmpeg_bin,
                    "-y",
                    "-loglevel", "error",
                    "-f", "rawvideo",
                    "-vcodec", "rawvideo",
                    "-s", f"{w}x{h}",
                    "-pix_fmt", "bgr24",
                    "-r", str(fps),
                    "-i", "-",
                    *video_args,
                    "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart",
                    tmp_path,
                ]
                proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                for f in prepared_frames():
                    proc.stdin.write(f.tobytes())
                proc.stdin.close()
                proc.wait(timeout=300)
                ffmpeg_success = proc.returncode == 0 and os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 100
            except Exception:
                ffmpeg_success = False
                if proc is not None:
                    try:
                        proc.stdin.close()  # ffmpeg already exited: drop the unwritten buffer quietly
                    except Exception:
                        pass
                    if proc.poll() is None:
                        proc.kill()
                    proc.wait(timeout=10)
            if ffmpeg_success:
                break
            if hw:
                _mark_hw_encoder_failed()  # refused this stream: x264 until the next probe
            elif allow_hw:
                break  # already x264; a second identical attempt would fail the same way

    if not ffmpeg_success:
        # Fallback to OpenCV VideoWriter (prefer avc1 H.264 for HTML5 browser playback)
        codecs_to_try = [fourcc_str]
        for alt in ["avc1", "mp4v", "XVID", "MJPG"]:
            if alt not in codecs_to_try:
                codecs_to_try.append(alt)
        if os.path.exists("/dev/video11"):
            # On a Pi OpenCV's avc1 is the VideoCore encoder inside this process: a refused stream there spams the
            # kernel log and can strand CMA memory for the life of the service
            codecs_to_try = [c for c in codecs_to_try if c != "avc1"]

        # OpenCV's built-in encoder cannot take a preset; cap it to 2 threads so a refresh does not light up
        # every core at once (same CPU-seconds, far lower peak load and temperature)
        prev_threads = cv2.getNumThreads()
        cv2.setNumThreads(2)
        writer = None
        for codec in codecs_to_try:
            fourcc = cv2.VideoWriter_fourcc(*codec)
            candidate = cv2.VideoWriter(tmp_path, fourcc, float(fps), (w, h))
            if candidate.isOpened():
                writer = candidate
                break

        if writer is None or not writer.isOpened():
            cv2.setNumThreads(prev_threads)
            raise RuntimeError(f"OpenCV VideoWriter failed to open for {output_path}")

        try:
            for f in prepared_frames():
                writer.write(f)
        finally:
            writer.release()
            cv2.setNumThreads(prev_threads)

    if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
        raise RuntimeError(f"Timelapse compilation produced an empty or missing file: {output_path}")

    os.replace(tmp_path, abs_out)
    return output_path


def frame_quality_mask(
    paths: List[str],
    expected_hw: Optional[Tuple[int, int]] = None,
    window: int = 7,
    rel_tol: float = 0.3,
    abs_tol: float = 20.0,
    telemetry_map: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[bool]:
    """
    Which archived frames are fit for the video (True) and which are skipped (False).

    A frame is skipped when its size is not the archive size (a different camera or mode was
    running), or when its mean brightness jumps away from the median of its neighbours
    (a black or saturated glitch frame). Neighbours are taken in time order, so a slow dusk or
    dawn ramp is followed and only single-frame or block glitches are removed. When telemetry_map
    carries precomputed frame brightness, only the image header is read for dimensions; otherwise
    frames are read at 1/4 scale. Skipped frames stay on disk; only the video leaves them out.
    """
    from PIL import Image

    raw_shapes: List[Optional[Tuple[int, int]]] = []
    raw_means: List[Optional[float]] = []
    shape_counts: Dict[Tuple[int, int], int] = {}
    for p in paths:
        rec = None
        if telemetry_map:
            fname = os.path.basename(p)
            rec = telemetry_map.get(fname) or telemetry_map.get(
                f"{os.path.basename(os.path.dirname(p))}/{fname}"
            )
        b_val = rec.get("brightness") if isinstance(rec, dict) else None
        if b_val is not None:
            try:
                with Image.open(p) as im:
                    w_px, h_px = im.size
                sh = ((int(h_px) + 3) // 4, (int(w_px) + 3) // 4)
                raw_shapes.append(sh)
                raw_means.append(float(b_val))
                shape_counts[sh] = shape_counts.get(sh, 0) + 1
                continue
            except Exception:
                pass
        im = frame_codec.read_frame(p, cv2.IMREAD_REDUCED_GRAYSCALE_4)
        if im is not None and im.size > 0:
            sh = (int(im.shape[0]), int(im.shape[1]))
            raw_shapes.append(sh)
            raw_means.append(float(im.mean()))
            shape_counts[sh] = shape_counts.get(sh, 0) + 1
        else:
            raw_shapes.append(None)
            raw_means.append(None)

    if expected_hw is not None:
        reduced_hw: Optional[Tuple[int, int]] = ((expected_hw[0] + 3) // 4, (expected_hw[1] + 3) // 4)
    elif shape_counts:
        default_reduced = ((972 + 3) // 4, (1296 + 3) // 4)
        reduced_hw = max(
            shape_counts.keys(),
            key=lambda sh: (shape_counts[sh], 1 if sh == default_reduced else 0, sh[0] * sh[1]),
        )
    else:
        reduced_hw = ((972 + 3) // 4, (1296 + 3) // 4)

    means: List[Optional[float]] = [
        m if (m is not None and sh == reduced_hw) else None
        for sh, m in zip(raw_shapes, raw_means)
    ]

    keep: List[bool] = []
    for i, m in enumerate(means):
        if m is None:
            keep.append(False)
            continue
        near = [means[j] for j in range(max(0, i - window), min(len(means), i + window + 1))
                if means[j] is not None]
        med = float(np.median(near))
        keep.append(abs(m - med) <= max(abs_tol, rel_tol * med))
    return keep


def _select_sampled_frames(
    paths: List[str],
    max_frames: int = 600,
    telemetry_map: Optional[Dict[str, Dict[str, Any]]] = None,
    window: int = 7,
    rel_tol: float = 0.3,
    abs_tol: float = 20.0,
) -> Tuple[List[str], int]:
    """
    Uniformly sample up to ``max_frames`` quality-checked frames across the full ``paths``
    sequence (dawn -> daytime -> dusk -> night) instead of taking only the tail.

    Brightness neighbour filtering runs on the full un-strided sequence first so dawn/dusk
    ramps keep their true frame-to-frame step size; dimension checks then run on the sampled pool.
    Returns ``(selected_paths, rejected_count)``.
    """
    if not paths:
        return [], 0

    if len(paths) <= max_frames:
        keep = frame_quality_mask(
            paths, window=window, rel_tol=rel_tol, abs_tol=abs_tol, telemetry_map=telemetry_map
        )
        kept = [p for p, ok in zip(paths, keep) if ok]
        return kept, len(paths) - len(kept)

    # 1. Full-resolution brightness glitch rejection using telemetry_map when available
    pre_rejected = 0
    candidates = list(paths)
    if telemetry_map:
        means: List[Optional[float]] = []
        for p in paths:
            fname = os.path.basename(p)
            rec = telemetry_map.get(fname) or telemetry_map.get(
                f"{os.path.basename(os.path.dirname(p))}/{fname}"
            )
            b = rec.get("brightness") if isinstance(rec, dict) else None
            means.append(float(b) if b is not None else None)
        if any(m is not None for m in means):
            b_keep: List[bool] = []
            for i, m in enumerate(means):
                if m is None:
                    b_keep.append(True)
                    continue
                near = [
                    means[j]
                    for j in range(max(0, i - window), min(len(means), i + window + 1))
                    if means[j] is not None
                ]
                med = float(np.median(near))
                b_keep.append(abs(m - med) <= max(abs_tol, rel_tol * med))
            candidates = [p for p, ok in zip(paths, b_keep) if ok]
            pre_rejected = len(paths) - len(candidates)

    if not candidates:
        return [], len(paths)

    # 2. Uniformly sample across the full day and verify frame dimensions/readability
    pool_limit = min(len(candidates), max_frames + 32)
    idx = np.linspace(0, len(candidates) - 1, pool_limit, dtype=int)
    pool = [candidates[int(i)] for i in idx]

    # Only check shape consistency on the strided pool (rel_tol=1e9 skips re-running strided brightness diff)
    keep = frame_quality_mask(
        pool,
        window=window,
        rel_tol=1e9 if telemetry_map else rel_tol,
        abs_tol=1e9 if telemetry_map else abs_tol,
        telemetry_map=telemetry_map,
    )
    kept = [p for p, ok in zip(pool, keep) if ok]
    rejected_count = pre_rejected + (len(pool) - len(kept))

    if len(kept) > max_frames:
        idx2 = np.linspace(0, len(kept) - 1, max_frames, dtype=int)
        selected = [kept[int(i)] for i in idx2]
    else:
        selected = kept
    return selected, rejected_count


def _event_frame(p: str, telemetry_map: Optional[Dict[str, Dict[str, Any]]]) -> bool:
    if not telemetry_map:
        return False
    fname = os.path.basename(p)
    rec = telemetry_map.get(fname) or telemetry_map.get(f"{os.path.basename(os.path.dirname(p))}/{fname}")
    return bool(isinstance(rec, dict) and rec.get("events"))


def select_timelapse_frames(
    paths: List[str],
    max_frames: int = 600,
    telemetry_map: Optional[Dict[str, Dict[str, Any]]] = None,
    **kwargs: Any,
) -> Tuple[List[str], int]:
    """
    ``_select_sampled_frames`` plus every frame archived for a sky event (meteor, aircraft, ...):
    the quiet stretches are sped up by sampling, the events are always kept.
    """
    selected, rejected = _select_sampled_frames(paths, max_frames, telemetry_map, **kwargs)
    events = [p for p in paths if _event_frame(p, telemetry_map)]
    if not events:
        return selected, rejected
    keep = set(selected) | set(events)
    merged = [p for p in paths if p in keep]
    return merged, max(0, len(paths) - len(merged))
