"""Gallery history: dated products from previous days are listed, newest first, today excluded."""

import os

from src.api.service import AllSkyService


def _touch(path, size=1024):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\0" * size)


def test_past_products_grouped_by_day(tmp_path, monkeypatch):
    AllSkyService.reset_instance()
    svc = AllSkyService.get_instance()
    for name in ("timelapses", "keograms", "startrails"):
        monkeypatch.setattr(svc, f"{name}_dir", str(tmp_path / name))

    _touch(str(tmp_path / "timelapses" / "2026-10-14.mp4"), 2048)
    _touch(str(tmp_path / "timelapses" / "2026-10-16.mp4"))
    _touch(str(tmp_path / "timelapses" / "2026-10-17.mp4"))   # today: excluded
    _touch(str(tmp_path / "timelapses" / "latest.mp4"))       # not dated: ignored
    _touch(str(tmp_path / "keograms" / "2026-10-16.jpg"))
    _touch(str(tmp_path / "startrails" / "2026-10-15.jpg"))
    _touch(str(tmp_path / "startrails" / "2026-10-16.mp4"))   # wrong extension: ignored

    days = svc._list_past_products("2026-10-17")
    assert [d["date"] for d in days] == ["2026-10-16", "2026-10-15", "2026-10-14"]
    assert days[0]["timelapse"]["url"] == "/uploads/allsky/timelapses/2026-10-16.mp4"
    assert days[0]["keogram"]["url"] == "/uploads/allsky/keograms/2026-10-16.jpg"
    assert "startrails" not in days[0]
    assert set(days[1]) == {"date", "startrails"}
    assert days[2]["timelapse"]["size_kb"] == 2.0
    AllSkyService.reset_instance()
