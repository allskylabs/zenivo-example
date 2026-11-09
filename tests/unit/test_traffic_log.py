import os
import time
from datetime import datetime

from src.analytics.traffic_log import TrafficLog

AC = [["abc123", "VN123", "VN-A1", "A321", 21.1, 105.4, 3000, 500, 90, 250.0, 70.0, 0.4, 0.5]]


def test_append_and_query_skip_empty(tmp_path):
    tl = TrafficLog(str(tmp_path))
    t = time.time()
    assert tl.append(t, [], []) is False
    assert tl.append(t, AC, []) is True
    assert tl.append(t + 5, [], [[1, "ISS", 100.0, 60.0, 0.5, 0.5, 420, 1]]) is True
    got = tl.query(t - 1, t + 10)
    assert [s["t"] for s in got] == [round(t, 1), round(t + 5, 1)]
    assert got[0]["a"][0][1] == "VN123" and got[1]["s"][0][1] == "ISS"
    assert tl.query(t + 100, t + 200) == []


def test_prune_by_age_and_size(tmp_path):
    tl = TrafficLog(str(tmp_path), {"retention_days": 3, "max_mb": 5})
    now = time.time()
    for d in range(6):
        ts = now - d * 86400.0
        name = datetime.fromtimestamp(ts).strftime("%Y-%m-%d") + ".jsonl"
        with open(os.path.join(tmp_path, name), "w") as fh:
            fh.write("x" * (2 * 1024 * 1024))
    removed = tl.prune(now)
    left = sorted(os.listdir(tmp_path))
    assert removed >= 4
    assert len(left) == 2  # 5 MB cap holds two 2 MB files; today's file is never removed
    assert datetime.fromtimestamp(now).strftime("%Y-%m-%d") + ".jsonl" in left


def test_torn_last_line_is_ignored(tmp_path):
    tl = TrafficLog(str(tmp_path))
    t = time.time()
    tl.append(t, AC, [])
    with open(tl._path(t), "a") as fh:
        fh.write('{"t": 1, "a": [[')
    assert len(tl.query(t - 1, t + 1)) == 1
