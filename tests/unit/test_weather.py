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
