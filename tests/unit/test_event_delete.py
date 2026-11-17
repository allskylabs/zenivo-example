"""Deleting a saved sky event: its line and image files go, the other events stay (EventsMixin.delete_event / get_event)."""
import json
import threading

import pytest

from src.api.events_mixin import EventsMixin


class _Activity:
    def __init__(self):
        self.lines = []

    def info(self, cat, msg, **kw):
        self.lines.append((cat, msg, kw))


class _Svc(EventsMixin):
    EVENT_KEEP_IN_MEMORY = 100

    def __init__(self, root):
        self.events_dir = str(root)
        self._events_lock = threading.Lock()
        self._events_cache = None
        self.activity = _Activity()


@pytest.fixture
def svc(tmp_path):
    day = tmp_path / "2026-11-16"
    day.mkdir()
    recs = [{"event_id": "meteor_20261116_021408_001", "type": "meteor", "timestamp": "2026-11-16T02:14:08+07:00"},
            {"event_id": "plane_20261116_030000_002", "type": "plane", "timestamp": "2026-11-16T03:00:00+07:00"}]
    (day / "events.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    for name in ("meteor_20261116_021408_001.jpg", "meteor_20261116_021408_001_sky.jpg", "plane_20261116_030000_002.jpg"):
        (day / name).write_bytes(b"x")
    return _Svc(tmp_path)


def test_delete_removes_record_and_images_and_keeps_the_rest(svc, tmp_path):
    day = tmp_path / "2026-11-16"
    svc._load_events()   # warm the in-memory list so the cache update is exercised too
    assert svc.get_event("meteor_20261116_021408_001")["type"] == "meteor"
    assert svc.delete_event("meteor_20261116_021408_001") is True
    assert sorted(p.name for p in day.iterdir()) == ["events.jsonl", "plane_20261116_030000_002.jpg"]
    assert [e["event_id"] for e in svc._load_events()] == ["plane_20261116_030000_002"]
    assert svc.get_event("meteor_20261116_021408_001") is None
    assert svc.activity.lines and svc.activity.lines[0][2]["event_id"] == "meteor_20261116_021408_001"


@pytest.mark.parametrize("bad", ["", "../../etc/passwd", "meteor_20261116_021408_999", "meteor_2026-11-16_1_1", "METEOR_20261116_021408_001"])
def test_delete_unknown_or_malformed_id_is_refused(svc, bad):
    assert svc.delete_event(bad) is False
