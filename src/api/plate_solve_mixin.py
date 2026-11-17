"""Location, sky mask, overlay and plate solving.

Mixin of AllSkyService (src/api/service.py): methods only, all state lives on the service instance."""

import math
import os
import re
import time
import threading
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
import numpy as np
import cv2
from src.defaults import DEFAULT_LAT, DEFAULT_LON

class PlateSolveMixin:

    def get_station_config(self) -> Dict[str, Any]:
        """Return station coordinates and astrometric camera/lens calibration parameters. The Flightradar24 token and
        password are write-only: they never leave the station (GET /api/adsb reports only whether a token is set)."""
        out = dict(self.station_config)
        out["timezone"] = self.timezone_info["setting"]
        out["timezone_info"] = self.timezone_info
        if isinstance(out.get("adsb"), dict):
            out["adsb"] = {k: v for k, v in out["adsb"].items() if k not in ("fr24_token", "fr24_password")}
        return out

    def _apply_timezone(self) -> Dict[str, Any]:
        from src import station_tz

        self.timezone_info = station_tz.apply(str(self.station_config.get("timezone") or ""),
                                              float(self.station_config.get("longitude_deg", 0.0)))
        return self.timezone_info

    def set_timezone(self, tz: str) -> Dict[str, Any]:
        """Station time zone: "" = the operating system's, "coords" = from the longitude, or an IANA name."""
        from src import station_tz

        tz = (tz or "").strip()
        if tz and tz != "coords" and not station_tz.valid_name(tz):
            raise ValueError(f"Unknown time zone '{tz}'")
        if tz:
            self.station_config["timezone"] = tz
        else:
            self.station_config.pop("timezone", None)
        info = self._apply_timezone()
        self._save_station_config()
        return {"success": True, "timezone": info["setting"], "timezone_info": info}

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
        Update station GPS coordinates and/or manual camera/lens/orientation settings on the Web UI
        (manual_overrides may also carry `remote_solver_url`, the opt-in remote plate solver).
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
        if manual_overrides and manual_overrides.get("remote_solver_url") is not None:
            ru = str(manual_overrides["remote_solver_url"]).strip()
            if ru and not (ru.startswith("http://") or ru.startswith("https://")):
                ru = ""
            self.station_config["remote_solver_url"] = ru[:300]
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
            # Overlays (/flights3d, /sky, satellites, ADS-B) read the camera pointing from the star solution:
            # a hand-edited azimuth / altitude / north angle must reach it too, or they keep the old pointing.
            sol = self.station_config.get("bright_star_solution")
            if isinstance(sol, dict) and any(
                manual_overrides.get(k) is not None
                for k in ("azimuth_deg", "altitude_deg", "zenith_tilt_deg", "north_position_angle_deg")
            ):
                sol["pointing_az_deg"] = float(orientation.get("azimuth_deg", sol.get("pointing_az_deg", 0.0)))
                sol["pointing_alt_deg"] = float(orientation.get("altitude_deg", sol.get("pointing_alt_deg", 90.0)))
                sol["roll_deg"] = float(orientation.get("north_position_angle_deg", sol.get("roll_deg", 0.0)))
                sol["manual_override"] = True

        if longitude_deg is not None and self.station_config.get("timezone") == "coords":
            self._apply_timezone()
        self._save_station_config()

        self.activity.success(
            "station",
            # No coordinates in the activity log: its messages must stay safe to show to anyone
            f"Station configuration saved: {self.station_config.get('location_name')}",
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

    def start_plate_solve_job(self, fov_hint: str = "auto", deep: bool = False) -> Dict[str, Any]:
        """Run plate-solving in the background (the solver can take minutes); poll get_plate_solve_status()."""
        with self._plate_solve_lock:
            if self.plate_solve_status.get("state") == "running":
                return dict(self.plate_solve_status)
            self.plate_solve_status = {
                "state": "running",
                "started_at": datetime.now().astimezone().isoformat(),
                "finished_at": None,
                "message": "Capturing a series of long exposures and matching the stars (usually 2-3 minutes)...",
                "deep": deep,
            }
        self.activity.info("station", "Deep plate-solve started (re-measuring lens and sensor)" if deep
                           else "Plate-solve started")

        def _job():
            try:
                res = self.run_station_plate_solve(fov_hint=fov_hint, deep=deep)
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

    def auto_sky_mask(self) -> Dict[str, Any]:
        """Propose sky/obstruction polygons from the current frame without saving them.

        The polygons are in the displayed orientation, like the ones the editor draws; the user reviews
        them and applies them with set_sky_mask. A night frame gives the cleanest result."""
        from src.calibration.sky_mask_auto import propose_sky_mask

        frame = self.last_frame
        if frame is None:
            raise ValueError("No camera frame yet")
        result = propose_sky_mask(self._apply_image_transform(frame, False))
        if not result["sky"]:
            raise ValueError("No clear open-sky region found in this frame (too bright, cloudy or fully obstructed): "
                             "try again at night or draw the mask by hand")
        return result

    def get_obs_overlay(self) -> Dict[str, Any]:
        """Get current OBS overlay display preferences and toggles."""
        cfg = dict(self.DEFAULT_OBS_OVERLAY)
        saved = self.station_config.get("obs_overlay")
        if isinstance(saved, dict):
            cfg.update(saved)
        return cfg

    def set_obs_overlay(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        """Update OBS overlay preferences and persist to station_config."""
        current = self.get_obs_overlay()
        for k, v in patch.items():
            if k in self.DEFAULT_OBS_OVERLAY and v is not None:
                current[k] = v
        self.station_config["obs_overlay"] = current
        self._save_station_config()
        self.activity.info("obs", f"OBS overlay updated: stars={current.get('sky_overlay')}, "
                                  f"labels={current.get('sky_labels')}, grid={current.get('grid_overlay')}")
        return current

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
            auto = self.hot_pixels.mask(shape)
            if not masks or not np.any(masks[0]):
                return auto
            hot = masks[0]
            h, w = shape[:2]
            ys, xs = np.nonzero(hot)
            out = np.zeros((h, w), dtype=np.uint8)
            out[np.minimum(h - 1, (ys * h / hot.shape[0]).astype(int)), np.minimum(w - 1, (xs * w / hot.shape[1]).astype(int))] = 1
            out = cv2.dilate(out, np.ones((5, 5), np.uint8)).astype(bool)
            return out if auto is None else out | auto
        except Exception:
            return None

    def _solve_bright_star_pattern(self, frame: np.ndarray, lat: float, lon: float,
                                   frames: Optional[List[np.ndarray]] = None,
                                   times: Optional[List[float]] = None,
                                   exclude: Optional[np.ndarray] = None,
                                   refit_lens: bool = False) -> Optional[Dict[str, Any]]:
        """Match the brightest stars against the catalog; apply and return the result, or None if unsure.

        With a frame series, only points that persist and drift with the sky are used (hot pixels are
        static). With already-solved lens intrinsics, only the rotation is fitted first (needs 3-4 stars);
        refit_lens skips that and measures focal length, distortion and optical centre again.
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
        intr = None if refit_lens else self._lens_intrinsics(w)
        try:
            # Solved optics (optical centre, roll) live in native sensor coordinates and the overlay applies the
            # display mirror/rotation itself, so solve on the native-orientation frame (the transform is its own
            # inverse). The sky mask is drawn on the displayed image, hence built there and turned back.
            mask = bss.combined_sky_mask(frame, self.station_config.get("sky_mask"))
            if mask is not None:
                mask = self._apply_image_transform(mask.astype(np.uint8), False).astype(bool)
            frame = self._apply_image_transform(frame, False)
            if frames is not None:
                frames = [self._apply_image_transform(f, False) for f in frames]
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
                    velocity=tracked["velocity"] if tracked is not None else None, sky_mask=mask,
                    velocity_sigma=tracked["velocity_sigma"] if tracked is not None else 0.0)
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
            self.activity.info("station", f"Bright-star solve: {res.get('error')}; trying the remote solver if one is configured")
            self._last_bright_error = str(res.get("error") or "")
            return None
        if res.get("mode") == "fixed_lens":
            return self._apply_fixed_lens_solution(res)

        return self._apply_bright_star_solution(res, frame.shape[1], frame.shape[0])

    def _apply_bright_star_solution(self, res: Dict[str, Any], w: int, h: int) -> Dict[str, Any]:
        """Store a free-lens star solution (pointing and lens) made on a w x h frame in the station config."""
        sensor = self.station_config.get("camera_sensor") or {}
        pitch = float(sensor.get("pixel_pitch_um") or 2.0)
        # The solved frame may be downscaled to the output resolution; station optics use sensor pixels
        res["frame_width"], res["frame_height"] = int(w), int(h)  # the stored solution is in this frame's pixels
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

    def manual_star_solve(self, points: List[List[float]], apply: bool = False,
                          when: Optional[datetime] = None) -> Dict[str, Any]:
        """Identify stars the user clicked on the live frame and (optionally) store the camera pointing.

        points: [x, y] frame fractions on the displayed image. Each click snaps to the brightest spot nearby,
        the clicked spots are matched to the bright-star catalog by their mutual angles, and with apply=True
        the pointing and lens are stored like a normal solve. Needs 4 or more real stars.
        """
        from src.calibration import bright_star_solver as bss

        frame = self.last_frame
        if frame is None:
            raise ValueError("No camera frame yet")
        if len(points) < 4:
            raise ValueError("Click at least 4 stars you can see in the frame")
        h, w = frame.shape[:2]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        gray = gray.astype(np.float32)
        tf = self.station_config.get("image_transform") or {}
        rot = int(tf.get("rotate_deg", 0)) % 360 == 180
        mirror_x, mirror_y = bool(tf.get("flip_horizontal")) != rot, bool(tf.get("flip_vertical")) != rot
        reach = max(10, int(w * 0.012))
        # Like every other solve, only stars on the open sky (your sky mask, minus obstructions) count
        user_mask = self.station_config.get("sky_mask")
        sky = bss.combined_sky_mask(frame, user_mask, use_auto=not (user_mask or {}).get("sky"))
        shown, native, skipped = [], [], []
        picked = 0
        for fx, fy in points:
            cx, cy = int(round(float(fx) * w)), int(round(float(fy) * h))
            x0, x1, y0, y1 = max(0, cx - reach), min(w, cx + reach + 1), max(0, cy - reach), min(h, cy + reach + 1)
            if x1 - x0 < 5 or y1 - y0 < 5:
                raise ValueError("A click lies outside the frame")
            patch = cv2.GaussianBlur(gray[y0:y1, x0:x1], (0, 0), 1.5)
            iy, ix = np.unravel_index(int(patch.argmax()), patch.shape)
            px, py = x0 + ix, y0 + iy
            a, b_, c_, d_ = max(0, px - 4), min(w, px + 5), max(0, py - 4), min(h, py + 5)
            win = gray[c_:d_, a:b_]
            wgt = np.clip(win - np.median(win), 0, None)
            tot = float(wgt.sum())
            if tot > 0:
                xs, ys = np.meshgrid(np.arange(a, b_), np.arange(c_, d_))
                px, py = float((wgt * xs).sum() / tot), float((wgt * ys).sum() / tot)
            picked += 1
            if sky is not None and not sky[min(h - 1, int(round(py))), min(w - 1, int(round(px)))]:
                skipped.append(picked)  # outside the sky mask: ignored, like every other solve
                shown.append([round(px / w, 4), round(py / h, 4)])
                native.append(None)
                continue
            flux = float(max(1.0, patch.max() - np.median(patch)))
            shown.append([round(px / w, 4), round(py / h, 4)])
            native.append([w - 1 - px if mirror_x else px, h - 1 - py if mirror_y else py, flux])
        valid = [n for n in native if n is not None]
        if len(valid) < 4:
            raise ValueError(f"Only {len(valid)} of your clicks lie on the open sky inside the sky mask "
                             f"(ignored: {', '.join(map(str, skipped)) or 'none'}); click at least 4 stars there")
        det = np.array(valid, dtype=np.float64)
        det = det[np.argsort(-det[:, 2])][:40]  # the pattern search seeds on the brightest 16, all 40 are matched
        lat = float(self.station_config.get("latitude_deg", DEFAULT_LAT))
        lon = float(self.station_config.get("longitude_deg", DEFAULT_LON))
        res = bss.solve_bright_stars(
            np.zeros((h, w, 3), np.uint8), lat, lon, when=when or datetime.now().astimezone(), projection="equidistant",
            hfov_candidates_deg=(176, 179, 182, 185), max_mag=4.5, n_detections=min(len(det), 8), n_seed_stars=100,
            sep_tol_deg=1.0, min_matches=min(4, len(det)), max_residual_px=10.0, max_rms_px=8.0,
            detections=det, sky_mask=np.ones((h, w), dtype=bool), min_completeness=0.0, deep=True)
        out: Dict[str, Any] = {"success": bool(res.get("success")), "points": shown, "clicked": len(det),
                               "skipped": skipped}
        if not res.get("success"):
            out["error"] = res.get("error", "no consistent star pattern")
            return out
        out["matched_stars"] = res["matched_stars"]
        out["names"] = []
        for item in native:
            if item is None:
                out["names"].append(None)
                continue
            nx, ny, _ = item
            hit = next((m for m in res["matched_stars"] if abs(m["x"] - nx) < 1.0 and abs(m["y"] - ny) < 1.0), None)
            out["names"].append({"name": hit["name"], "mag": hit["mag"]} if hit else None)
        out["rms_px"] = res["rms_px"]
        out["pointing"] = {k: res.get(k) for k in ("pointing_az_deg", "pointing_alt_deg", "roll_deg",
                                                    "fov_horizontal_deg", "fov_vertical_deg")}
        out["applied"] = False
        if len(res["matched_stars"]) < min(len(det), 6):
            out["warning"] = (f"Only {len(res['matched_stars'])} of {len(det)} clicked stars matched; "
                              "check that every click is on a real star")
        if skipped:
            out["warning"] = (out.get("warning", "") + f" Clicks {', '.join(map(str, skipped))} are outside the sky "
                              "mask and were ignored.").strip()
        if apply:
            applied = self._apply_bright_star_solution(res, w, h)
            self.station_config["solve_source"] = "manual star alignment"
            self._save_station_config()
            out.update({"applied": True, "message": applied.get("message")})
        return out

    def _apply_fixed_lens_solution(self, res: Dict[str, Any]) -> Dict[str, Any]:
        """Store the pointing from a fixed-lens solve; the lens intrinsics stay unless the solve re-measured them."""
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
        if res.get("refined_lens"):
            # Enough stars to re-measure focal length and distortion k: store them like a free-lens solve does
            sensor = self.station_config.get("camera_sensor") or {}
            sk = float(sensor.get("width_px") or res["frame_width"]) / float(res["frame_width"])
            f_s = res["focal_px"] * sk
            fisheye = res.get("projection") == "equidistant"
            dist_k = float(res["distortion_k"])
            optics.update({
                "focal_length_px": round(f_s, 2),
                "focal_length_mm": round(f_s * float(sensor.get("pixel_pitch_um") or 2.0) / 1000.0, 3),
                "distortion_k": dist_k,
                "pixel_scale_arcsec_px": round(3600.0 * math.degrees(1.0 / f_s), 2),
                "fov_horizontal_deg": res["fov_horizontal_deg"],
                "fov_vertical_deg": res["fov_vertical_deg"],
            })
            if fisheye:
                optics["horizon_radius_90_px"] = round(f_s * (math.pi / 2 + dist_k * (math.pi / 2) ** 3), 1)
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

    def run_station_plate_solve(self, fov_hint: str = "auto", deep: bool = False) -> Dict[str, Any]:
        """
        Capture/fetch a frame and solve it (local first; optional remote solver) when the user clicks Plate-Solve on the Web UI.

        deep: re-read the sensor geometry from the camera and re-measure the lens (focal length, distortion,
        optical centre) from the tracking series instead of reusing the solved lens.
        """
        from src.calibration.station_solver import solve_station_parameters

        lat = float(self.station_config.get("latitude_deg", DEFAULT_LAT))
        lon = float(self.station_config.get("longitude_deg", DEFAULT_LON))
        # Lens already solved: one frame usually fixes the pointing (only the rotation is unknown). Hot pixels
        # from the dark library are masked out; the 90 s tracking series runs only if this is not sure.
        if deep:
            if self.camera is not None:
                self.station_config["camera_sensor"] = self._detected_sensor_spec()
            self.activity.info("station", "Deep solve: sensor spec refreshed, lens will be re-measured")
        elif self.last_frame is not None and self._lens_intrinsics(self.last_frame.shape[1]) is not None:
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
        if (fov_hint or "auto") == "auto" and not deep:
            # A known lens FOV narrows the solver's scale search (it scans 0.35x..2.5x of the hint)
            hfov = (self.station_config.get("optics") or {}).get("fov_horizontal_deg")
            if hfov and 1.0 <= float(hfov) <= 170.0:
                fov_hint = str(int(round(float(hfov))))
        elev = float(self.station_config.get("elevation_m", 20.0))

        # 1. Local bright-star pattern solve: seconds, and works under city light pollution
        self._ps_progress("matching", f"Matching the bright stars of {len(frames)} frame(s) against the catalog",
                          eta_s=15.0, frame=len(frames), frames_total=len(frames))
        bright = self._solve_bright_star_pattern(frame, lat, lon, frames, times, refit_lens=deep) if len(frames) >= 3 \
            else self._solve_bright_star_pattern(frame, lat, lon, refit_lens=deep)
        if bright is not None:
            return bright
        if frame.ndim == 2:
            frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)

        # 2. Optional blind solve on an operator-configured remote solver (needs many stars, takes minutes).
        # Opt-in: without `remote_solver_url` nothing leaves this host.
        remote_url = str(self.station_config.get("remote_solver_url") or "").strip()
        if remote_url:
            self._ps_progress("online", "Bright-star match not sure enough: full blind solve on the configured remote solver (up to 3 min)",
                              eta_s=180.0)
        solved = solve_station_parameters(
            plate_solving_base_url=remote_url,
            frame_bgr=frame,
            latitude_deg=lat,
            longitude_deg=lon,
            elevation_m=elev,
            fov_hint=fov_hint or "auto",
            sensor_spec=self.station_config.get("camera_sensor"),
        )
        if not solved.get("success"):
            reason = solved.get("error") or "no solution"
            local = getattr(self, "_last_bright_error", "")
            if local:
                reason = f"{reason}. Local bright-star match: {local}"
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
