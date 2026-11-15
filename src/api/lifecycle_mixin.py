"""Retention/storage, camera watchdog and shutdown.

Mixin of AllSkyService (src/api/service.py): methods only, all state lives on the service instance."""

import logging
import os
import threading
from typing import Dict, Any


from src.storage.retention import merge_policy

logger = logging.getLogger("imx335.api")


class LifecycleMixin:

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

    def close(self) -> None:
        """Release camera hardware resources."""
        if self.adsb is not None:
            self.adsb.stop()
            os.makedirs(os.path.dirname(self._adsb_trails_path()), exist_ok=True)
            self.adsb.save_trails(self._adsb_trails_path())
        if self.adsb_public is not None:
            self.adsb_public.stop()
        if self.adsb_contrib is not None:
            self.adsb_contrib.stop()
        if getattr(self, "aircraft_info", None) is not None:
            self.aircraft_info.stop()
        if getattr(self, "community", None) is not None:
            self.community.stop()
        for name in ("_retention_stop", "_camera_watchdog_stop"):
            stop = getattr(self, name, None)
            if stop is not None:
                stop.set()
        if self.camera is not None:
            try:
                self.camera.close()
            except Exception:
                pass
