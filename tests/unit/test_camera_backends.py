"""Camera backend selection and the Raspberry Pi (Picamera2) / generic UVC drivers."""

import math
import sys
import types

import numpy as np
import pytest

from src.hardware import driver as drv
from src.hardware.picamera import PiCameraController
from src.hardware.simulator import SimulatedIMX335Controller
from src.hardware.uvc_generic import GenericUVCCameraController


class _FakeRequest:
    def __init__(self, cam):
        self.cam = cam

    def get_metadata(self):
        return {
            "ExposureTime": self.cam.controls.get("ExposureTime", 1000),
            "AnalogueGain": self.cam.controls.get("AnalogueGain", 1.0),
            "FrameDuration": self.cam.controls.get("ExposureTime", 1000) + 500,
            "SensorTemperature": 31.0,
        }

    def make_array(self, name):
        w, h = self.cam.config["main"]["size"]
        return np.full((h, w, 3), 40, dtype=np.uint8)

    def release(self):
        pass


class _FakePicamera2:
    def __init__(self, num=0):
        self.camera_properties = {"Model": "imx708_noir", "PixelArraySize": (4608, 2592)}
        self.sensor_modes = [
            {"size": (1536, 864), "crop_limits": (768, 432, 3072, 1728)},
            {"size": (2304, 1296), "crop_limits": (0, 0, 4608, 2592)},
            {"size": (4608, 2592), "crop_limits": (0, 0, 4608, 2592)},
        ]
        self.camera_controls = {"ExposureTime": (26, 112_015_443, None), "AnalogueGain": (1.12, 16.0, None)}
        self.controls = {}
        self.config = None

    @staticmethod
    def global_camera_info():
        return [{"Model": "imx708_noir", "Num": 0}]

    def create_still_configuration(self, main, raw=None, buffer_count=2, queue=False, controls=None):
        return {"main": dict(main), "raw": raw}

    def configure(self, config):
        self.config = config

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass

    def set_controls(self, ctrls):
        self.controls.update(ctrls)

    stalled = False
    flushed = 0

    def capture_request(self, wait=None):
        if self.stalled:
            raise TimeoutError("timed out")
        return _FakeRequest(self)

    def cancel_all_and_flush(self):
        self.flushed += 1


@pytest.fixture
def fake_picamera2(monkeypatch):
    mod = types.ModuleType("picamera2")
    mod.Picamera2 = _FakePicamera2
    monkeypatch.setitem(sys.modules, "picamera2", mod)
    return mod


def test_picamera_picks_binned_full_fov_mode_and_clamps_gain(fake_picamera2):
    cam = PiCameraController()
    cam.open()
    assert (cam.width, cam.height) == (2304, 1296)
    assert cam.max_gain_db == pytest.approx(20 * math.log10(16.0), abs=0.01)

    info = cam.set_hardware_exposure(4.0, 60.0)
    assert info["actual_exposure_s"] == pytest.approx(4.0)
    assert info["gain_db"] == pytest.approx(cam.max_gain_db)
    assert cam.picam.controls["ExposureTime"] == 4_000_000
    assert cam.picam.controls["FrameDurationLimits"][1] >= 4_000_000

    frame, meta = cam.capture_frame()
    assert frame.shape == (1296, 2304, 3)
    assert meta["actual_exposure_s"] == pytest.approx(4.0)
    assert meta["sensor_temp_c"] == 31.0
    assert cam.get_info()["has_registers"] is False
    cam.close()


def test_picamera_capture_times_out_instead_of_hanging(fake_picamera2):
    cam = PiCameraController()
    cam.open()
    cam.picam.stalled = True
    with pytest.raises(TimeoutError):
        cam.capture_frame()
    assert cam.picam.flushed == 1
    cam.close()


def test_factory_auto_selects_picamera_when_no_rts5869(fake_picamera2, monkeypatch):
    monkeypatch.setattr(drv, "find_rts5869_device_node", lambda: None)
    cam = drv.get_camera_driver(camera_type="auto")
    assert isinstance(cam, PiCameraController)
    assert drv.describe_camera(cam)["backend"] == "picamera"
    cam.close()


