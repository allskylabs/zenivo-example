import json
import os
import time

from src.api.capture_mixin import CaptureMixin


class _Cam:
    def __init__(self):
        self.set = None

    def get_limits(self):
        return {"min_exposure_s": 0.0001, "max_exposure_s": 60.0, "min_gain_db": 0.0, "max_gain_db": 72.0}

    def set_hardware_exposure(self, e, g):
        self.set = (e, g)


class _Act:
    def info(self, *a, **k):
        pass


class _AE:
    exposure_s = 0.04
    gain_db = 0.0


class _Svc(CaptureMixin):
    def __init__(self, d):
        self.output_dir, self.mode, self.camera, self.auto_exposure, self.activity = d, "auto", _Cam(), _AE(), _Act()


def _write(d, age, exp=10.0, gain=26.4):
    with open(os.path.join(d, "ae_state.json"), "w") as fh:
        json.dump({"t": time.time() - age, "exposure_s": exp, "gain_db": gain}, fh)


def test_recent_state_is_resumed(tmp_path, monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    _write(tmp_path, 300)
    s = _Svc(str(tmp_path))
    assert s._restore_ae_state() and s.camera.set == (10.0, 26.4) and s.auto_exposure.exposure_s == 10.0


def test_old_or_out_of_range_state_is_ignored(tmp_path, monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    _write(tmp_path, 3 * 3600)
    assert not _Svc(str(tmp_path))._restore_ae_state()
    _write(tmp_path, 60, exp=500.0)
    assert not _Svc(str(tmp_path))._restore_ae_state()


def test_save_is_throttled(tmp_path):
    s = _Svc(str(tmp_path))
    s._save_ae_state(2.0, 12.0)
    s._save_ae_state(3.0, 14.0)
    assert json.load(open(tmp_path / "ae_state.json"))["exposure_s"] == 2.0


def test_exposure_warning_only_when_something_is_locked():
    from types import SimpleNamespace
    from src.api.exposure_mixin import ExposureMixin as M
    ae = SimpleNamespace(target_brightness=None, solar_elevation=-30.0, lock_name=lambda: None)
    svc = SimpleNamespace(mode="auto", auto_exposure=ae, last_brightness=250.0, **{k: getattr(M, k) for k in
                          ("WARN_BRIGHT", "WARN_DARK_DAY", "WARN_DARK_NIGHT", "WARN_OFF_TARGET")})
    warn = lambda: M.exposure_warning(svc)
    assert warn() is None                                  # auto: AE will correct it
    ae.lock_name = lambda: "exposure"
    assert warn()["kind"] == "bright"
    svc.last_brightness = 1.0
    assert warn()["kind"] == "dark"
    svc.last_brightness = 60.0
    assert warn() is None
    ae.target_brightness = 130.0
    assert warn()["kind"] == "off_target"
