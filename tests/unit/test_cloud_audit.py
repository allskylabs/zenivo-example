"""Cloud-cover audit: 70 labelled frames of the Hanoi station (tests/fixtures/cloud_audit, see docs/design/cloud-audit.md).

Agreement = the model's class (clear <25 / partly <70 / overcast) equals the hand label's class. Before the rewrite the
deployed value agreed on 38.6 % of the frames; the new rules must stay far above that, per condition."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
import cloud_audit_eval as ev  # noqa: E402
from src.analytics.cloud_cover import analyze_cloud_cover  # noqa: E402


def _legacy(frame, sun, malt, mill, mask, circle):
    return analyze_cloud_cover(frame, 0.01, 0.0, sun, sky_polygons=mask, circle=circle, count_stars=False,
                               legacy=True)["cloud_cover_pct"]


def test_audit_agreement_beats_the_deployed_values_and_the_legacy_rules():
    rs = ev.rows()
    new = ev.score(rs, "value")
    assert ev.score(rs, "deployed")["agree_pct"] < 45  # the frozen "before" reference
    assert new["agree_pct"] >= 85 and new["mae"] <= 13
    legacy = ev.score(ev.rows(_legacy), "value")
    assert new["agree_pct"] > legacy["agree_pct"] + 15
    floors = {"day (sun>0)": 85, "twilight (-12..0)": 60, "night moonless (moon<0)": 50, "night moon up": 80}
    for name, g in ev.groups(rs).items():
        if g and name in floors:
            assert ev.score(g, "value")["agree_pct"] >= floors[name], name


def test_moonlit_clear_nights_are_not_overcast():
    # failure mode (a): moon glare hid the stars, so clear moonlit nights read overcast
    clear = [r for r in ev.rows() if r["sun"] <= -12 and r["moon"] > 0 and r["label"] < 25]
    assert clear and all(r["value"] is not None and r["value"] < 50 for r in clear)


def test_overcast_labelled_frames_never_read_clear():
    # Known miss (docs/design/cloud-audit.md): moonlit thin overcast at night,
    # frame 20261123_013441_884, reads ~20 because the cloud is lit by the moon.
    known = {"20261123_013441_884.jpg"}
    bad = [r["frame"] for r in ev.rows() if r["label"] >= 90 and r["value"] is not None
           and r["value"] < 25 and r["frame"] not in known]
    assert not bad, bad


def test_smoothing_median_and_hysteresis():
    from types import SimpleNamespace
    from src.api.service import AllSkyService as S
    o = SimpleNamespace(performance={"cloud_day_abs": 1}, DAY_SMOOTH_N=5, CLOUD_HYST_PCT=5.0)
    f = lambda cc, m="night": S._smooth_day_cloud(o, {"cloud_cover_pct": cc, "method": m, "status": "x"})
    for v in (10, 12, 11):
        r = f(v)
    assert r["status"] == "clear"
    assert f(90)["cloud_cover_pct"] == 12          # one outlier cannot move the median
    assert f(26)["status"] == "clear"              # 26 is inside the 5 % hysteresis band
    assert f(80, "day")["cloud_cover_pct"] == 80   # method change restarts the history


def test_pi_imx708_frames_agree():
    """33 frames of the Pi (imx708, rectilinear 66x41 deg, teal/neutral white balance): the B/R rules need no per-sensor
    calibration there. The set has no clear sky (Nov 2026 was overcast); 11-21 13:00 is the blue-gap partly case."""
    import csv
    import json
    from datetime import datetime, timedelta, timezone

    import cv2
    from src.calibration.auto_exposure import calculate_solar_elevation

    d = os.path.join(os.path.dirname(__file__), "..", "fixtures", "cloud_audit_pi")
    st = json.load(open(os.path.join(d, "station.json")))
    tz = timezone(timedelta(hours=st["tz_hours"]))
    rs = []
    for r in csv.DictReader(open(os.path.join(d, "labels.csv"))):
        t = datetime.strptime(r["frame"][6:21], "%Y%m%d_%H%M%S").replace(tzinfo=tz)
        sun = calculate_solar_elevation(st["lat"], st["lon"], t)
        res = analyze_cloud_cover(cv2.imread(os.path.join(d, r["frame"])), 0.1, 0.0, sun, sky_polygons=st["sky_mask"],
                                  circle=None, count_stars=False)
        rs.append({"frame": r["frame"], "label": float(r["label_pct"]), "value": res["cloud_cover_pct"]})
    sc = ev.score(rs, "value")
    assert sc["n"] == 33 and sc["agree_pct"] >= 90 and sc["mae"] <= 15, sc
    gap = next(r for r in rs if "20261121_130020" in r["frame"])
    assert 25 <= gap["value"] < 85
