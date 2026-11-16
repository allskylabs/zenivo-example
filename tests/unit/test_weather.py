from datetime import datetime, timezone

from src import weather as wx


def _payload(base):
    times = [datetime.fromtimestamp(base + 3600 * i, timezone.utc).strftime("%Y-%m-%dT%H:00") for i in range(72)]
    n = len(times)
    return {"current": {"temperature_2m": 24.46, "relative_humidity_2m": 80, "cloud_cover": 10, "weather_code": 1, "is_day": 0,
                        "wind_speed_10m": 2.0, "surface_pressure": 1008.2, "visibility": 24000},
            "hourly": {"time": times, "cloud_cover": [10] * 6 + [90] * (n - 6), "precipitation_probability": [0] * n,
                       "precipitation": [0] * n, "temperature_2m": [20] * n, "wind_speed_10m": [1] * n,
                       "visibility": [20000] * n, "weather_code": [1] * n}}


def test_parse_current_hourly_and_outlook():
    base = int(datetime(2026, 11, 17, 12, tzinfo=timezone.utc).timestamp())
    out = wx.parse(_payload(base), now=base)
    assert out["current"]["summary"] == "Mostly clear" and out["current"]["temp_c"] == 24.5
    assert len(out["hourly"]) == wx.HOURS and out["hourly"][0]["t"] == base
    assert out["outlook"]["clear_hours_next_12"] == 6 and out["outlook"]["rain_expected_next_12"] is False


def test_parse_rejects_bad_answer():
    import pytest
    with pytest.raises(ValueError):
        wx.parse({"error": True})


def test_settings_validate_and_default_off(tmp_path):
    saved = {}
    m = wx.WeatherManager(str(tmp_path), lambda: saved, saved.update, lambda: None)
    assert m.status()["enabled"] is False
    m.set_settings(refresh_min=60)
    assert saved["refresh_min"] == 60 and saved["enabled"] == 0
    import pytest
    with pytest.raises(ValueError):
        m.set_settings(refresh_min=7)


def test_metno_parse_and_fallback_when_open_meteo_refuses(tmp_path, monkeypatch):
    import json
    base = int(datetime(2026, 11, 17, 12, tzinfo=timezone.utc).timestamp())
    ts = [{"time": datetime.fromtimestamp(base + 3600 * i, timezone.utc).strftime("%Y-%m-%dT%H:00:00Z"),
           "data": {"instant": {"details": {"air_temperature": 21.0, "cloud_area_fraction": 10.0, "relative_humidity": 70,
                                            "wind_speed": 2.0, "wind_from_direction": 90, "air_pressure_at_sea_level": 1010}},
                    "next_1_hours": {"summary": {"symbol_code": "partlycloudy_day"}, "details": {"precipitation_amount": 0.0}}}}
          for i in range(60)]
    out = wx.parse_metno({"properties": {"timeseries": ts}}, now=base)
    assert out["current"]["summary"] == "Partly cloudy" and len(out["hourly"]) == wx.HOURS and out["outlook"]["clear_hours_next_12"] == 12

    def fake_get(url, headers, timeout):
        if "open-meteo" in url:
            raise OSError("HTTP Error 429")
        return 200, json.dumps({"properties": {"timeseries": ts}}).encode(), {}
    monkeypatch.setattr(wx.net, "pooled_get", fake_get)
    monkeypatch.setattr(wx.time, "time", lambda: base)
    s = {"enabled": 1}
    m = wx.WeatherManager(str(tmp_path), lambda: s, s.update, lambda: (21.1, 105.4))
    assert m.refresh() and m.status()["provider"] == "MET Norway" and m.status()["available"]
