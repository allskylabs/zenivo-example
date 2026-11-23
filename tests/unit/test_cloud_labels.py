"""Day cloud cover on the hand-labelled thumbnails: the absolute blue-sky test must beat the old relative map."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
import cloud_label_eval as ev  # noqa: E402


def test_day_absolute_method_beats_relative_on_labelled_frames():
    old = ev.summary(ev.day(ev.evaluate(False)))
    new = ev.summary(ev.day(ev.evaluate(True)))
    assert new["mae"] < 15 and new["mae"] < old["mae"]
    assert new["overcast_below_50"] == 0