def test_factory_falls_back_to_simulator(monkeypatch):
    monkeypatch.setattr(drv, "find_rts5869_device_node", lambda: None)
    monkeypatch.setitem(sys.modules, "picamera2", None)  # import fails
    monkeypatch.setattr("src.hardware.uvc_generic.find_uvc_device_nodes", lambda exclude_ids=(): [])
    cam = drv.get_camera_driver(camera_type="auto")
    assert isinstance(cam, SimulatedIMX335Controller)
    assert drv.describe_camera(cam)["backend"] == "simulator"
    assert drv.camera_limits(cam)["max_gain_db"] == 72.0


class _FakeCap:
    def __init__(self):
        self.reads = 0

    def isOpened(self):
        return True

    def set(self, *a):
        return True

    def read(self):
        self.reads += 1
        return True, np.full((480, 640, 3), 100, dtype=np.uint8)

    def grab(self):
        return True

    def release(self):
        pass


def test_uvc_long_exposure_sums_subframes(monkeypatch):
    cam = GenericUVCCameraController(device_path="/dev/video99")
    cam.cap = _FakeCap()
    cam.is_open = True
    cam.hw_max_exposure_s = 0.5
    cam.controls = {}
    cam.set_hardware_exposure(2.0, 0.0)
    frame, meta = cam.capture_frame()
    assert meta["sub_frames"] == 4
    assert meta["actual_exposure_s"] == pytest.approx(2.0)
    assert int(frame[0, 0, 0]) == 255  # 4 x 100 DN saturates


def test_service_leaves_simulator_when_real_camera_appears(monkeypatch, fake_picamera2):
    from src.api import service as svc_mod
    from src.api.service import AllSkyService

    AllSkyService.reset_instance()
    svc = AllSkyService.get_instance()
    monkeypatch.setattr(svc, "_save_station_config", lambda: None)
    assert isinstance(svc.camera, SimulatedIMX335Controller)

    monkeypatch.setattr(svc_mod, "get_camera_driver", lambda **kw: (_ for _ in ()).throw(RuntimeError("busy")))
    svc._camera_driver_kwargs = {"camera_type": "picamera"}
    with pytest.raises(RuntimeError):
        svc.try_reconnect_camera()
    assert isinstance(svc.camera, SimulatedIMX335Controller)

    def real(**kw):
        cam = PiCameraController()
        cam.open()
        return cam

    monkeypatch.setattr(svc_mod, "get_camera_driver", real)
    info = svc.try_reconnect_camera()
    assert info["backend"] == "picamera" and isinstance(svc.camera, PiCameraController)
    assert svc.station_config["camera_sensor"]["width_px"] == 2304
    AllSkyService.reset_instance()


def test_factory_can_refuse_simulator_fallback(monkeypatch):
    monkeypatch.setattr(drv, "find_rts5869_device_node", lambda: None)
    monkeypatch.setitem(sys.modules, "picamera2", None)
    monkeypatch.setattr("src.hardware.uvc_generic.find_uvc_device_nodes", lambda exclude_ids=(): [])
    with pytest.raises(RuntimeError):
        drv.get_camera_driver(camera_type="auto", fallback_to_simulator=False)


def test_picamera_copies_only_the_kept_frame_read_only(fake_picamera2):
    """Stale-exposure frames are released uncopied; the kept one is mapped read-only (no cache write-back)."""
    maps = []

    class _MappedArray:
        def __init__(self, req, stream, reshape=True, write=True):
            maps.append(write)
            w, h = req.cam.config["main"]["size"]
            self.array = np.full((h, w, 3), 7, dtype=np.uint8)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    fake_picamera2.MappedArray = _MappedArray
    cam = PiCameraController()
    cam.open()
    stale = iter([500, 600])  # two frames still carrying an old exposure, then the requested one
    real_md = _FakeRequest.get_metadata
    _FakeRequest.get_metadata = lambda self: {**real_md(self), "ExposureTime": next(stale, real_md(self)["ExposureTime"])}
    try:
        frame, meta = cam.capture_frame()
    finally:
        _FakeRequest.get_metadata = real_md
    assert maps == [False]
    assert frame.shape == (cam.height, cam.width, 3) and int(frame[0, 0, 0]) == 7
    cam.close()


