"""Motorised lens: score sweep autofocus, saved position, and night focus tracking."""

import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from src.analytics.focus_tracker import FocusTracker
from src.api.main import create_app
from src.api.service import AllSkyService
from src.hardware.simulator import SimulatedIMX335Controller

TRUE_FOCUS = 0.37  # dioptres where this fake lens is sharpest (deliberately not its nominal infinity, 0)


def _star_field(sigma: float, w: int = 640, h: int = 480, seed: int = 3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    img = np.full((h, w), 15, np.float32) + rng.normal(0, 1.5, (h, w))
    yy, xx = np.mgrid[0:h, 0:w]
    pts = np.random.default_rng(7).integers(20, [w - 20, h - 20], size=(60, 2))
    for x, y in pts:
        sl = (slice(max(0, y - 25), y + 25), slice(max(0, x - 25), x + 25))
        img[sl] += 140 * np.exp(-((xx[sl] - x) ** 2 + (yy[sl] - y) ** 2) / (2 * sigma ** 2))
    g = np.clip(img, 0, 255).astype(np.uint8)
    return np.dstack([g, g, g])


class LensSimCamera(SimulatedIMX335Controller):
    """Simulator with a VCM lens: blur grows with distance from TRUE_FOCUS; reports LensPosition."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.lens_range = (0.0, 12.0, 1.0)
        self.lens_position_target = None
        self.lens_position = 1.0
        self.moves = []
        self.drift_per_frame = 0.0  # seeing / dusk: the whole scene softens steadily while sweeping
        self.frames = 0

    def set_lens_position(self, d):
        pos = round(min(12.0, max(0.0, float(d))), 4)
        self.lens_position_target = self.lens_position = pos
        self.moves.append(pos)
        return pos

    def lens_info(self):
        return {"available": True, "min": 0.0, "max": 12.0, "default": 1.0, "position": self.lens_position,
                "held": self.lens_position_target, "af_state": None, "camera_af": False}

    def af_status(self):
        return {"af_state": None, "lens_position": self.lens_position, "scanning": False}

    def capture_frame(self):
        _, meta = super().capture_frame()
        self.frames += 1
        sigma = 0.9 + 9.0 * abs(self.lens_position - TRUE_FOCUS) + self.drift_per_frame * self.frames
        meta["lens_position"] = self.lens_position
        return _star_field(sigma, self.width, self.height), meta


@pytest.fixture
def lens_client():
    AllSkyService.reset_instance()
    cam = LensSimCamera(width=640, height=480, seed=42)
    cam.open()
    svc = AllSkyService.get_instance(camera=cam)
    app = create_app(camera=cam)
    with TestClient(app) as c:
        yield c, svc, cam
    AllSkyService.reset_instance()


@pytest.mark.parametrize("drift", [0.0, 0.006])
def test_sweep_finds_real_focus_not_nominal_infinity(lens_client, drift):
    client, svc, cam = lens_client
    cam.drift_per_frame = drift
    assert client.get("/api/focus").json()["lens"]["available"]
    client.post("/api/focus/start", json={"roi_size": 384, "method": "stars"})
    r = client.post("/api/focus/lens/autofocus", json={"mode": "sweep"})
    assert r.status_code == 200
    deadline = time.time() + 90
    while svc.focus.sweep["state"] == "running" and time.time() < deadline:
        time.sleep(0.2)
    sw = svc.focus.sweep
    assert sw["state"] == "done", sw
    assert abs(sw["best_position"] - TRUE_FOCUS) <= 0.03, sw["best_position"]
    assert abs(cam.lens_position_target - TRUE_FOCUS) <= 0.03
    saved = svc.station_config["lens_focus"]
    assert saved["source"] == "sweep" and saved["base_position"] == saved["position"]
    # Early stop: it never needs to walk the whole 0..2.5 D range
    assert max(p["pos"] for p in sw["points"]) < 1.2
    # A successful sweep records the result in settings and leaves focus mode
    rec = svc.station_config["focus_record"]
    assert not svc.focus.active and rec["source"] == "sweep" and abs(rec["lens_position"] - TRUE_FOCUS) <= 0.03
    assert rec["method"] == "stars" and rec["score"] is not None


def test_manual_lens_moves_and_requires_lens(lens_client):
    client, svc, cam = lens_client
    client.post("/api/focus/lens", json={"position": 0.5})
    assert cam.lens_position_target == 0.5
    client.post("/api/focus/lens", json={"delta": -0.02})
    assert abs(cam.lens_position_target - 0.48) < 1e-6
    assert client.post("/api/focus/lens", json={"tracking": False}).status_code == 200
    assert svc.focus_tracker.enabled is False
    assert client.post("/api/focus/lens/autofocus", json={"mode": "sweep"}).status_code == 409  # focus mode off


def test_tracker_follows_drift_toward_sharper_stars():
    tr = FocusTracker(delta=0.04, interval_s=0.0, max_offset=0.3)
    tr.reset(0.30)
    pos = 0.30
    best = 0.42  # focus drifted (temperature): the sharp position moved +0.12 D
    rng = (0.0, 12.0)
    now = 1.0
    for _ in range(60):
        frame = _star_field(0.9 + 9.0 * abs(pos - best))
        new = tr.step(now, frame, pos, pos, rng, is_night=True, temp_c=10.0)
        if new is not None:
            pos = new
        now += 1.0
    assert abs(pos - best) < 0.03, pos
    assert tr.log and tr.log[-1]["temp_c"] == 10.0


def test_tracker_restores_lens_at_dawn_and_skips_cloud():
    tr = FocusTracker(interval_s=0.0)
    tr.reset(0.3)
    first = tr.step(1.0, _star_field(1.0), 0.3, 0.3, (0, 12), is_night=True)
    assert first == pytest.approx(0.34)
    assert tr.step(2.0, _star_field(1.0), 0.34, 0.34, (0, 12), is_night=False) == 0.3
    cloud = np.full((480, 640, 3), 40, np.uint8)
    assert tr.step(3.0, cloud, 0.3, 0.3, (0, 12), is_night=True) is None and tr.next_ts > 3.0


def test_temperature_model_learns_slope_and_clamps():
    from src.analytics.focus_tracker import TempCompensation
    comp = TempCompensation()
    comp.add(20.0, 0.30, "sweep", 1)
    comp.add(21.0, 0.305, "tracking", 2)
    assert comp.fit()["learned"] is False  # too few samples / too little span
    comp.add(26.0, 0.33, "tracking", 3)
    fit = comp.fit()
    assert fit["learned"] and abs(fit["slope"] - 0.005) < 0.001
    assert abs(comp.target(0.33, 26.0, 30.0) - 0.35) < 0.005
    steep = TempCompensation([{"temp_c": 0, "pos": 0}, {"temp_c": 5, "pos": 1}, {"temp_c": 10, "pos": 2}])
    assert steep.fit()["slope"] == TempCompensation.MAX_SLOPE


def test_auto_mode_follows_temperature_manual_holds(lens_client):
    client, svc, cam = lens_client
    client.post("/api/focus/lens", json={"mode": "auto"})
    svc.last_metadata["sensor_temp_c"] = 20.0
    client.post("/api/focus/lens", json={"position": 0.30})  # manual set in auto mode = verified sample
    svc.lens_comp.add(30.0, 0.35, "tracking", 2)
    svc.lens_comp.add(25.0, 0.325, "tracking", 3)
    frame = _star_field(1.0)
    svc._track_focus(frame, {"sensor_temp_c": 30.0, "lens_position": cam.lens_position}, time.time())
    assert abs(cam.lens_position_target - 0.35) < 0.006
    state = client.get("/api/focus").json()["lens"]
    assert state["mode"] == "auto" and state["compensation"]["learned"]
    client.post("/api/focus/lens", json={"mode": "manual"})
    held = cam.lens_position_target
    svc._track_focus(frame, {"sensor_temp_c": 10.0, "lens_position": held}, time.time())
    assert cam.lens_position_target == held
    client.post("/api/focus/lens", json={"reset_learning": True})
    assert svc.lens_comp.samples == []


def test_finish_saves_and_record_can_be_reused(lens_client):
    client, svc, cam = lens_client
    assert client.post("/api/focus/finish").status_code == 409
    client.post("/api/focus/start", json={"cx": 0.3, "cy": 0.6, "roi_size": 192, "method": "stars"})
    client.post("/api/focus/lens", json={"position": 0.41})
    client.get("/api/focus/frame")
    out = client.post("/api/focus/finish").json()
    assert not out["active"] and out["record"]["lens_position"] == 0.41 and out["record"]["roi"]["size"] == 192
    assert client.get("/api/focus").json()["last_record"]["source"] == "manual"
    # Next session starts from the saved zoom region and measure
    st = client.post("/api/focus/start", json={}).json()
    assert st["cx"] == 0.3 and st["roi_size"] == 192 and st["method"] == "stars"
    client.post("/api/focus/lens", json={"position": 1.5})
    st = client.post("/api/focus/apply-record?index=-1").json()
    assert cam.lens_position_target == 0.41 and st["active"]
