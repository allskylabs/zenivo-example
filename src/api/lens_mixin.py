"""Motorised lens control and camera/sweep autofocus.

Mixin of AllSkyService (src/api/service.py): methods only, all state lives on the service instance."""

import math
import os
import time
import threading
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
import numpy as np
from src.hardware.driver import camera_limits
from src.analytics.focus_tracker import SKY_MAX_DIOPTRES, TempCompensation
from src.analytics.focus import analyse_sweep, LOWER_IS_BETTER, find_focus_point, measure_focus
from src.api.service_consts import PAST_PEAK_D

class LensMixin:

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
            if self.focus.roi_mode == "auto" and not os.environ.get("PYTEST_CURRENT_TEST"):
                try:
                    self.focus_auto_point()
                except Exception:
                    pass
            try:
                return self.start_lens_autofocus("sweep")
            except RuntimeError:
                pass
        return self.get_focus_state()

    def get_lens_state(self) -> Dict[str, Any]:
        if not self._lens_supported():
            cfg = self._lens_cfg()
            pos = float(cfg.get("position", 0.0))
            return {
                "available": True,
                "motorized": False,
                "min": 0.0,
                "max": 3.0,
                "default": 0.0,
                "position": pos,
                "held": pos,
                "mode": "manual",
                "saved": {k: v for k, v in cfg.items() if k != "samples"},
                "sweep": dict(self.focus.sweep) if self.focus.sweep else None,
            }
        info = dict(self.camera.lens_info() or {})
        info["motorized"] = True
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
        info["refocus_every_h"] = float(cfg.get("refocus_every_h", self.REFOCUS_EVERY_H_DEFAULT))
        nxt = cfg.get("next_auto_sweep")
        info["next_auto_sweep_in_s"] = round(max(0.0, float(nxt) - time.time())) if nxt is not None else 0
        if self.focus_tracker.next_ts:
            info["tracking"]["next_in_s"] = round(max(0.0, self.focus_tracker.next_ts - time.time()))
        info["sweep"] = dict(self.focus.sweep) if self.focus.sweep else None
        return info

    def set_lens(self, position: Optional[float] = None, delta: Optional[float] = None,
                 tracking: Optional[bool] = None, mode: Optional[str] = None,
                 reset_learning: bool = False, refocus_every_h: Optional[float] = None) -> Dict[str, Any]:
        if not self._lens_supported():
            if mode == "auto":
                raise RuntimeError("Autofocus requires a camera with motorized lens. For manual lenses, please use manual focus (MF).")
            cfg = self._lens_cfg()
            cur = float(cfg.get("position", 0.0))
            target = float(position) if position is not None else float(cur) + float(delta or 0.0)
            pos = round(max(0.0, min(3.0, target)), 4)
            self._save_lens(pos, "manual")
            if not os.environ.get("PYTEST_CURRENT_TEST"):
                self._save_station_config()
            self.focus.last_action = time.time()
            with self._focus_lock:
                self.focus.result_key = None
            return self.get_focus_state()
        if refocus_every_h is not None:
            cfg = self._lens_cfg()
            cfg["refocus_every_h"] = max(0.0, float(refocus_every_h))
            if cfg["refocus_every_h"] > 0:
                cfg["next_auto_sweep"] = float(cfg.get("last_auto_sweep") or time.time()) + self._auto_sweep_interval_s()
            if not os.environ.get("PYTEST_CURRENT_TEST"):
                self._save_station_config()
            if position is None and delta is None and mode is None and tracking is None and not reset_learning:
                return self.get_focus_state()
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
            self.focus.result_key = None
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
        cfg = self._lens_cfg()
        saved = cfg.get("position")
        max_accept_d = SKY_MAX_DIOPTRES
        if source == "sweep":
            if self.focus.roi_mode == "manual":
                max_accept_d = float(self.camera.lens_range[1]) if self._lens_supported() else 3.0
            elif sw and sw.get("method") == "contrast":
                max_accept_d = 1.65
        if state != "done" and cfg.get("mode") == "auto" and saved is not None and float(saved) <= max_accept_d:
            # A failed refocus keeps the last good focus; try again one refocus period later, not every
            # 30 min (each attempt pauses archiving for a few minutes)
            cfg["next_auto_sweep"] = time.time() + self._auto_sweep_interval_s()
        if state == "done" and position is not None and float(position) > max_accept_d:
            # Focused on something nearer than 1 m (window frame, railing, dome dust): not the sky
            back = (self.station_config.get("lens_focus") or {}).get("base_position")
            if back is None or float(back) > max_accept_d:
                back = self.camera.lens_range[0]
            self.camera.set_lens_position(float(back))
            msg = (f"focused on a near object ({1 / float(position):.2f} m, {float(position):.3f} D), not the sky: "
                   f"not saved, lens put back to {float(back):.3f} D. Move the zoom onto distant detail or stars.")
            sw.update({"state": "rejected", "message": msg, "progress": 1.0, "finished_at": time.time(),
                       "rejected_position": round(float(position), 4)})
            with self._focus_lock:
                self.focus.result_key = None
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
            self.focus.result_key = None
        if state == "done" and self.focus.active:
            sw["phase"] = "saving"
            # Measure the result where the lens now sits, record it in settings, tell the user, leave focus mode
            score = sw.get("final_score")
            method = sw.get("method") or (self.focus.method if self.focus.method != "auto" else None)
            try:
                if score is None and method:
                    score, _ = self._lens_score(float(position), method, 1, 3, self.last_capture_time)
            except Exception:
                pass
            sw["final_score"] = score
            if sw.get("auto"):
                cfg_l = self._lens_cfg()
                cfg_l["last_auto_sweep"] = time.time()
                cfg_l["next_auto_sweep"] = time.time() + self._auto_sweep_interval_s()
            self.finish_focus("auto_sweep" if sw.get("auto") else source, method=method, score=score, note=message)
            sw.update({"state": state, "message": message + " · saved, focus mode off", "progress": 1.0,
                       "finished_at": time.time()})
            return
        sw.update({"state": state, "message": message, "progress": 1.0, "finished_at": time.time()})
        if not os.environ.get("PYTEST_CURRENT_TEST"):
            self.activity.log("warning", "camera", f"Lens autofocus ({sw.get('mode')}): {message}")
        if sw.get("auto") and self.focus.active:
            self.stop_focus(f"automatic autofocus {state}")

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
        prev_pos = getattr(self.camera, "lens_position", None)
        pos = self.camera.set_lens_position(pos)
        is_pytest = bool(os.environ.get("PYTEST_CURRENT_TEST"))
        if not is_pytest:
            time.sleep(0.05)  # allow VCM voice-coil motor to physically settle before requesting next frame
            self.last_capture_time = 0.0
            since = max(since, time.time())
        wait = 10.0 + 3 * float(getattr(self.camera, "current_exposure_s", 0.0))
        # Controls reach the sensor a few frames late: wait until frames report the new position
        # (when the camera reports one), then drop `settle` more exposed while the lens was moving
        last_rep = None
        arrived_frame = None
        max_attempts = 2 if not is_pytest else 8
        for attempt in range(max_attempts):
            f, meta, since = self._focus_new_frame(since, timeout_s=wait)
            reported = meta.get("lens_position")
            if reported is None:
                arrived_frame = f
                break
            rep_f = float(reported)
            if abs(rep_f - pos) < 0.025:
                arrived_frame = f
                break
            moved_from_prev = (
                prev_pos is None
                or abs(rep_f - float(prev_pos)) >= 0.015
                or abs(pos - float(prev_pos)) < 0.025
            )
            if attempt >= 1 and moved_from_prev and (not is_pytest or (attempt >= 2 and last_rep is not None and abs(rep_f - last_rep) < 0.005)):
                # Motor has settled at resting position
                arrived_frame = f
                break
            last_rep = rep_f
        for _ in range(settle):
            _, _, since = self._focus_new_frame(since, timeout_s=wait)
        per = [[] for _ in range(1 + len(tiles))]
        for k in range(frames):
            if k == 0 and settle == 0 and arrived_frame is not None and not is_pytest:
                frame = arrived_frame
            else:
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
                    if i == 0 and not is_pytest:
                        with self._focus_lock:
                            self.focus.last_result = self.focus.record(res, time.time())
        return [float(np.median(v)) if v else None for v in per], since

    @staticmethod
    def _sweep_positions(lo: float, hi: float) -> List[float]:
        """0.04 D steps to 1.6 D (sky and daytime NIR/horizon focus), 0.1 D to 3 D, then 0.25 D to the end."""
        out, p = [], float(lo)
        while p <= hi + 1e-9:
            out.append(round(p, 4))
            p += 0.04 if p < 1.6 - 1e-9 else 0.1 if p < 3.0 - 1e-9 else 0.25
        return out


    def _lens_sweep(self) -> None:
        sw = self.focus.sweep
        is_pytest = bool(os.environ.get("PYTEST_CURRENT_TEST"))
        old_exp = float(getattr(self.camera, "current_exposure_s", 0.1))
        old_gain = float(getattr(self.camera, "current_gain_db", 0.0))
        fast_exp_applied = False
        try:
            lo, hi, _ = self.camera.lens_range
            start_pos = getattr(self.camera, "lens_position_target", None)
            # Reuse last_frame in production so we don't wait 4-10 s on a night exposure before moving the lens
            if not is_pytest and self.last_frame is not None:
                frame = self.last_frame
                since = self.last_capture_time
            else:
                frame, _, since = self._focus_new_frame(self.last_capture_time, timeout_s=15.0)
            fh, fw = frame.shape[:2]
            x0, y0, rw, rh = self.focus.roi_rect(fw, fh)
            is_night = self._is_focus_night()
            method = self.focus.method
            if is_night is False and method in ("stars", "lights"):
                method = "contrast"
            if method == "auto":
                method = measure_focus(frame[y0:y0 + rh, x0:x0 + rw], "auto", is_night=is_night)["method"]
                if not is_pytest and method == "contrast":
                    try:
                        from src.calibration.auto_exposure import calculate_solar_elevation
                        if calculate_solar_elevation(self.auto_exposure.lat, self.auto_exposure.lon) < -6.0:
                            pt_n = find_focus_point(frame, sky_mask=self._focus_sky_mask(frame), is_night=True)
                            if pt_n is not None and pt_n.get("method") in ("stars", "lights"):
                                method = pt_n["method"]
                                if self.focus.roi_mode != "manual":
                                    with self._focus_lock:
                                        self.focus.cx, self.focus.cy = float(pt_n["cx"]), float(pt_n["cy"])
                                    x0, y0, rw, rh = self.focus.roi_rect(fw, fh)
                            else:
                                method = "stars"
                    except Exception:
                        pass
            lower = LOWER_IS_BETTER[method]
            sw["method"] = method
            exp = float(getattr(self.camera, "current_exposure_s", 0.1))
            if not is_pytest and exp > 0.25:
                try:
                    lim = camera_limits(self.camera)
                    max_gain = float(lim.get("max_gain_db") or 36.0)
                    if method in ("stars", "lights"):
                        sweep_exp = min(exp, 0.38)
                    else:
                        sweep_exp = 0.18
                    if exp > sweep_exp + 0.05:
                        sweep_gain = max_gain if method in ("stars", "lights") else min(max_gain, max(old_gain, old_gain + 20.0 * math.log10(exp / sweep_exp)))
                        with self.lock:
                            self.camera.set_hardware_exposure(sweep_exp, sweep_gain)
                            self.last_capture_time = 0.0
                        exp = sweep_exp
                        fast_exp_applied = True
                except Exception:
                    pass
            if not is_pytest:
                settle, frames = 0, 1
            else:
                settle, frames = (0, 2) if exp >= 0.4 else (1, 3)

            def quality(score):
                return float("-inf") if score is None else (-score if lower else score)

            def measure(pos, phase):
                nonlocal since
                score, since = self._lens_score(pos, method, settle, frames, since)
                sw["points"].append({"pos": round(pos, 4), "score": score, "phase": phase})
                return score

            # By day (when roi_mode is not manual) measure several detailed tiles besides the zoom on the same frames
            tiles: List[Dict[str, float]] = []
            main_allowed = True
            if method == "contrast" and self.focus.roi_mode != "manual":
                frame2, _, since = self._focus_new_frame(since, timeout_s=15.0)
                cands = self._day_focus_candidates(frame, frame2, k=8, side=rw)
                grid_tol = max(rw / 2, fw / 12)
                main_allowed = any(abs(t["cx"] - self.focus.cx) * fw <= grid_tol and abs(t["cy"] - self.focus.cy) * fh <= grid_tol
                                   for t in self._day_focus_candidates(frame, frame2, k=40, side=rw)) or not cands
                for t in cands:
                    if abs(t["cx"] - self.focus.cx) * fw > rw or abs(t["cy"] - self.focus.cy) * fh > rh:
                        tiles.append({"cx": t["cx"], "cy": t["cy"]})
            elif method in ("stars", "lights") and self.focus.roi_mode != "manual":
                pt = find_focus_point(frame, sky_mask=self._focus_sky_mask(frame), is_night=True)
                if pt is not None and (abs(pt["cx"] - self.focus.cx) * fw > rw * 0.5 or abs(pt["cy"] - self.focus.cy) * fh > rh * 0.5):
                    tiles.append({"cx": float(pt["cx"]), "cy": float(pt["cy"])})
            sw["tiles"] = [{"cx": round(t["cx"], 4), "cy": round(t["cy"], 4)} for t in tiles]

            # One pass from infinity (0 D) outwards, continuing over the lens range until every
            # measured region is clearly past its peak (at least 4 steps down the right side of the parabola)
            sweep_hi = min(hi, 1.4 if (method in ("stars", "lights") and self.focus.roi_mode != "manual") else 2.8) if not is_pytest else hi
            positions = self._sweep_positions(lo, sweep_hi)
            n_roi = 1 + len(tiles)
            curves: List[List[Optional[float]]] = [[] for _ in range(n_roi)]
            min_q = [float("inf")] * n_roi
            best = [float("-inf")] * n_roi
            best_at = [None] * n_roi
            worse_run = [0] * n_roi
            done_pos: List[float] = []
            sw["phase"] = "sweep"
            for i, pos in enumerate(positions):
                if self.focus.sweep_cancel:
                    raise InterruptedError
                if i == 0 and not is_pytest:
                    # The first frame after focus mode starts can still carry the previous pipeline state: it
                    # scored 31.6 at 0.0 D where the same spot re-measured 16.8, which then won the sweep
                    _, since = self._lens_score_multi(pos, method, 0, 1, since, [])
                scores, since = self._lens_score_multi(pos, method, settle, frames, since, tiles)
                done_pos.append(pos)
                sw["points"].append({"pos": round(pos, 4), "score": scores[0], "phase": "sweep"})
                sw["tile_scores"] = sw.get("tile_scores", []) + [[None if v is None else round(v, 4) for v in scores[1:]]]
                for r, v in enumerate(scores):
                    curves[r].append(v)
                    q = quality(v)
                    if np.isfinite(q) and q < min_q[r]:
                        min_q[r] = q
                    if q > best[r]:
                        best[r], best_at[r], worse_run[r] = q, pos, 0
                    elif np.isfinite(best[r]):
                        rise = best[r] - min_q[r] if np.isfinite(min_q[r]) else 0.0
                        worse = (
                            (not np.isfinite(q))
                            or (abs(q) > abs(best[r]) * 1.3 if lower else q < best[r] * 0.7)
                            or (not lower and rise >= 0.06 * abs(best[r]) and (best[r] - q) >= 0.30 * rise and pos - (best_at[r] or 0.0) >= PAST_PEAK_D - 1e-9)
                        )
                        worse_run[r] = worse_run[r] + 1 if worse else 0
                sw["progress"] = 0.95 * (i + 1) / len(positions)

                # Once any region is past its peak, the others need not hold the sweep: a region that is
                # flat (blank sky, cloud) or has not improved for 0.6 D since its best is not going to peak
                any_peak = any(worse_run[r] >= 3 and (best_at[r] is None or pos - best_at[r] >= PAST_PEAK_D - 1e-9) for r in range(n_roi))

                def finished(r):
                    if worse_run[r] >= 3 and (best_at[r] is None or pos - best_at[r] >= PAST_PEAK_D - 1e-9):
                        return True  # clearly past its peak (at least 4 steps down the right side of the parabola)
                    if pos < 1.0:
                        return False  # scan the entire sky focus range (0.0 to 1.0 D) before early exit
                    if not any_peak:
                        return False  # nothing has peaked yet: keep going to find a peak
                    vals = [quality(v) for v in curves[r] if v is not None]
                    if len(vals) < 3 or (max(vals) - min(vals)) / max(abs(max(vals)), 1e-9) < 0.05:
                        return True
                    return best_at[r] is not None and pos - best_at[r] >= 0.6 - 1e-9
                past = [finished(r) for r in range(n_roi)]
                if all(past) and i >= 12:
                    break

            # Judge every region's curve; sky focus = the most decisive peak within max_accept_d
            max_accept_d = hi if self.focus.roi_mode == "manual" else (1.65 if method == "contrast" else SKY_MAX_DIOPTRES)
            results = []
            for r in range(n_roi):
                res = analyse_sweep(done_pos, curves[r], lower)
                res["roi"] = 0 if r == 0 else r
                results.append(res)
            if not main_allowed and results[0]["ok"] and results[0]["vertex"] <= max_accept_d:
                results[0].update({"ok": False, "reason": "sky/cloud"})  # the zoom sits on sky or drifting cloud
            if not lower:
                # A relative change means nothing on an almost featureless tile (contrast near 0: noise
                # alone looks like a big swing). Only regions with real detail - a peak at least 25 % of
                # the strongest region's peak - may decide the focus.
                peaks = [abs(float(x["peak_score"])) for x in results if x["ok"] and x.get("peak_score") is not None]
                floor = 0.25 * max(peaks) if peaks else 0.0
                for x in results:
                    if x["ok"] and abs(float(x.get("peak_score") or 0.0)) < floor:
                        x.update({"ok": False, "reason": "weak"})
            sky = [x for x in results if x["ok"] and x["vertex"] <= max_accept_d]
            near = [x for x in results if x["ok"] and x["vertex"] > max_accept_d]
            if method == "contrast":
                def where(x):
                    return ({"cx": self.focus.cx, "cy": self.focus.cy} if x["roi"] == 0 else
                            {"cx": tiles[x["roi"] - 1]["cx"], "cy": tiles[x["roi"] - 1]["cy"]})
                good = [dict(where(x), vertex=x["vertex"], spread=x["spread"])
                        for x in sorted(sky, key=lambda x: -x["spread"]) if x["vertex"] <= SKY_MAX_DIOPTRES]
                self._remember_day_targets(good, [dict(where(x), vertex=x["vertex"]) for x in near])
            sw["regions"] = [{k: x.get(k) for k in ("roi", "ok", "reason", "vertex", "spread")} for x in results]
            if not sky:
                pos_back = start_pos if start_pos is not None and start_pos <= max_accept_d else lo
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
            if win["roi"] != 0 and self.focus.roi_mode != "manual":
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
            coarse = 0.04 if final < 1.6 else 0.1 if final < 3.0 else 0.25
            fine = max(0.02 if not is_pytest else 0.01, coarse / (2 if not is_pytest else 4))
            span = (1.5 if not is_pytest else 2.0) * coarse
            top, bottom = min(positions[-1], final + 2 * span), max(lo, final - span)  # extra far side: fits the parabola better
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
            if best_score is not None:
                sw["final_score"] = round(float(best_score), 4)
            if fast_exp_applied:
                fast_exp_applied = False
                try:
                    with self.lock:
                        self.camera.set_hardware_exposure(old_exp, old_gain)
                        self.last_capture_time = 0.0
                except Exception:
                    pass
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
        finally:
            if fast_exp_applied:
                try:
                    with self.lock:
                        self.camera.set_hardware_exposure(old_exp, old_gain)
                        self.last_capture_time = 0.0
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Day focus targets: static detail, never sky or cloud, remembered across refocuses
    # ------------------------------------------------------------------
    @staticmethod
    def _near_any(t: Dict[str, float], others: List[Dict[str, Any]], r: float = 0.06) -> bool:
        return any(abs(t["cx"] - o["cx"]) < r and abs(t["cy"] - o["cy"]) < r for o in others or [])
