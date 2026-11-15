"""Choosing the active camera at runtime (several cameras attached: ZWO ASI, IMX335, Pi CSI, webcam)."""

from typing import Any, Dict

from src.hardware.driver import describe_camera, get_camera_driver, list_camera_devices


class CameraSelectMixin:
    def _current_camera_type(self) -> str:
        if getattr(self.camera, "simulator", False) or "Simulated" in self.camera.__class__.__name__:
            return "simulator"
        return str(describe_camera(self.camera).get("backend", "rts5869"))

    def list_camera_options(self) -> Dict[str, Any]:
        """Cameras found now, the one in use and the saved choice (applied at the next start unless --camera is given)."""
        info = describe_camera(self.camera)
        kwargs = getattr(self, "_camera_driver_kwargs", None) or {}
        cur_type = self._current_camera_type()
        saved = dict(self.station_config.get("camera_select") or {"type": "auto", "index": 0})
        index = int(kwargs.get("camera_index", saved.get("index", 0)) or 0) if kwargs.get("camera_type", saved.get("type")) == cur_type else 0
        devices = list_camera_devices()
        # A camera that is open right now may not be listed again by its SDK: always show the active one
        if not any(d["type"] == cur_type and d["index"] == index for d in devices) and cur_type != "simulator":
            devices.insert(0, {"type": cur_type, "index": index, "id": f"{cur_type}:{index}", "name": str(info.get("sensor"))})
        return {
            "current": {"type": cur_type, "index": index, "id": f"{cur_type}:{index}", "sensor": info.get("sensor"),
                        "width": int(getattr(self.camera, "width", 0) or 0), "height": int(getattr(self.camera, "height", 0) or 0)},
            "devices": devices,
            "saved": saved,
        }

    def select_camera(self, camera_type: str, index: int = 0) -> Dict[str, Any]:
        """Close the active camera, open the chosen one and make it the saved choice. On failure the old camera is
        re-opened and a ValueError explains why."""
        if self.focus.active or self.calibration_status.get("state") == "running":
            raise ValueError("Stop focus mode / calibration before switching camera")
        with self.lock:
            old = self.camera
            exp = float(getattr(old, "current_exposure_s", self.auto_exposure.exposure_s))
            gain = float(getattr(old, "current_gain_db", self.auto_exposure.gain_db))
            try:
                old.close()
            except Exception:
                pass
            try:
                new = get_camera_driver(fallback_to_simulator=False, camera_type=camera_type, camera_index=int(index))
            except Exception as exc:
                try:
                    old.open()
                except Exception:
                    pass
                raise ValueError(f"Could not open camera {camera_type}:{index}: {exc}")
            if camera_type == "simulator":
                new = get_camera_driver(camera_type="simulator")
            self.camera = new
            try:
                new.set_hardware_exposure(exp, gain)
            except Exception:
                pass
            self._camera_driver_kwargs = dict(camera_type=camera_type, camera_index=int(index))
            self.station_config["camera_select"] = {"type": camera_type, "index": int(index)}
            self._sync_camera_identity()
            self._save_station_config()
            self.last_capture_time = 0.0
        info = describe_camera(new)
        self.activity.success("camera", f"Camera switched to {info.get('sensor')} via {info.get('backend')} at {new.width}x{new.height}")
        return self.list_camera_options()
