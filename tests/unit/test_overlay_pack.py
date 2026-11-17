from src.api import overlay_pack as op


def _air(x=0.5, alt=1000):
    return {"t": 100.123, "enabled": True, "connected": True, "total": 2, "in_view": 1, "fr24": {"email": "x"},
            "aircraft": [{"icao": "A1", "callsign": "HVN1", "altitude_m": alt, "distance_km": 10.0, "frame_xy": [x, 0.25],
                          "frame_v": [-0.0004, 0.0013], "in_view": True, "above_horizon": True, "provider": "p"},
                         {"icao": "B2", "callsign": None, "altitude_m": 9000, "distance_km": 80.0, "in_view": False, "above_horizon": True}]}


def test_snapshot_then_only_changed_rows_and_removals():
    msg, state = op.delta("aircraft", _air(), None)
    f = op.FIELDS["aircraft"]
    assert msg["f"] == 1 and len(msg["u"]) == 2 and "fr24" not in msg["m"] and msg["m"]["total"] == 2
    row = dict(zip(f, msg["u"][0]))
    assert (row["fx"], row["fy"], row["vx"], row["vy"], row["fl"]) == (5000, 2500, -4, 13, 3)
    assert dict(zip(f, msg["u"][1]))["fx"] is None
    same, state = op.delta("aircraft", _air(), state)
    assert same is None                                   # nothing changed: nothing is sent
    moved, state = op.delta("aircraft", _air(x=0.6), state)
    assert [r[0] for r in moved["u"]] == ["A1"] and "m" not in moved and "x" not in moved
    gone = _air(x=0.6)
    gone["aircraft"].pop()
    gone["total"] = 1
    msg, state = op.delta("aircraft", gone, state)
    assert msg["x"] == ["B2"] and msg["m"] == {"total": 1} and "u" not in msg


def test_satellites_keep_those_in_the_frame_by_day_too():
    payload = {"sun_alt_deg": 16.2, "camera": {"lens": {}}, "satellites": [
        {"norad": 1, "name": "GEO", "height_km": 35786, "frame_xy": [0.4, 0.3], "in_view": True, "visible": False},
        {"norad": 2, "name": "LOW", "height_km": 600, "in_view": False, "visible": False}]}
    msg, _ = op.delta("satellites", payload, None)
    assert [r[0] for r in msg["u"]] == [1, 2] and "camera" not in msg["m"]   # the list card shows those outside the frame too
    assert dict(zip(op.FIELDS["satellites"], msg["u"][0]))["fl"] == 1    # in view, not visible to the eye
