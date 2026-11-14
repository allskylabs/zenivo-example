import json

import run_allsky


def test_saved_station_position(tmp_path):
    p = tmp_path / "station_config.json"
    assert run_allsky.saved_station_position(str(p)) is None  # missing file
    p.write_text("not json")
    assert run_allsky.saved_station_position(str(p)) is None  # unreadable
    p.write_text(json.dumps({"latitude_deg": 0.0, "longitude_deg": 0.0, "geo_source": "default"}))
    assert run_allsky.saved_station_position(str(p)) is None  # factory placeholder
    p.write_text(json.dumps({"latitude_deg": 48.5, "longitude_deg": 7.5, "geo_source": "manual"}))
    assert run_allsky.saved_station_position(str(p)) == (48.5, 7.5)
