"""Sky events: detection, persistence and lookup of frames/images.

Mixin of AllSkyService (src/api/service.py): methods only, all state lives on the service instance."""

import os
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
import numpy as np
import cv2
from src.storage import frame_codec

class EventsMixin:

    def get_event_config(self) -> Dict[str, Any]:
        cfg = dict(self.EVENT_DEFAULTS)
        cfg.update({k: int(bool(v)) for k, v in (self.station_config.get("event_detection") or {}).items() if k in cfg})
        return {
            "enabled": cfg, "defaults": dict(self.EVENT_DEFAULTS), "night_sun_elev_deg": self.EVENT_NIGHT_SUN_ELEV,
            "night": self._events_night, "frames_seen": self.sky_events.frames_seen,
            "frames_compared": self.sky_events.frames_compared,
            "dot_tracking": dict(self.sky_events.point_stats),
        }

    def set_event_config(self, **flags: Optional[bool]) -> Dict[str, Any]:
        clean = {}
        for key, val in flags.items():
            if val is None:
                continue
            if key not in self.EVENT_DEFAULTS:
                raise ValueError(f"Unknown event type {key}")
            clean[key] = int(bool(val))
        if clean:
            self.station_config.setdefault("event_detection", {}).update(clean)
            self._save_station_config()
            on = [k for k, v in self.get_event_config()["enabled"].items() if v]
            self.activity.info("events", "Event detection: " + (", ".join(on) if on else "all off"))
            if not on:
                self.sky_events.reset()
        return {"success": True, **self.get_event_config()}

    def _detect_sky_events(self, frame: np.ndarray, meta: Dict[str, Any], now: float) -> List[str]:
        """Streak / flash detection on a new frame, day and night (a few ms on a ~640 px gray copy).
        Returns the types of the events found (their frame is archived)."""
        enabled = {k for k, v in self.get_event_config()["enabled"].items() if v and k != "clouds"}
        if now - self._events_sun_ts >= 60.0:
            from src.calibration.auto_exposure import calculate_solar_elevation
            self._events_sun_ts = now
            night = calculate_solar_elevation(self.auto_exposure.lat, self.auto_exposure.lon) < self.EVENT_NIGHT_SUN_ELEV
            if self._events_night != night:
                if getattr(self, "day_objects", None) is not None:
                    self.day_objects.reset()
                self.sky_events.reset()  # dusk / dawn: the sky and exposure change completely, start a fresh reference
            self._events_night = night
        if not self._events_night:
            # By day only aircraft and flashes can show (meteors and satellites vanish in the glare); high aircraft
            # often do not show at all against a bright sky, and cloud / rain are screened out by the usual guards
            enabled &= self.EVENT_DAY_TYPES
        if not self._events_night:
            self._track_day_objects(frame, now)
        if not enabled:
            return []
        events = self.sky_events.process(
            frame, enabled, ts=now,
            exposure_s=float(meta.get("actual_exposure_s", getattr(self.camera, "current_exposure_s", 0.0))),
            gain_db=float(meta.get("gain_db", getattr(self.camera, "current_gain_db", 0.0))),
            sky_polygons=self.station_config.get("sky_mask"),
        )
        for ev in events:
            self._record_sky_event(ev)
        return [str(ev.get("type")) for ev in events]

    def day_object_log(self):
        """The on-disk log of daytime flying objects (birds, insects, aircraft), created on first use."""
        from src.analytics.traffic_log import TrafficLog
        from src.analytics.day_objects import normalize_config
        cfg = normalize_config(self.station_config.get("day_objects"))
        dl = getattr(self, "_day_log", None)
        if dl is None:
            dl = self._day_log = TrafficLog(os.path.join(self.output_dir, "day_objects"), cfg)
        else:
            dl.configure(cfg)
        return dl

    def _track_day_objects(self, frame: np.ndarray, now: float) -> None:
        """Daytime: follow small moving dots (birds, insects, aircraft) and store the finished tracks. While rain
        drops sit on the dome nothing is stored."""
        from src.analytics.day_objects import DayObjectTracker, keep_classes, normalize_config
        cfg = normalize_config(self.station_config.get("day_objects"))
        if not cfg["enabled"]:
            return
        if getattr(self, "day_objects", None) is None:
            self.day_objects = DayObjectTracker(config=cfg)
        was = self.day_objects.raining
        tracks = self.day_objects.process(frame, now, self.station_config.get("sky_mask"))
        if self.day_objects.raining != was:
            self.activity.info("events", "Rain: drops on the dome, daytime object tracking paused" if not was
                               else "Rain over: daytime object tracking resumed")
        log = self.day_object_log()
        for t in keep_classes(tracks, cfg["classes"]):
            log.append_record(float(t["t"]), t)

    def get_day_objects_config(self) -> Dict[str, Any]:
        from src.analytics.day_objects import CLASSES, SENSITIVITY, normalize_config
        return {"config": normalize_config(self.station_config.get("day_objects")),
                "classes": list(CLASSES), "presets": SENSITIVITY}

    def set_day_objects_config(self, **fields: Any) -> Dict[str, Any]:
        """Merge the given fields into the saved config, validate/clamp, persist and apply to the live tracker and log.
        A min_contrast_dn of None (explicit) goes back to the sensitivity preset."""
        from src.analytics.day_objects import normalize_config
        merged = {**(self.station_config.get("day_objects") or {}), **fields}
        self.station_config["day_objects"] = cfg = normalize_config(merged)
        self._save_station_config()
        if getattr(self, "day_objects", None) is not None:
            self.day_objects.configure(cfg)
        self.day_object_log()  # re-applies retention and size cap
        return {"success": True, **self.get_day_objects_config()}

    def _cloud_event(self, frame: np.ndarray, cc: float, now: float) -> None:
        """Clouds arriving / clearing, from the (already sampled) cloud cover, with hysteresis."""
        state = "cloudy" if cc >= 60.0 else "clear" if cc <= 30.0 else self._cloud_state
        prev, self._cloud_state = self._cloud_state, state
        if prev is None or state == prev or not self.get_event_config()["enabled"].get("clouds"):
            return
        h, w = frame.shape[:2]
        overview = cv2.resize(frame, (640, max(1, round(h * 640 / w))), interpolation=cv2.INTER_AREA) if w > 640 else frame
        self._record_sky_event({
            "type": "clouds", "ts": now, "start_ts": now, "frames": 1, "state": state,
            "cloud_cover_pct": round(cc, 1), "_overview": overview,
        })

    def _record_sky_event(self, ev: Dict[str, Any]) -> Dict[str, Any]:
        """Save the event's images + a JSON line under events/<local day>/ and log it."""
        import json

        try:
            self._match_flight(ev)
        except Exception:
            pass  # flight naming is a bonus; never lose the event over it
        crop, overview = ev.pop("_crop", None), ev.pop("_overview", None)
        dt = datetime.fromtimestamp(float(ev.get("start_ts", ev["ts"]))).astimezone()
        day = dt.strftime("%Y-%m-%d")
        self._event_seq = (self._event_seq + 1) % 1000
        eid = f"{ev['type']}_{dt.strftime('%Y%m%d_%H%M%S')}_{self._event_seq:03d}"
        # Daily cap: a false-alarm storm must not fill the disk (today's files escape the size rule)
        count_day, count = getattr(self, "_events_saved", (None, 0))
        count = count + 1 if count_day == day else 1
        self._events_saved = (day, count)
        cap = int(self.retention.policy.get("max_events_per_day", 1000))
        if count > cap:
            if count == cap + 1:
                self.activity.warning("events", f"{cap} events saved today; further events are not saved until tomorrow")
            return {"event_id": eid, "type": ev["type"], "dropped": True}
        day_dir = os.path.join(self.events_dir, day)
        os.makedirs(day_dir, exist_ok=True)
        rec = {"event_id": eid, "type": ev["type"], "timestamp": dt.isoformat(),
               **{k: v for k, v in ev.items() if k not in ("type", "ts")}}
        if crop is not None and cv2.imwrite(os.path.join(day_dir, f"{eid}.jpg"), crop, [cv2.IMWRITE_JPEG_QUALITY, 90]):
            rec["thumbnail_url"] = f"/api/events/img/{day}/{eid}.jpg"
        if overview is not None and cv2.imwrite(os.path.join(day_dir, f"{eid}_sky.jpg"), overview, [cv2.IMWRITE_JPEG_QUALITY, 85]):
            rec["sky_url"] = f"/api/events/img/{day}/{eid}_sky.jpg"
            rec.setdefault("thumbnail_url", rec["sky_url"])
        with open(os.path.join(day_dir, "events.jsonl"), "a", encoding="utf-8") as fp:
            fp.write(json.dumps(rec) + "\n")
        with self._events_lock:
            if self._events_cache is not None:
                self._events_cache.insert(0, rec)
                del self._events_cache[self.EVENT_KEEP_IN_MEMORY:]
        if ev["type"] == "clouds":
            msg = f"Sky turned {ev['state']} (cloud cover {ev['cloud_cover_pct']:.0f}%)"
        elif ev["type"] == "flash":
            msg = f"Flash: sky brightness {ev['sky_mean_before']} -> {ev['sky_mean']}"
        else:
            msg = (f"{ev['type'].capitalize()} detected: {ev.get('length_px', 0):.0f} px streak"
                   + (f" over {ev['frames']} frames" if ev.get("frames", 1) > 1 else ""))
            fl = ev.get("flight")
            if fl:
                msg += (f" — flight {fl.get('callsign') or fl['icao']}, {fl['altitude_m']} m, {fl['distance_km']} km"
                        + (f" (was {ev['reclassified_from']})" if ev.get("reclassified_from") else ""))
        self.activity.info("events", msg, event_id=eid)
        return rec

    def _load_events(self) -> List[Dict[str, Any]]:
        import json

        with self._events_lock:
            if self._events_cache is None:
                items: List[Dict[str, Any]] = []
                days = sorted(os.listdir(self.events_dir), reverse=True) if os.path.isdir(self.events_dir) else []
                for day in days:
                    try:
                        with open(os.path.join(self.events_dir, day, "events.jsonl"), encoding="utf-8") as fp:
                            day_items = [json.loads(line) for line in fp if line.strip()]
                    except (OSError, ValueError):
                        continue
                    items.extend(reversed(day_items))
                    if len(items) >= self.EVENT_KEEP_IN_MEMORY:
                        break
                self._events_cache = items[: self.EVENT_KEEP_IN_MEMORY]
            return list(self._events_cache)

    def get_events(self, limit: int = 20, event_type: Optional[str] = None, since: Optional[float] = None,
                   until: Optional[float] = None, brief: bool = False) -> Dict[str, Any]:
        """Detected sky events, newest first (optionally one type, a time window, or just what a timeline needs)."""
        events = self._load_events()
        counts: Dict[str, int] = {}
        for e in events:
            counts[e.get("type", "?")] = counts.get(e.get("type", "?"), 0) + 1
        if event_type:
            events = [e for e in events if e.get("type") == event_type]
        if since is not None or until is not None:
            events = [e for e in events if (since is None or self._event_ts(e) >= since) and (until is None or self._event_ts(e) <= until)]
        out = events[:limit]
        if brief:
            def callsign(e):  # a meteor / satellite is not a flight: its "nearest aircraft" is only a diagnostic
                if e.get("type") != "plane":
                    return None
                return (e.get("flight") or e.get("nearest_flight") or {}).get("callsign")

            out = [{"event_id": e.get("event_id"), "type": e.get("type"), "start_ts": self._event_ts(e),
                    "end_ts": e.get("end_ts"), "label": callsign(e),
                    "thumbnail_url": e.get("thumbnail_url"), "sky_url": e.get("sky_url"),
                    "line": e.get("line"), "moving_dot": bool(e.get("moving_dot")), "frames": e.get("frames"),
                    "length_px": e.get("length_px"), "peak_dn": e.get("peak_dn"),
                    "flight": callsign(e)} for e in out]
        return {"events": out, "total_count": len(events), "counts": counts, **self.get_event_config()}

    @staticmethod
    def _event_ts(e: Dict[str, Any]) -> float:
        if e.get("start_ts") is not None:
            return float(e["start_ts"])
        try:
            return datetime.fromisoformat(str(e.get("timestamp"))).timestamp()
        except ValueError:
            return 0.0

    def frame_near(self, ts: float) -> Optional[Dict[str, Any]]:
        """The archived frame closest to a moment (epoch seconds), with the gap to it."""
        import bisect

        dt = datetime.fromtimestamp(ts).astimezone()
        key = f"frame_{dt:%Y%m%d_%H%M%S}"
        best = None
        for off in (-1, 0):   # the observing day of a time before noon may be the previous date
            day = (dt.date() + timedelta(days=off)).strftime("%Y-%m-%d")
            day_dir = os.path.join(self.frames_archive_root, day)
            if not os.path.isdir(day_dir):
                continue
            names = frame_codec.list_frames(day_dir)
            i = bisect.bisect_left(names, key)
            for j in (i - 1, i):
                if 0 <= j < len(names):
                    try:
                        t = datetime.strptime(names[j][6:21], "%Y%m%d_%H%M%S").astimezone().timestamp()
                    except ValueError:
                        continue
                    if best is None or abs(t - ts) < abs(best[0] - ts):
                        best = (t, day, names[j])
        if best is None:
            return None
        t, day, name = best
        return {"ts": t, "delta_s": round(t - ts, 1), "day": day, "name": name, "url": f"/api/frames/img/{day}/{name}"}

    def frames_span(self, start: float, end: float, max_n: int = 600) -> List[Dict[str, Any]]:
        """Archived frames between two moments (epoch seconds), oldest first, thinned evenly to max_n."""
        import bisect

        d0, d1 = datetime.fromtimestamp(start).astimezone(), datetime.fromtimestamp(end).astimezone()
        k0, k1 = f"frame_{d0:%Y%m%d_%H%M%S}", f"frame_{d1:%Y%m%d_%H%M%S}~"
        out: List[Dict[str, Any]] = []
        day = d0.date() - timedelta(days=1)
        while day <= d1.date():
            ds = day.strftime("%Y-%m-%d")
            day_dir = os.path.join(self.frames_archive_root, ds)
            if os.path.isdir(day_dir):
                names = frame_codec.list_frames(day_dir)
                for name in names[bisect.bisect_left(names, k0):bisect.bisect_right(names, k1)]:
                    try:
                        t = datetime.strptime(name[6:21], "%Y%m%d_%H%M%S").astimezone().timestamp()
                    except ValueError:
                        continue
                    out.append({"ts": t, "url": f"/api/frames/img/{ds}/{name}"})
            day += timedelta(days=1)
        out.sort(key=lambda f: f["ts"])
        if len(out) > max_n:
            step = len(out) / max_n
            out = [out[int(i * step)] for i in range(max_n)]
        return out

    def frame_image_path(self, day: str, name: str) -> Optional[str]:
        import re

        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) or not frame_codec.FRAME_NAME_RE.fullmatch(name):
            return None
        path = os.path.join(self.frames_archive_root, day, name)
        return path if os.path.isfile(path) else None

    def event_image_path(self, day: str, name: str) -> Optional[str]:
        import re

        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) or not re.fullmatch(r"[a-z]+_\d{8}_\d{6}_\d{3}(_sky)?\.jpg", name):
            return None
        path = os.path.join(self.events_dir, day, name)
        return path if os.path.isfile(path) else None
