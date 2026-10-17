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

    def capture_request(self):
        return _FakeRequest(self)


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
