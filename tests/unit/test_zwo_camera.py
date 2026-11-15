"""ZWO ASI driver, against a fake `zwoasi` module (no SDK or hardware needed)."""

import sys
import types

import numpy as np
import pytest

from src.hardware import driver as drv
from src.hardware import zwo
from src.hardware.zwo import ZwoAsiController


class _FakeCamera:
    def __init__(self, idx, color=True):
        self.color = color
        self.ctrl = {}
        self.roi = None
        self.exposures = 0

    def get_camera_property(self):
        return {"Name": "ZWO ASI533MC Pro", "IsColorCam": self.color, "BayerPattern": 0, "PixelSize": 3.76,
                "MaxWidth": 3008, "MaxHeight": 3008, "SupportedBins": [1, 2, 3, 4]}

    def get_controls(self):
        return {"Exposure": {"MinValue": 32, "MaxValue": 2_000_000_000}, "Gain": {"MinValue": 0, "MaxValue": 500},
                "BandWidth": {"MaxValue": 100}}

    def set_roi(self, start_x, start_y, width, height, bins, image_type):
        self.roi = (width, height, bins)

    def set_control_value(self, name, value):
        self.ctrl[name] = value

    def get_control_value(self, name):
        return (253, False)

    def start_exposure(self):
        self.exposures += 1

    def get_exposure_status(self):
        return 2

    def get_data_after_exposure(self):
        w, h, _ = self.roi
        return np.full((h, w), 40 * 256, dtype=np.uint16).tobytes()

    def stop_exposure(self):
        pass

    def close(self):
        pass


@pytest.fixture
def fake_zwoasi(monkeypatch):
    mod = types.ModuleType("zwoasi")
    mod.ASI_IMG_RAW16, mod.ASI_EXPOSURE, mod.ASI_GAIN = 2, "exp", "gain"
    mod.ASI_BANDWIDTHOVERLOAD, mod.ASI_TEMPERATURE = "bw", "temp"
    mod.ASI_EXP_SUCCESS, mod.ASI_EXP_FAILED = 2, 3
    mod.init = lambda path: None
    mod.get_num_cameras = lambda: 1
    mod.list_cameras = lambda: ["ZWO ASI533MC Pro"]
    mod.Camera = _FakeCamera
    monkeypatch.setitem(sys.modules, "zwoasi", mod)
    monkeypatch.setattr(zwo, "_asi_ready", False)
    return mod


def test_open_reports_limits_and_info(fake_zwoasi):
    cam = ZwoAsiController()
    assert cam.open()
    assert (cam.width, cam.height) == (3008, 3008)
    lim = cam.get_limits()
    assert lim["max_gain_db"] == 50.0 and lim["max_exposure_s"] == 2000.0
    info = drv.describe_camera(cam)
    assert info["backend"] == "zwo" and info["pixel_pitch_um"] == 3.76


def test_exposure_and_gain_are_clamped_and_written_in_sdk_units(fake_zwoasi):
    cam = ZwoAsiController()
    cam.open()
    out = cam.set_hardware_exposure(5.0, 99.0)
    assert out["gain_db"] == 50.0 and cam.cam.ctrl["gain"] == 500 and cam.cam.ctrl["exp"] == 5_000_000


def test_colour_frame_is_demosaiced_to_8bit_bgr(fake_zwoasi):
    cam = ZwoAsiController()
    cam.open()
    frame, meta = cam.capture_frame()
    assert frame.shape == (3008, 3008, 3) and frame.dtype == np.uint8 and int(frame.mean()) == 40
    assert meta["camera_backend"] == "zwo" and meta["sensor_temp_c"] == 25.3


def test_requested_size_picks_binning(fake_zwoasi):
    cam = ZwoAsiController(width=1500, height=1500)
    cam.open()
    assert cam.bin == 2 and (cam.width, cam.height) == (1504, 1504)


def test_factory_zwo_and_fallback(fake_zwoasi, monkeypatch):
    assert drv.get_camera_driver(camera_type="zwo").backend == "zwo"
    monkeypatch.setitem(sys.modules, "zwoasi", None)  # wrapper missing
    monkeypatch.setattr(zwo, "_asi_ready", False)
    assert "Simulated" in drv.get_camera_driver(camera_type="zwo").__class__.__name__
    with pytest.raises(RuntimeError):
        drv.get_camera_driver(camera_type="zwo", fallback_to_simulator=False)


# ---------------------------------------------------------------- camera picker (API)
def test_saved_camera_selection_reads_station_config(tmp_path):
    import json
    import run_allsky

    p = tmp_path / "station_config.json"
    p.write_text(json.dumps({"camera_select": {"type": "zwo", "index": 1}}))
    assert run_allsky.saved_camera_selection(str(p)) == ("zwo", 1)
    p.write_text(json.dumps({"camera_select": {"type": "bogus", "index": 0}}))
    assert run_allsky.saved_camera_selection(str(p)) is None
    assert run_allsky.saved_camera_selection(str(tmp_path / "missing.json")) is None


@pytest.fixture
def picker_client(fake_zwoasi, tmp_path):
    from fastapi.testclient import TestClient
    from src.api.main import create_app
    from src.api.service import AllSkyService
    from src.hardware.simulator import SimulatedIMX335Controller

    sim = SimulatedIMX335Controller(width=640, height=480, seed=1)
    sim.open()
    AllSkyService.reset_instance()
    service = AllSkyService.get_instance(camera=sim)
    service.station_config_path = str(tmp_path / "station_config.json")
    with TestClient(create_app(camera=sim)) as client:
        yield client, service
    service.close()
    AllSkyService.reset_instance()


def test_picker_lists_zwo_and_switches(picker_client, monkeypatch):
    client, service = picker_client
    monkeypatch.setattr(drv, "find_rts5869_device_node", lambda: None)
    data = client.get("/api/camera/devices").json()
    assert data["current"]["type"] == "simulator"
    assert any(d["id"] == "zwo:0" and "ASI533" in d["name"] for d in data["devices"])

    out = client.post("/api/camera/select", json={"type": "zwo", "index": 0}).json()
    assert out["current"]["type"] == "zwo" and out["current"]["width"] == 3008
    assert service.station_config["camera_select"] == {"type": "zwo", "index": 0}


def test_picker_failure_keeps_old_camera(picker_client):
    client, service = picker_client
    r = client.post("/api/camera/select", json={"type": "zwo", "index": 3})  # only one camera exists
    assert r.status_code == 400
    assert client.get("/api/camera/devices").json()["current"]["type"] == "simulator"
    assert "camera_select" not in service.station_config
