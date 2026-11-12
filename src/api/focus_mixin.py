"""Focus assistant sessions and tracking.

Mixin of AllSkyService (src/api/service.py): methods only, all state lives on the service instance."""

import os
import time
import threading
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
import numpy as np
import cv2
from src.hardware.driver import describe_camera, camera_limits
from src.analytics.focus_tracker import SKY_MAX_DIOPTRES
from src.analytics.focus import find_focus_tiles, LOWER_IS_BETTER, _contrast, display_stretch, find_best_star, find_focus_point, measure_focus

class FocusMixin:

    def _is_focus_night(self) -> Optional[bool]:
        """True at night (sun < -6 deg and exposure >= 50 ms), False in daytime, None under pytest."""
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return None
        exp = float(getattr(self.camera, "current_exposure_s", 0.0))
        if 0.0 < exp < 0.05:
            return False
        try:
            from src.calibration.auto_exposure import calculate_solar_elevation
            return bool(calculate_solar_elevation(self.auto_exposure.lat, self.auto_exposure.lon) < -6.0)
        except Exception:
            return None

    def get_focus_state(self) -> Dict[str, Any]:
        if self._is_focus_night() is False and self.focus.method in ("stars", "lights"):
            self.focus.method = "contrast"
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
        if "method" not in config and self._is_focus_night() is False and self.focus.method in ("stars", "lights"):
            config["method"] = "contrast"
        has_explicit_coords = "cx" in config or "cy" in config
        auto_point = (
            not has_explicit_coords
            and config.get("roi_mode") != "manual"
            and (
                (not config and not self.focus.active and self.focus.roi_mode != "manual")
                or config.get("roi_mode") == "auto"
            )
        )
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
            try:
                if state is not None and state.get("point", {}).get("method") in ("stars", "lights", "contrast"):
                    self.configure_focus(method=state["point"]["method"])
                af = self.start_lens_autofocus("sweep")
                af.update({k: state[k] for k in ("found", "point") if state and k in state})
                msg = (state or {}).get("message", "")
                af["message"] = f"{msg} · sweep autofocus started" if msg else "sweep autofocus started"
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
        if ("cx" in config or "cy" in config) and "roi_mode" not in config:
            config["roi_mode"] = "manual"
        if "method" not in config and self._is_focus_night() is False and self.focus.method in ("stars", "lights"):
            config["method"] = "contrast"
        with self._focus_lock:
            self.focus.last_action = time.time()
            self.focus.configure(**config)
            if reset:
                self.focus.reset_stats()
            self.focus.result_key = None
        if config.get("roi_mode") == "auto" and "cx" not in config and "cy" not in config:
            try:
                return self.focus_auto_point()
            except Exception:
                pass
        return self.get_focus_state()

    def _focus_new_frame(self, since_ts: float, timeout_s: float) -> Tuple[np.ndarray, Dict[str, Any], float]:
        """Return a frame newer than since_ts (capturing as needed), or the latest one on timeout."""
        deadline = time.time() + max(0.0, timeout_s)
        while True:
            frame, meta = self.capture_frame(force_refresh=(self.last_capture_time <= since_ts))
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
        is_night = self._is_focus_night()
        if is_night is False and self.focus.method in ("stars", "lights"):
            self.focus.method = "contrast"
        key = (ts, x0, y0, rw, self.focus.method, bool(stretch))
        with self._focus_lock:
            if self.focus.result_key == key and self.focus.crop_jpeg is not None and self.focus.last_result:
                return self.focus.crop_jpeg, self.focus.last_result
            crop = frame[y0:y0 + rh, x0:x0 + rw]
            result = self.focus.record(self.focus.measure(crop, is_night=is_night), ts)
            view = display_stretch(crop) if stretch else crop
            ok, jpeg = cv2.imencode(".jpg", view, [cv2.IMWRITE_JPEG_QUALITY, 93])
            prev_ts = (self.focus.last_result or {}).get("frame_ts", 0.0)
            lens_pos = None
            lens_target = None
            if self._lens_supported():
                lens_pos = getattr(self.camera, "lens_position", None)
                lens_target = getattr(self.camera, "lens_position_target", None)
                if lens_pos is None:
                    lens_pos = lens_target
            if lens_pos is None:
                lens_pos = self._lens_cfg().get("position", 0.0)
            sw = self.focus.sweep
            sweep_brief = None
            if sw:
                sweep_brief = {
                    "state": sw.get("state"),
                    "mode": sw.get("mode"),
                    "phase": sw.get("phase"),
                    "progress": round(float(sw.get("progress") or 0.0), 3),
                    "method": sw.get("method"),
                    "steps": len(sw.get("points") or []),
                    "message": sw.get("message"),
                }
            result.update({
                "frame_ts": ts,
                "frame_interval_s": round(ts - prev_ts, 3) if 0 < prev_ts < ts else None,
                "roi": [x0, y0, rw, rh],
                "frame_width": fw,
                "frame_height": fh,
                "lens_position": round(float(lens_pos), 4) if lens_pos is not None else 0.0,
                "lens_target": round(float(lens_target), 4) if lens_target is not None else None,
                "sweep": sweep_brief,
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

    def _auto_sweep_interval_s(self) -> float:
        """Refocus period of AF mode (lens_focus.refocus_every_h; 0 = only when there is no valid focus)."""
        h = float(self._lens_cfg().get("refocus_every_h", self.REFOCUS_EVERY_H_DEFAULT))
        return h * 3600.0 if h > 0 else 10 * 365 * 86400.0

    def _focus_sky_mask(self, frame: np.ndarray) -> Optional[np.ndarray]:
        """The station sky mask (Settings sky/obstruction polygons) at this frame's size, or None."""
        from src.calibration.bright_star_solver import polygon_mask
        m = self.station_config.get("sky_mask") or {}
        return polygon_mask(frame.shape, m.get("sky"), m.get("obstruction"))

    def _maybe_auto_sweep(self, frame: np.ndarray, now: float, cfg: Dict[str, Any]) -> bool:
        """Auto lens mode runs a full sweep autofocus by itself when there is no valid sky focus (none saved,
        or one nearer than 1 m), right after AF is chosen, and every refocus_every_h hours, but only
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
        point = find_focus_point(frame, sky_mask=self._focus_sky_mask(frame), is_night=(sun < -12.0))
        if sun > 3.0 and (point is None or point["method"] == "contrast"):
            cands = self._day_focus_candidates(frame, k=1)  # static detail, not sky/cloud or a known wall
            point = {"cx": cands[0]["cx"], "cy": cands[0]["cy"], "method": "contrast"} if cands else None
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
                # Only a remembered target a day sweep verified as distant static detail; none = no day probe
                # (probing cloud or sky lets noise walk the lens around)
                targets = self._lens_cfg().get("day_targets") or []
                self._day_focus_tile = (targets[0]["cx"], targets[0]["cy"]) if targets else None
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
        is_night = self._is_focus_night()
        if is_night is False and self.focus.method in ("stars", "lights"):
            return "contrast"
        if self.focus.method != "auto":
            return self.focus.method
        return measure_focus(frame[y0:y0 + rh, x0:x0 + rw], "auto", is_night=is_night)["method"]

    def _day_focus_candidates(self, frame: np.ndarray, frame2: Optional[np.ndarray] = None, k: int = 8,
                              side: Optional[int] = None) -> List[Dict[str, Any]]:
        """Where to measure focus by day, best first: the remembered good targets, then the most detailed
        tiles. Skipped: anything in the station sky mask (sky and cloud), tiles that change between two
        frames (drifting cloud), and places already found to be nearer than 1 m (walls next to the camera)."""
        cfg = self._lens_cfg()
        h, w = frame.shape[:2]
        # The ROI size is in native pixels; frames outside focus mode may be downscaled
        native_w = int(getattr(self.camera, "width", 0) or w)
        side = side or max(16, int(min(self.focus.roi_size * w / max(native_w, w), w, h)))
        mask = self._focus_sky_mask(frame)
        near = cfg.get("near_targets") or []

        def ok(t):
            px, py = int(round(t["cx"] * w)), int(round(t["cy"] * h))
            r_m = max(8, min(side // 4, h // 16, w // 16))
            if mask is not None:
                patch_mask = mask[max(0, py - r_m):min(h, py + r_m), max(0, px - r_m):min(w, px + r_m)]
                if patch_mask.size and float(patch_mask.mean()) > 0.3:
                    return False  # sky / cloud
            if self._near_any(t, near):
                return False  # a known nearby object
            x0 = int(min(max(0, t["cx"] * w - side / 2), w - side))
            y0 = int(min(max(0, t["cy"] * h - side / 2), h - side))
            if frame2 is not None and frame2.shape == frame.shape:
                a = cv2.cvtColor(frame[y0:y0 + side, x0:x0 + side], cv2.COLOR_BGR2GRAY).astype(np.float32)
                b = cv2.cvtColor(frame2[y0:y0 + side, x0:x0 + side], cv2.COLOR_BGR2GRAY).astype(np.float32)
                # A static tile differs between frames by sensor noise only, a drifting one by about its
                # own variation: compare with the tile's spread rather than its brightness
                if float(np.abs(a - b).mean()) > max(3.0, 0.25 * float(a.std())):
                    return False  # moving between frames: cloud
            return True

        out: List[Dict[str, Any]] = []
        for t in (cfg.get("day_targets") or []):
            if ok(t) and not self._near_any(t, out):
                out.append({"cx": t["cx"], "cy": t["cy"], "saved": True})
        for t in find_focus_tiles(frame, k=max(k * 4, 24), border=True):
            if len(out) >= k:
                break
            if ok(t) and not self._near_any(t, out):
                out.append({"cx": t["cx"], "cy": t["cy"]})
        return out[:k]

    def _remember_day_targets(self, good: List[Dict[str, Any]], near: List[Dict[str, Any]]) -> None:
        cfg = self._lens_cfg()
        now = time.time()
        targets = [t for t in (cfg.get("day_targets") or []) if not self._near_any(t, good)]
        cfg["day_targets"] = ([{**t, "t": round(now, 1)} for t in good] + targets)[:5]
        nears = [t for t in (cfg.get("near_targets") or []) if not self._near_any(t, near)]
        cfg["near_targets"] = ([{**t, "t": round(now, 1)} for t in near] + nears)[:30]
        if not os.environ.get("PYTEST_CURRENT_TEST"):
            self._save_station_config()

    def focus_auto_point(self) -> Dict[str, Any]:
        """Move the zoom onto the best thing to focus on in the current frame (see find_focus_point)."""
        frame = self.last_frame
        if frame is None:
            frame, _ = self.capture_frame()
        is_night = self._is_focus_night()
        point = find_focus_point(frame, sky_mask=self._focus_sky_mask(frame), is_night=is_night)
        if point is None or point["method"] == "contrast":
            # By day (or no star/light found): static detail outside the sky, not a known wall
            cands = self._day_focus_candidates(frame, k=1)
            point = {"cx": cands[0]["cx"], "cy": cands[0]["cy"], "method": "contrast"} if cands else None
        if point is None:
            state = self.get_focus_state()
            state.update({"found": False, "message": "nothing to focus on in the frame (blank sky, cloud or fog)"})
            return state
        cfg_kw: Dict[str, Any] = {"cx": point["cx"], "cy": point["cy"], "roi_mode": "auto"}
        if not os.environ.get("PYTEST_CURRENT_TEST") and point["method"] in ("stars", "lights", "contrast"):
            cfg_kw["method"] = point["method"]
        state = self.configure_focus(**cfg_kw)
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
        sh, sw = small.shape[:2]
        mh, mw = int(sh * 0.03), int(sw * 0.03)
        keep_sub = None
        if not lights:
            sky_mask = self._focus_sky_mask(frame)
            if sky_mask is not None:
                m = cv2.resize(sky_mask.astype(np.uint8), (sw, sh), interpolation=cv2.INTER_NEAREST) > 0
                m = cv2.erode(m.astype(np.uint8), np.ones((9, 9), np.uint8)) > 0
                sub = m[mh:sh - mh, mw:sw - mw]
                if sub.mean() > 0.05:
                    keep_sub = sub
        star = find_best_star(small[mh:sh - mh, mw:sw - mw], lights=lights, keep_mask=keep_sub)
        if star is None:
            return {"found": False, **self.get_focus_state()}
        cx = (star["x"] + mw + 0.5) * scale / w
        cy = (star["y"] + mh + 0.5) * scale / h
        cfg_kw: Dict[str, Any] = {"cx": cx, "cy": cy, "roi_mode": "manual"}
        if not os.environ.get("PYTEST_CURRENT_TEST"):
            cfg_kw["method"] = "lights" if (lights or star.get("saturated")) else "stars"
        state = self.configure_focus(**cfg_kw)
        return {"found": True, "source": star, **state}
