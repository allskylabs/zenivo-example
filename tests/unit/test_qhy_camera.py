"""QHY driver against a fake SDK wrapper (no libqhyccd or hardware needed)."""

import threading

import numpy as np
import pytest

from src.hardware import driver as drv
from src.hardware import qhy
from src.hardware.qhy import QhyController


class _FakeSdk:
    def __init__(self, color=True, hang=False, fail_first=False):
        self.color, self.hang, self.fail_first = color, hang, fail_first
        self.params = {}
        self.cancelled = 0
        self.gets = 0
        self.bin = 1
        self.res = None
        self.release = threading.Event()

    def scan(self):
        return [("QHY533C-1234", "QHY533C")]

    def open(self, cam_id):
        return object()

    def close(self, h):
        pass

    def init_camera(self, h):
        pass

    def chip_info(self, h):
        return {"width": 3008, "height": 3008, "pixel_um": 3.76, "bpp": 16}

    def has_control(self, h, control):
        if control == qhy.CAM_COLOR:
            return 4 if self.color else 0xFFFFFFFF  # BAYER_RG, or "not available"
        return 0

    def param_range(self, h, control):
        return {qhy.CONTROL_EXPOSURE: (1.0, 3_600_000_000.0), qhy.CONTROL_GAIN: (0.0, 3000.0)}.get(control)

    def set_param(self, h, control, value):
        self.params[control] = value

    def get_param(self, h, control):
        return 22.5 if control == qhy.CONTROL_CURTEMP else 0.0

    def set_bin(self, h, b):
        self.bin = b

    def set_bits(self, h, bits):
        pass

    def set_resolution(self, h, w, hg):
        self.res = (w, hg)

    def expose(self, h):
        pass

    def get_frame(self, h):
        self.gets += 1
        if self.hang:
            self.release.wait(5)
            raise RuntimeError("cancelled")
        if self.fail_first and self.gets == 1:
            raise RuntimeError("readout failed")
        w, hg = self.res
        return w, hg, 16, 1, np.full((hg, w), 40 * 256, dtype=np.uint16).tobytes()

    def cancel(self, h):
        self.cancelled += 1
        self.release.set()


def _cam(sdk, **kw):
    c = QhyController(**kw)
    c.sdk = sdk
    return c


def test_open_maps_limits_and_gain_range():
    c = _cam(_FakeSdk())
    assert c.open() and (c.width, c.height) == (3008, 3008) and c.bayer == "RGGB"
    assert c.get_limits()["max_exposure_s"] == 3600.0
    info = drv.describe_camera(c)
    assert info["backend"] == "qhy" and info["pixel_pitch_um"] == 3.76 and "colour" in info["sensor"]


def test_gain_db_is_mapped_onto_the_sdk_range_and_exposure_in_us():
    sdk = _FakeSdk()
    c = _cam(sdk)
    c.open()
    out = c.set_hardware_exposure(2.0, 15.0)
    assert out["gain_db"] == 15.0 and sdk.params[qhy.CONTROL_EXPOSURE] == 2_000_000 and sdk.params[qhy.CONTROL_GAIN] == 1500.0
    c.set_hardware_exposure(2.0, 99.0)
    assert c.current_gain_db == qhy.GAIN_DB_SPAN and sdk.params[qhy.CONTROL_GAIN] == 3000.0


def test_colour_frame_is_demosaiced_and_reports_temperature():
    c = _cam(_FakeSdk())
    c.open()
    frame, meta = c.capture_frame()
    assert frame.shape == (3008, 3008, 3) and frame.dtype == np.uint8 and int(frame.mean()) == 40
    assert meta["camera_backend"] == "qhy" and meta["sensor_temp_c"] == 22.5


def test_mono_sensor_uses_hardware_bin_for_a_small_size():
    sdk = _FakeSdk(color=False)
    c = _cam(sdk, width=1500, height=1500)
    c.open()
    assert sdk.bin == 2 and (c.width, c.height) == (1504, 1504)
    colour = _cam(_FakeSdk(color=True), width=1500, height=1500)
    colour.open()
    assert colour.bin == 1  # binning would destroy the Bayer pattern


def test_transient_error_retries_once():
    sdk = _FakeSdk(fail_first=True)
    c = _cam(sdk)
    c.open()
    frame, _ = c.capture_frame()
    assert sdk.gets == 2 and sdk.cancelled == 1 and frame.shape == (3008, 3008, 3)


def test_hung_camera_times_out_and_is_cancelled(monkeypatch):
    sdk = _FakeSdk(hang=True)
    c = _cam(sdk)
    c.open()
    monkeypatch.setattr(QhyController, "_read_once", QhyController._read_once)
    c.current_exposure_s = -7.0  # deadline = 2 * -7 + 15 = 1 s
    with pytest.raises(TimeoutError):
        c._read()
    assert sdk.cancelled >= 1


def test_factory_qhy_and_fallback(monkeypatch):
    monkeypatch.setattr(qhy, "_get_sdk", lambda: _FakeSdk())
    assert drv.get_camera_driver(camera_type="qhy").backend == "qhy"
    monkeypatch.setattr(qhy, "_get_sdk", lambda: (_ for _ in ()).throw(RuntimeError("SDK missing")))
    with pytest.raises(RuntimeError):
        drv.get_camera_driver(camera_type="qhy", fallback_to_simulator=False)
    assert "Simulated" in drv.get_camera_driver(camera_type="qhy").__class__.__name__


def test_list_devices_includes_qhy(monkeypatch):
    monkeypatch.setattr(qhy, "_get_sdk", lambda: _FakeSdk())
    monkeypatch.setattr(drv, "find_rts5869_device_node", lambda: None)
    assert any(d["id"] == "qhy:0" and d["name"] == "QHY533C" for d in drv.list_camera_devices())
