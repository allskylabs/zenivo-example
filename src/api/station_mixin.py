"""Station config, output resolution, image transform, performance profile.

Mixin of AllSkyService (src/api/service.py): methods only, all state lives on the service instance."""

import math
import os
from typing import Dict, Any, List, Optional, Tuple
import numpy as np
import cv2
from src.hardware.driver import describe_camera
from src.hardware.v4l2_mmap import configure_white_balance, get_white_balance, get_raw10_bin, set_raw10_bin
from src.pipeline.star_boost import GAMMA_RAW10, GAMMA_SRGB, STAR_BOOST_BINS, sky_polygon_mask, star_bin_boost
from src.api.service_consts import ARCHIVE_WIDTH
from src.defaults import DEFAULT_STATION_NAME, DEFAULT_LAT, DEFAULT_LON, DEFAULT_ELEVATION_M

class StationMixin:

    def _load_station_config(self) -> Dict[str, Any]:
        """Load persisted station coordinates and plate-solving optical parameters from disk."""
        import json

        default_cfg = self._default_station_config()
        try:
            if os.path.isfile(self.station_config_path):
                with open(self.station_config_path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                if isinstance(saved, dict):
                    default_cfg.update(saved)
                    if "hoan.uk" in str(default_cfg.get("plate_solver_endpoint") or ""):
                        default_cfg["plate_solver_endpoint"] = ""  # old hard-wired default, no longer used
        except Exception:
            pass
        return default_cfg

    @staticmethod
    def _default_station_config() -> Dict[str, Any]:
        """Factory station config: neutral location, IMX335 sensor, unsolved 1.65 mm fisheye pointing at the zenith."""
        from src.calibration.station_solver import IMX335_SPEC, compute_lst_and_zenith

        default_lat = DEFAULT_LAT
        default_lon = DEFAULT_LON
        default_elev = DEFAULT_ELEVATION_M
        z_ra, z_dec = compute_lst_and_zenith(default_lat, default_lon)
        default_cfg: Dict[str, Any] = {
            "latitude_deg": default_lat,
            "longitude_deg": default_lon,
            "elevation_m": default_elev,
            "location_name": DEFAULT_STATION_NAME,
            "geo_source": "default",
            "plate_solver_endpoint": "",
            "remote_solver_url": "",
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
        default_cfg["camera_sensor"] = dict(IMX335_SPEC)
        return default_cfg

    def _save_station_config(self) -> None:
        """Persist station configuration and solved optical parameters to disk."""
        import json
        import logging
        path = self.station_config_path
        tmp = f"{path}.tmp"
        try:
            # Atomic (temp file + rename) and owner-only (0600): the file can hold the FR24 login and map API key
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.station_config, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except Exception as exc:
            logging.getLogger(__name__).warning("Could not save %s: %s", path, exc)
            try:
                os.unlink(tmp)
            except OSError:
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
        if abs(tw / th - w / h) > 0.02:
            th = max(1, round(h * tw / w))  # a size saved for another camera's aspect: keep this frame's shape
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
            self._restore_imx335_identity()
            return
        w, h = int(self.camera.width), int(self.camera.height)
        self.station_config["camera_sensor"] = self._detected_sensor_spec(info)
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

    def _detected_sensor_spec(self, info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Sensor geometry of the attached camera (IMX335 datasheet for the RTS5869 bridge / simulator)."""
        from src.calibration.station_solver import IMX335_SPEC

        info = info or describe_camera(self.camera)
        if info.get("backend") in ("rts5869", "simulator"):
            return dict(IMX335_SPEC)
        w, h = int(self.camera.width), int(self.camera.height)
        pitch = float(info.get("pixel_pitch_um", 2.0))
        nat_w = int(info.get("native_width") or w)
        bin_pitch = pitch * nat_w / max(1, w)  # effective pitch of the (binned) output pixels
        return {
            "sensor_model": info.get("sensor_model", "unknown"),
            "usb_bridge": info.get("vid_pid", ""),
            "width_px": w,
            "height_px": h,
            "pixel_pitch_um": round(bin_pitch, 3),
            "sensor_width_mm": round(w * bin_pitch / 1000.0, 3),
            "sensor_height_mm": round(h * bin_pitch / 1000.0, 3),
            "sensor_diag_mm": round(math.hypot(w, h) * bin_pitch / 1000.0, 3),
        }

    def reset_station_calibration(self) -> Dict[str, Any]:
        """Forget the solved lens, sensor and mounting: back to the attached camera's stock values.
        Coordinates, sky mask, image transform, output size and focus are kept."""
        with self._plate_solve_lock:
            if self.plate_solve_status.get("state") == "running":
                return {"success": False, "message": "A plate-solve is running; reset after it finishes.",
                        "station_config": self.get_station_config()}
        defaults = self._default_station_config()
        for key in self.CALIBRATION_KEYS:
            self.station_config[key] = defaults[key]
        self.station_config.pop("bright_star_solution", None)
        if self.camera is not None:
            self._sync_camera_identity()  # sensor spec, optical centre and stock lens of the attached camera
        self._save_station_config()
        self.activity.info("station", "Lens, sensor and mounting parameters reset to the camera defaults")
        return {"success": True, "message": "Lens, sensor and mounting parameters reset. "
                "Run a deep plate-solve on a clear night to measure them again.",
                "station_config": self.get_station_config()}

    def _restore_imx335_identity(self) -> None:
        """Back on the IMX335 after another camera ran (e.g. a 1280x720 webcam): undo its sensor spec,
        optical centre and output size, or the 4:3 frame gets squashed into the old camera's shape."""
        from src.calibration.station_solver import IMX335_SPEC

        sensor = self.station_config.get("camera_sensor") or {}
        res = self.station_config.get("output_resolution") or {}
        allowed = {(p["width"], p["height"]) for p in self.RESOLUTION_PRESETS}
        foreign = (int(sensor.get("width_px", 0) or 0), int(sensor.get("height_px", 0) or 0)) != (2592, 1944)
        bad_res = (int(res.get("width", 0) or 0), int(res.get("height", 0) or 0)) not in allowed
        if not (foreign or bad_res):
            return
        if foreign:
            self.station_config["camera_sensor"] = dict(IMX335_SPEC)
            optics = self.station_config.setdefault("optics", {})
            centre = (self.station_config.get("bright_star_solution") or {}).get("optical_center_px")
            cx, cy = (float(centre[0]), float(centre[1])) if centre else (1296.0, 972.0)
            optics.update({"optical_center_x_px": cx, "optical_center_y_px": cy, "horizon_radius_90_px": 894.2})
        if bad_res:
            self.station_config["output_resolution"] = {"width": 2592, "height": 1944}
        self.activity.info("camera", f"Restored IMX335 station identity (sensor spec reset={foreign}, output size reset={bad_res})")
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
            "archive_width": ARCHIVE_WIDTH,
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

    def get_display_frame(self) -> Tuple[Optional[np.ndarray], float]:
        """(frame, capture timestamp) for viewers: the latest star-boosted frame.

        last_frame is published before AE and star boost run, so in that window it is a raw frame;
        serving it made /live flash a different-looking frame every few seconds."""
        display = self.display_frame
        if display is not None:
            return display, self._display_frame_ts
        return self.last_frame, self.last_capture_time

    def _star_boost_frame(self, frame: np.ndarray, raw10: bool = True) -> np.ndarray:
        """Sum-bin stars for viewers at night (sun below -6 deg), limited to the station sky mask."""
        cfg = self.station_config.get("star_boost") or {}
        if not cfg.get("enabled") or frame is None or frame.ndim != 3:
            return frame
        try:
            if self.effective_solar_elevation() >= -6.0:
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

            def night_flips() -> Tuple[bool, bool]:
                rot = int(tf.get("rotate_deg", 0)) % 360 == 180
                return bool(tf.get("flip_horizontal")) != rot, bool(tf.get("flip_vertical")) != rot

            before = night_flips()
            if flip_horizontal is not None:
                tf["flip_horizontal"] = bool(flip_horizontal)
            if flip_vertical is not None:
                tf["flip_vertical"] = bool(flip_vertical)
            if rotate_deg is not None:
                tf["rotate_deg"] = 180 if int(rotate_deg) % 360 == 180 else 0
            if rotate_isp_180 is not None:
                tf["rotate_isp_180"] = bool(rotate_isp_180)
            # The sky mask is drawn on the displayed (night) image: turn it with the image so it keeps covering
            # the same part of the sky. Solved optics are stored in sensor coordinates and need no change.
            after = night_flips()
            mask = self.station_config.get("sky_mask")
            if mask and after != before:
                fx, fy = after[0] != before[0], after[1] != before[1]
                for key in ("sky", "obstruction"):
                    mask[key] = [[[round(1.0 - x, 4) if fx else x, round(1.0 - y, 4) if fy else y]
                                  for x, y in poly] for poly in mask.get(key, [])]
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
