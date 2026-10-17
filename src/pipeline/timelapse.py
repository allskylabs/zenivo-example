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


def render_metadata_overlay(
    frame: np.ndarray,
    timestamp: Optional[datetime] = None,
    telemetry: Optional[Dict[str, Any]] = None,
    station_name: str = "AllSky Hanoi",
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
    station_name: str = "AllSky Hanoi",
    crf: int = 23,
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
            return cv2.imread(item)
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
