"""Archive, keogram/startrails/timelapse products and the gallery.

Mixin of AllSkyService (src/api/service.py): methods only, all state lives on the service instance."""

import os
import re
import shutil
import time
import threading
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
import numpy as np
import cv2
from src.pipeline.startrails import StartrailsAccumulator, StartrailsFrameGate
from src.pipeline.timelapse import compile_timelapse, select_timelapse_frames
from src.storage import frame_codec
from src.pipeline.overlay import stamp_info_box
from src.api.service_consts import ARCHIVE_WIDTH

class ProductsMixin:

    def _obs_day_str(self, dt: Optional[datetime] = None) -> str:
        """Observing-day key for frames / keogram / startrails / timelapse.

        The day rolls over at dawn (civil twilight, sun above -6 deg in the morning) instead of midnight,
        so one whole night, evening to sunrise, lands in a single uninterrupted product set."""
        dt = dt or datetime.now().astimezone()
        day = dt.date()
        try:
            if dt.hour < 12 and self._startrails_sun_elev(dt) < -6.0:
                day = day - timedelta(days=1)
        except Exception:
            pass
        return day.strftime("%Y-%m-%d")

    def _get_today_frames_dir(self, date_str: Optional[str] = None) -> str:
        """Return directory path for today's archived timelapse frames."""
        d_str = date_str or self._obs_day_str()
        day_dir = os.path.join(self.frames_archive_root, d_str)
        os.makedirs(day_dir, exist_ok=True)
        return day_dir

    def _list_today_frame_paths(self, date_str: Optional[str] = None) -> List[str]:
        """Return sorted list of archived frame JPEG paths for the given local date."""
        day_dir = self._get_today_frames_dir(date_str)
        if not os.path.isdir(day_dir):
            return []
        return [os.path.join(day_dir, f) for f in frame_codec.list_frames(day_dir)]

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
            today_str = self._obs_day_str()
            self._archived_date_str = today_str
            paths = self._list_today_frame_paths(today_str)
            if not paths:
                return
            meta_map = self._load_today_telemetry_records(today_str)

            def _ts(p: str) -> datetime:
                ts_iso = meta_map.get(os.path.basename(p), {}).get("timestamp")
                return datetime.fromisoformat(ts_iso) if ts_iso else datetime.fromtimestamp(os.path.getmtime(p)).astimezone()

            last = frame_codec.read_frame(paths[-1])
            if last is None:
                return
            fh, fw = last.shape[:2]
            self.keogram_gen.reset()
            self.startrails_acc.reset()
            self.startrails_gate.reset()
            keo_ok = self._restore_keogram_from_jpeg(today_str, paths, fh, _ts)
            trails_ok = self._restore_startrails_from_jpeg(today_str, (fh, fw))
            if not keo_ok:
                keo_paths = (
                    [paths[int(i)] for i in np.linspace(0, len(paths) - 1, 600, dtype=int)]
                    if len(paths) > 600
                    else paths
                )
                for p in keo_paths:
                    img = last if p == paths[-1] else frame_codec.read_frame(p)
                    if img is not None:
                        self.keogram_gen.add_frame(img, timestamp=_ts(p))
            if not trails_ok:
                # Rebuilt off to the side (live archiving keeps running meanwhile) and swapped in;
                # only night frames (by their timestamp) are decoded, the gate rejects the rest
                gate, acc = StartrailsFrameGate(), StartrailsAccumulator(comet_mode=self.startrails_acc.comet_mode)
                bw, bh = stamp_info_box(fw)
                for p in paths:
                    sun = self._startrails_sun_elev(_ts(p))
                    if sun > gate.max_sun_elev:
                        continue
                    img = last if p == paths[-1] else frame_codec.read_frame(p)
                    if img is None or img.shape[:2] != (fh, fw):
                        continue
                    if meta_map.get(os.path.basename(p), {}).get("stamped", True):
                        img[:bh, :bw] = 0  # older archived JPEGs carry the info stamp; keep it out of the stack
                    if gate.check(img, sun) is None:
                        acc.add_frame(img)
                self.startrails_gate, self.startrails_acc = gate, acc
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

    def _restore_startrails_from_jpeg(self, today_str: str, shape: Tuple[int, int]) -> bool:
        """Reload the dated composite and its gate counts; a composite without the sidecar predates
        the frame gate (it may hold daylight frames) and is rebuilt from the archive instead."""
        import json

        if self.startrails_acc.comet_mode:
            return False
        try:
            with open(os.path.join(self.startrails_dir, f"{today_str}.json"), encoding="utf-8") as fp:
                stats = json.load(fp)
        except (OSError, ValueError):
            return False
        img = cv2.imread(os.path.join(self.startrails_dir, f"{today_str}.jpg"), cv2.IMREAD_COLOR)
        if img is None or img.shape[:2] != shape:
            return False
        self.startrails_acc.composite = img
        self.startrails_acc.frame_count = int(stats.get("accepted", 0))
        self.startrails_gate.accepted = self.startrails_acc.frame_count
        self.startrails_gate.rejected = {k: int(v) for k, v in (stats.get("rejected") or {}).items()}
        return True

    def _startrails_sun_elev(self, dt: Optional[datetime] = None) -> float:
        from src.calibration.auto_exposure import calculate_solar_elevation

        return float(calculate_solar_elevation(self.auto_exposure.lat, self.auto_exposure.lon, dt))

    def _save_startrails_stats(self, today_str: str) -> None:
        import json

        stats = {"accepted": self.startrails_acc.frame_count, "rejected": self.startrails_gate.rejected}
        tmp = os.path.join(self.startrails_dir, ".stats.json.tmp")
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump(stats, fp)
        os.replace(tmp, os.path.join(self.startrails_dir, f"{today_str}.json"))

    def _save_products(self, today_str: str, startrails: bool = True) -> None:
        """Encode keogram and startrails once each (dated file) and copy them to latest.jpg."""
        targets = [(lambda p: self.keogram_gen.save(p, add_timestamps=True), self.keograms_dir)]
        if startrails:
            targets.append((self.startrails_acc.save, self.startrails_dir))
        for save, out_dir in targets:
            dated = os.path.join(out_dir, f"{today_str}.jpg")
            if save(dated):
                tmp = os.path.join(out_dir, ".latest.jpg.tmp")
                shutil.copyfile(dated, tmp)
                os.replace(tmp, os.path.join(out_dir, "latest.jpg"))
        if startrails:
            self._save_startrails_stats(today_str)

    def _maybe_archive_frame_and_update_products(
        self, frame: np.ndarray, meta: Dict[str, Any], force: bool = False, events: Optional[List[str]] = None
    ) -> Optional[str]:
        """
        Periodically archive a downscaled 1296x972 frame to data/frames/<YYYY-MM-DD>/,
        update real-time Keogram and Startrails, and trigger background H.264 Timelapse compilation.
        """
        import json

        if frame is None or frame.size == 0:
            return None

        now = time.time()
        today_str = self._obs_day_str()
        if today_str != self._archived_date_str:
            prev_day = self._archived_date_str
            n_prev = len(self._list_today_frame_paths(prev_day))
            self._archived_date_str = today_str
            self.keogram_gen.reset()
            self.startrails_acc.reset()
            self.startrails_gate.reset()
            self.activity.success("archive", f"Day {prev_day} closed with {n_prev} archived frames; started {today_str}")
            if n_prev >= 4:
                self._trigger_background_timelapse_compile(fps=12.0, date_str=prev_day)

        existing_paths = self._list_today_frame_paths(today_str)
        # Fast initial warm-up (every 2.5s for first 15 frames), then every 15s for continuous all-day/night timelapse
        interval_s = 2.5 if len(existing_paths) < 15 else float(self.retention.policy["archive_interval_s"])
        if not force and (now - self._last_archive_ts) < interval_s:
            return None

        self._last_archive_ts = now
        now_dt = datetime.now().astimezone()

        # Standardize the archive width for fast H.264 compilation
        h, w = frame.shape[:2]
        if w != ARCHIVE_WIDTH:
            target_w = ARCHIVE_WIDTH
            target_h = int(round(h * (ARCHIVE_WIDTH / w)))
            if target_h % 2 == 1:
                target_h += 1
            interp = cv2.INTER_AREA if w > target_w else cv2.INTER_LINEAR
            arch_frame = cv2.resize(frame, (target_w, target_h), interpolation=interp)
        else:
            arch_frame = frame.copy()

        day_dir = self._get_today_frames_dir(today_str)
        stem = f"frame_{now_dt.strftime('%Y%m%d_%H%M%S_%f')[:19]}"
        # Frames are stored clean (AVIF, JPEG fallback); time / exposure / gain / brightness live in telemetry.jsonl
        fpath = frame_codec.write_frame(os.path.join(day_dir, stem), arch_frame)
        if not fpath:
            self.activity.error("archive", f"Could not write archived frame {stem} (disk full or read-only?)")
            return None
        fname = os.path.basename(fpath)

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
            "brightness": self.last_brightness,
            "stamped": False,
        }
        if events:
            rec["events"] = events
        try:
            with open(os.path.join(day_dir, "telemetry.jsonl"), "a", encoding="utf-8") as fp:
                fp.write(json.dumps(rec) + "\n")
        except Exception:
            pass

        total_frames = len(existing_paths) + 1  # no per-day cap: every event keeps its frame

        # Update Keogram & Startrails
        try:
            self.keogram_gen.add_frame(arch_frame, timestamp=now_dt)
            # A flash frame (lightning, headlight) is exactly what the gate keeps out; other events are kept
            relaxed = bool(events) and "flash" not in events
            trail_ok = self.startrails_gate.check(arch_frame, self._startrails_sun_elev(now_dt), relaxed=relaxed) is None
            if trail_ok:
                self.startrails_acc.add_frame(arch_frame)
            self._save_products(today_str, startrails=trail_ok)
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

    def _trigger_background_timelapse_compile(
        self, fps: float = 12.0, date_str: Optional[str] = None
    ) -> None:
        """Spawn a non-blocking daemon thread to compile the H.264 MP4 timelapse."""
        if self._timelapse_compiling and date_str is None:
            return

        def _worker():
            t0 = time.time()
            try:
                res = self.generate_observational_products(
                    fps=fps, ensure_min_frames=False, date_str=date_str
                )
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
        self, fps: float = 12.0, ensure_min_frames: bool = True, date_str: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Compile Timelapse (.mp4 H.264), Keogram (.jpg), and Startrails (.jpg) from an observing
        day's archived frames (defaults to today). Uniformly samples up to 600 frames across the
        full day (dawn -> daytime -> sunset -> night) so daytime footage is never dropped.
        """
        current_obs_day = self._obs_day_str()
        target_day = date_str or current_obs_day
        is_today = target_day == current_obs_day
        paths = self._list_today_frame_paths(target_day)

        if is_today and ensure_min_frames and len(paths) < 4:
            needed = 4 - len(paths)
            for _ in range(needed):
                if self.last_frame is not None:
                    self._maybe_archive_frame_and_update_products(self.last_frame, self.last_metadata, force=True)
                try:
                    f, m = self.capture_frame()
                    self._maybe_archive_frame_and_update_products(f, m, force=True)
                except Exception:
                    break
            paths = self._list_today_frame_paths(target_day)

        if is_today and not paths and self.last_frame is not None:
            self._maybe_archive_frame_and_update_products(self.last_frame, self.last_metadata, force=True)
            paths = self._list_today_frame_paths(target_day)

        if not paths:
            raise RuntimeError("No frames available to compile timelapse")

        with self._timelapse_lock:
            self._timelapse_compiling = True
            try:
                meta_map = self._load_today_telemetry_records(target_day)
                # Up to 600 frames uniformly sampled across the full observing day that pass the
                # quality check (wrong-size or brightness-glitch frames would flicker the video)
                selected_paths, rejected_count = select_timelapse_frames(
                    paths, max_frames=600, telemetry_map=meta_map
                )
                if not selected_paths:
                    raise RuntimeError("No frames passed the quality check for the timelapse")
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
                # Quiet stretches are sampled (fast forward); each event frame is held for ~1 s so it can be seen
                hold = max(2, int(round(float(fps))))
                reps = [hold if tl.get("events") else 1 for tl in telemetry_list]
                if any(r > 1 for r in reps):
                    compile_paths = [p for p, r in zip(selected_paths, reps) for _ in range(r)]
                    compile_ts = [t for t, r in zip(timestamps, reps) for _ in range(r)]
                    compile_telem = [tl for tl, r in zip(telemetry_list, reps) for _ in range(r)]
                if len(compile_paths) < 12:
                    repeat = max(2, int(np.ceil(16 / max(1, len(compile_paths)))))
                    compile_paths = [p for p in selected_paths for _ in range(repeat)]
                    compile_ts = [t for t in timestamps for _ in range(repeat)]
                    compile_telem = [tl for tl in telemetry_list for _ in range(repeat)]

                station_name = str(self.station_config.get("location_name", "AllSky Hanoi"))
                latest_mp4 = os.path.join(self.timelapses_dir, "latest.mp4")
                dated_mp4 = os.path.join(self.timelapses_dir, f"{target_day}.mp4")
                out_mp4 = latest_mp4 if is_today else dated_mp4

                compile_timelapse(
                    compile_paths,
                    output_path=out_mp4,
                    fps=float(fps),
                    fourcc_str="avc1",
                    add_overlay=False,
                    telemetry_list=compile_telem,
                    timestamps=compile_ts,
                    station_name=station_name,
                )
                self._write_timelapse_info(
                    target_day, float(fps), compile_ts, compile_telem, update_latest=is_today
                )
                if is_today:
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

                mp4_size_kb = round(os.path.getsize(out_mp4) / 1024.0, 1) if os.path.isfile(out_mp4) else 0.0
                duration_s = round(len(compile_paths) / max(1.0, float(fps)), 1)
                return {
                    "success": True,
                    "date": target_day,
                    "archived_frames_count": len(paths),
                    "rejected_frames_count": rejected_count,
                    "video_frames_count": len(compile_paths),
                    "fps": float(fps),
                    "duration_s": duration_s,
                    "size_kb": mp4_size_kb,
                    "timelapse_url": "/uploads/allsky/timelapses/latest.mp4" if is_today else f"/uploads/allsky/timelapses/{target_day}.mp4",
                    "keogram_url": "/uploads/allsky/keograms/latest.jpg",
                    "startrails_url": "/uploads/allsky/startrails/latest.jpg",
                    "updated_at": datetime.now().astimezone().isoformat(),
                }
            finally:
                self._timelapse_compiling = False

    def build_range_timelapse(
        self, date_from: str, date_to: str, fps: float = 24.0, width: int = 0, overlay: bool = False
    ) -> Dict[str, Any]:
        """Compile archived frames of observing days date_from..date_to (YYYY-MM-DD, inclusive) into
        timelapses/custom.mp4. width=0 keeps the archived frame size; otherwise frames are scaled to that
        width at the same aspect ratio."""
        d0 = datetime.strptime(date_from, "%Y-%m-%d").date()
        d1 = datetime.strptime(date_to, "%Y-%m-%d").date()
        if d1 < d0 or (d1 - d0).days > 30:
            raise ValueError("Date range must be 1 to 31 days, From before To")
        paths: List[str] = []
        telem_map: Dict[str, Dict[str, Any]] = {}
        d = d0
        while d <= d1:
            ds = d.strftime("%Y-%m-%d")
            if os.path.isdir(os.path.join(self.frames_archive_root, ds)):
                paths += self._list_today_frame_paths(ds)
                for fname, rec in self._load_today_telemetry_records(ds).items():
                    telem_map[f"{ds}/{fname}"] = rec
            d += timedelta(days=1)
        if not paths:
            raise RuntimeError("No archived frames in the selected range")
        sel, _ = select_timelapse_frames(paths, max_frames=1500, telemetry_map=telem_map)
        if not sel:
            raise RuntimeError("No frames passed the quality check for the timelapse")
        stamps: List[datetime] = []
        telem: List[Dict[str, Any]] = []
        for p in sel:
            rec = telem_map.get(f"{os.path.basename(os.path.dirname(p))}/{os.path.basename(p)}", {})
            try:
                ts = datetime.fromisoformat(rec["timestamp"]) if rec.get("timestamp") else datetime.fromtimestamp(os.path.getmtime(p)).astimezone()
            except Exception:
                ts = datetime.now().astimezone()
            stamps.append(ts)
            telem.append(rec)
        size = None
        if width:
            import cv2
            img = frame_codec.read_frame(sel[0])
            if img is not None:
                h0, w0 = img.shape[:2]
                size = (int(width) // 2 * 2, int(round(int(width) * h0 / w0 / 2)) * 2)
        out = os.path.join(self.timelapses_dir, "custom.mp4")
        with self._timelapse_lock:
            compile_timelapse(
                sel, output_path=out, fps=float(fps), fourcc_str="avc1", add_overlay=bool(overlay),
                telemetry_list=telem, timestamps=stamps,
                station_name=str(self.station_config.get("location_name", "AllSky Hanoi")),
                target_size=size,
            )
        return {
            "success": True, "date_from": date_from, "date_to": date_to, "fps": float(fps),
            "video_frames_count": len(sel), "duration_s": round(len(sel) / max(1.0, float(fps)), 1),
            "size_kb": round(os.path.getsize(out) / 1024.0, 1), "timelapse_url": "/uploads/allsky/timelapses/custom.mp4",
        }

    def _write_timelapse_info(self, today_str: str, fps: float, stamps: List[datetime],
                              telem: List[Dict[str, Any]], update_latest: bool = True) -> None:
        """Per-video-frame capture info (latest.json + <date>.json) for the web player's HTML overlay."""
        import json

        frames = [
            [t.isoformat(timespec="seconds"), rec.get("exposure_s"), rec.get("gain_db"), rec.get("brightness"), rec.get("events")]
            for t, rec in zip(stamps, telem)
        ]
        tmp = os.path.join(self.timelapses_dir, f".info_{today_str}.json.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as fp:
                json.dump({"fps": fps, "fields": ["time", "exposure_s", "gain_db", "brightness", "events"], "frames": frames}, fp)
            if update_latest:
                shutil.copyfile(tmp, os.path.join(self.timelapses_dir, f"{today_str}.json"))
                os.replace(tmp, os.path.join(self.timelapses_dir, "latest.json"))
            else:
                os.replace(tmp, os.path.join(self.timelapses_dir, f"{today_str}.json"))
        except OSError:
            pass

    def get_gallery(self) -> Dict[str, Any]:
        """List astronomical observational products with live file status and frame counts."""
        today_str = self._obs_day_str()
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
                "info_url": "/uploads/allsky/timelapses/latest.json",
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
                "rejected": dict(self.startrails_gate.rejected),
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