def test_picamera_learns_sensor_exposure_floor(fake_picamera2):
    """A steady exposure above the request is the sensor mode's floor and becomes the new minimum."""
    cam = PiCameraController()
    cam.open()
    real_md = _FakeRequest.get_metadata
    _FakeRequest.get_metadata = lambda self: {**real_md(self), "ExposureTime": 53}
    try:
        cam.set_hardware_exposure(0.00003, 0.0)
        cam.capture_frame()
    finally:
        _FakeRequest.get_metadata = real_md
    assert cam.min_exposure_s == pytest.approx(53e-6)
    cam.close()


def test_picamera_stale_frames_do_not_raise_exposure_floor(fake_picamera2):
    """Frames still carrying an older, longer exposure must not ratchet the minimum up (saturated days)."""
    cam = PiCameraController()
    cam.open()
    floor = cam.min_exposure_s
    real_md = _FakeRequest.get_metadata
    # ramping down / never applied in time / one stale frame inside the 150 us slack (the 587 us incident)
    for want, reported in ((0.0003, [587, 587, 520, 450, 400, 380]), (0.0003, [587] * 6), (0.00044, [587, 440])):
        stale = iter(reported)
        _FakeRequest.get_metadata = lambda self: {**real_md(self), "ExposureTime": next(stale)}
        try:
            cam.set_hardware_exposure(want, 0.0)
            _, meta = cam.capture_frame()
        finally:
            _FakeRequest.get_metadata = real_md
        assert cam.min_exposure_s == floor
        assert cam.current_exposure_s == pytest.approx(want)
    cam.close()


def test_picamera_darkens_below_sensor_floor_with_isp_brightness(fake_picamera2):
    """At the floor the mode minimum is requested (no IPA digital gain); below it ISP Brightness takes over."""
    cam = PiCameraController()
    cam.open()
    cam.picam.camera_controls["Brightness"] = (-1.0, 1.0, 0.0)
    cam._has_brightness = True
    real_md = _FakeRequest.get_metadata
    _FakeRequest.get_metadata = lambda self: {**real_md(self), "ExposureTime": 53}
    try:
        cam.set_hardware_exposure(0.00003, 0.0)
        cam.capture_frame()
        assert cam.sensor_floor_s == pytest.approx(53e-6)
        assert cam.min_exposure_s == pytest.approx(53e-6 / 4)
        assert cam.picam.controls["ExposureTime"] == 26  # the mode minimum, not 53
        assert cam.picam.controls["Brightness"] == pytest.approx(-0.1 * math.log2(53 / 30), abs=1e-3)

        cam.set_hardware_exposure(53e-6, 0.0)
        assert cam.picam.controls["ExposureTime"] == 26
        assert cam.picam.controls["Brightness"] == 0.0
        _, meta = cam.capture_frame()
        assert meta["actual_exposure_s"] == pytest.approx(53e-6)
        assert "isp_brightness" not in meta

        cam.set_hardware_exposure(26.5e-6, 0.0)
        assert cam.picam.controls["Brightness"] == pytest.approx(-0.1)
        _, meta = cam.capture_frame()
        assert meta["actual_exposure_s"] == pytest.approx(26.5e-6, abs=1e-6)  # equivalent exposure
        assert meta["sensor_exposure_s"] == pytest.approx(53e-6)
        assert meta["isp_brightness"] == pytest.approx(-0.1)

        cam.set_hardware_exposure(1e-6, 0.0)  # clamped to two stops below the floor
        assert cam.current_exposure_s == pytest.approx(53e-6 / 4)
        assert cam.picam.controls["Brightness"] == pytest.approx(-0.2)

        _FakeRequest.get_metadata = real_md
        cam.set_hardware_exposure(0.002, 0.0)  # above the floor: plain exposure, brightness back to neutral
        assert cam.picam.controls["ExposureTime"] == 2000
        assert cam.picam.controls["Brightness"] == 0.0
        _, meta = cam.capture_frame()
        assert meta["actual_exposure_s"] == pytest.approx(0.002)
        assert cam.sensor_floor_s == pytest.approx(53e-6)
    finally:
        _FakeRequest.get_metadata = real_md
    cam.close()
