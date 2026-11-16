from datetime import datetime

import pytest

from src import station_tz


@pytest.fixture(autouse=True)
def _restore():
    yield
    station_tz.apply("", 0.0)


def test_iana_zone_is_applied_to_the_process():
    info = station_tz.apply("America/Chicago", -93.0)
    assert info["name"] == "America/Chicago" and info["offset_min"] in (-360, -300)
    assert datetime.now().astimezone().utcoffset().total_seconds() / 60 == info["offset_min"]


def test_coords_gives_a_whole_hour_offset_from_longitude():
    assert station_tz.apply("coords", 105.43)["offset_min"] == 7 * 60
    assert station_tz.apply("coords", -93.2)["offset_min"] == -6 * 60
    assert station_tz.apply("coords", 0.0)["name"] is None


def test_unknown_zone_falls_back_to_the_os_zone_and_names_are_validated():
    assert station_tz.apply("Mars/Olympus", 0.0)["setting"] == ""
    assert station_tz.valid_name("Asia/Ho_Chi_Minh") and not station_tz.valid_name("../etc/passwd")
