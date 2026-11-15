"""Settings > Integrations: satellite source settings and the status of every outside data source."""

import os
import time
from typing import Any, Dict, List, Optional

SAT_DEFAULTS = {"enabled": 1, "groups": ["stations", "visual"], "refresh_h": 24, "tle_url": ""}
REFRESH_CHOICES_H = (6, 12, 24, 72, 168)


class DataSourcesMixin:
    def _sat_tracker_get(self):
        from src.analytics.satellites import SatelliteTracker

        if getattr(self, "_sat_tracker", None) is None:
            self._sat_tracker = SatelliteTracker(os.path.join(self.output_dir, "cache", "sat_tle.json"))
            self._apply_satellite_config()
        return self._sat_tracker

    def _satellite_settings(self) -> Dict[str, Any]:
        cfg = dict(SAT_DEFAULTS)
        cfg.update(self.station_config.get("satellites") or {})
        return cfg

    def _apply_satellite_config(self) -> None:
        cfg = self._satellite_settings()
        self._sat_tracker.configure(groups=cfg["groups"], refresh_s=float(cfg["refresh_h"]) * 3600.0,
                                    url_template=cfg["tle_url"] or None)

    def get_satellites_config(self) -> Dict[str, Any]:
        from src.analytics.satellites import GROUP_CHOICES, TLE_URL

        cfg = self._satellite_settings()
        return {**cfg, "group_choices": list(GROUP_CHOICES), "refresh_choices_h": list(REFRESH_CHOICES_H),
                "default_url": TLE_URL, "status": self._sat_tracker_get().status()}

    def set_satellites_config(self, enabled: Optional[bool] = None, groups: Optional[List[str]] = None,
                              refresh_h: Optional[int] = None, tle_url: Optional[str] = None) -> Dict[str, Any]:
        from src.analytics.satellites import GROUP_CHOICES
        from src.community import netguard

        cfg = self._satellite_settings()
        if enabled is not None:
            cfg["enabled"] = int(bool(enabled))
        if groups is not None:
            bad = [g for g in groups if g not in GROUP_CHOICES]
            if bad or not groups:
                raise ValueError(f"groups must be one or more of: {', '.join(GROUP_CHOICES)}")
            cfg["groups"] = [g for g in GROUP_CHOICES if g in groups]
        if refresh_h is not None:
            if int(refresh_h) not in REFRESH_CHOICES_H:
                raise ValueError(f"refresh_h must be one of {list(REFRESH_CHOICES_H)}")
            cfg["refresh_h"] = int(refresh_h)
        if tle_url is not None:
            tle_url = tle_url.strip()
            if tle_url:
                if not tle_url.startswith("https://") or "{group}" not in tle_url:
                    raise ValueError("the TLE source must be an https:// address containing {group}")
                try:
                    netguard.safe_url(tle_url.replace("{group}", "stations"), allow_private=False)
                except Exception as exc:
                    raise ValueError(f"the TLE source cannot be used: {exc}")
            cfg["tle_url"] = tle_url
        self.station_config["satellites"] = cfg
        self._save_station_config()
        self._sat_tracker_get()
        self._apply_satellite_config()
        self.activity.info("station", f"Satellite settings saved: {'on' if cfg['enabled'] else 'off'}, "
                           f"groups {', '.join(cfg['groups'])}, refresh {cfg['refresh_h']} h")
        return {"success": True, **self.get_satellites_config()}

    def refresh_satellites(self) -> Dict[str, Any]:
        started = self._sat_tracker_get().refresh_now()
        return {"success": True, "started": started, "status": self._sat_tracker_get().status()}

    def get_data_sources(self) -> Dict[str, Any]:
        """One line per outside source: whether it is on, whether it works, and what it costs in privacy terms."""
        now = time.time()
        out: List[Dict[str, Any]] = []

        def add(sid, name, enabled, state, detail, host, where):
            out.append({"id": sid, "name": name, "enabled": bool(enabled), "state": state, "detail": detail,
                        "host": host, "where": where})

        adsb = self.get_adsb_config()
        st = adsb.get("status") or {}
        if not adsb.get("host"):
            add("adsb", "ADS-B receiver (your own)", False, "off", "No receiver address set", "", "integrations")
        elif st.get("connected"):
            add("adsb", "ADS-B receiver (your own)", True, "ok", f"{st.get('aircraft', 0)} aircraft, "
                f"{st.get('with_position', 0)} with position", f"{adsb['host']}:{adsb['port']}", "integrations")
        else:
            add("adsb", "ADS-B receiver (your own)", bool(adsb.get("enabled")), "error" if adsb.get("enabled") else "off",
                st.get("last_error") or "Not connected", f"{adsb['host']}:{adsb['port']}", "integrations")
        pub = adsb.get("public_status") or {}
        if not adsb.get("public_fallback"):
            add("adsb_public", "Public ADS-B network (fallback)", False, "off", "Off: nothing is sent", "", "integrations")
        else:
            state = "ok" if pub.get("active") or pub.get("last_ok") else "waiting"
            if pub.get("last_error") and not pub.get("active"):
                state = "error"
            add("adsb_public", "Public ADS-B network (fallback)", True, state,
                pub.get("last_error") or ("In use" if pub.get("active") else "Standing by while your receiver works"),
                pub.get("provider") or "", "integrations")
        fr = adsb.get("fr24") or {}
        add("fr24", "Flightradar24 (your account)", adsb.get("fr24_enabled"),
            ("error" if fr.get("last_error") else "ok" if fr.get("last_fetch_age_s") is not None else "waiting") if adsb.get("fr24_enabled") else "off",
            (fr.get("last_error") or "") if adsb.get("fr24_enabled") else "Off", "flightradar24.com" if adsb.get("fr24_enabled") else "",
            "integrations")
        sat = self.get_satellites_config()
        ss = sat["status"]
        if not sat["enabled"]:
            add("satellites", "Satellites (CelesTrak orbits)", False, "off", "Off", "", "integrations")
        else:
            age = f"updated {int((now - ss['fetched_at']) / 3600)} h ago" if ss.get("fetched_at") else "not downloaded yet"
            add("satellites", "Satellites (CelesTrak orbits)", True, "error" if ss.get("error") and not ss["count"] else "ok" if ss["count"] else "waiting",
                f"{ss['count']} satellites, {age}" + (f". Last error: {ss['error']}" if ss.get("error") else ""), ss.get("source") or "", "integrations")
        sondes_on = bool(adsb.get("radiosondes_enabled"))
        feed = getattr(self, "_sonde_feed", None)
        add("radiosondes", "Weather balloons (SondeHub)", sondes_on, ("error" if feed is not None and getattr(feed, "_error", "") else "ok" if feed is not None and getattr(feed, "_fetched", 0) else "waiting") if sondes_on else "off",
            (getattr(feed, "_error", "") if feed is not None else "")
            or ("Off" if not sondes_on else f"last answer {int(now - feed._fetched)} s ago" if feed is not None and getattr(feed, "_fetched", 0)
                else "Waiting for the first request (a Live page must be open)"),
            "api.v2.sondehub.org" if sondes_on else "", "integrations")
        apath = os.path.join(self.output_dir, "cache", "airports_near.json")
        have = os.path.isfile(apath)
        add("airports", "Airports and runways (OurAirports)", True, "ok" if have else "waiting",
            f"cached {int((now - os.path.getmtime(apath)) / 86400)} days ago" if have else "Not downloaded yet (needs a station position)",
            "davidmegginson.github.io", "station")
        return {"sources": out, "t": now}
