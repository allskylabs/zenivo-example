"""Motorised lens: score sweep autofocus, saved position, and night focus tracking."""

import time

import cv2
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
        self.true_focus = TRUE_FOCUS
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
        sigma = 0.9 + 9.0 * abs(self.lens_position - self.true_focus) + self.drift_per_frame * self.frames
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
    steep = TempCompensation([{"temp_c": 0, "pos": 0.12}, {"temp_c": 5, "pos": 0.25}, {"temp_c": 10, "pos": 0.38}])
    assert steep.fit()["slope"] == TempCompensation.MAX_SLOPE


def test_auto_mode_follows_temperature_manual_holds(lens_client):
    client, svc, cam = lens_client
    svc.last_metadata["sensor_temp_c"] = 20.0
    client.post("/api/focus/lens", json={"position": 0.30})
    client.post("/api/focus/lens", json={"mode": "auto"})
    svc._lens_cfg()["next_auto_sweep"] = time.time() + 86400  # this test is about compensation only
    svc.lens_comp.add(20.0, 0.30, "sweep", 1)
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
    # The next session places its zoom by itself; Use brings back the saved lens, zoom and measure
    st = client.post("/api/focus/start", json={}).json()
    assert st["active"] and "found" in st
    client.post("/api/focus/lens", json={"position": 0.9})
    st = client.post("/api/focus/apply-record?index=-1").json()
    assert cam.lens_position_target == 0.41 and st["active"]
    assert st["cx"] == 0.3 and st["roi_size"] == 192 and st["method"] == "stars"


def test_near_object_af_is_rejected_and_lens_put_back(lens_client):
    client, svc, cam = lens_client
    client.post("/api/focus/lens", json={"position": 0.2})  # verified sky focus
    client.post("/api/focus/start", json={})
    svc.focus.sweep = {"state": "running", "mode": "camera", "points": [], "started_at": time.time()}
    svc._lens_finish(4.02, "done", "focused at 4.02 D", "camera_af")
    assert svc.focus.sweep["state"] == "rejected"
    assert cam.lens_position_target == 0.2 and svc.focus.active  # not saved, focus mode stays on
    assert svc.station_config["lens_focus"]["position"] == 0.2


def test_temperature_fit_drops_near_and_outlier_samples():
    from src.analytics.focus_tracker import TempCompensation
    comp = TempCompensation([
        {"temp_c": 47, "pos": 0.2}, {"temp_c": 46, "pos": 0.16}, {"temp_c": 70, "pos": 0.22},
        {"temp_c": 73, "pos": 4.023},  # near object: never a sample
    ])
    assert len(comp.samples) == 3
    comp.add(71.0, 2.8, "camera_af", 1)
    assert len(comp.samples) == 3
    comp.add(60.0, 0.6, "manual", 2)  # far from the others: outlier
    fit = comp.fit()
    assert fit["n"] == 3 and fit["n_rejected"] == 1 and abs(fit["slope"]) < 0.005


def test_manual_move_cancels_running_autofocus(lens_client):
    client, svc, cam = lens_client
    client.post("/api/focus/start", json={"method": "stars", "roi_size": 384})
    client.post("/api/focus/lens/autofocus", json={"mode": "sweep"})
    time.sleep(0.5)
    r = client.post("/api/focus/lens", json={"position": 0.9})
    assert r.status_code == 200
    assert svc.focus.sweep["state"] == "cancelled"
    assert cam.lens_position_target == 0.9


def test_flat_sweep_on_blank_sky_is_not_saved(lens_client):
    client, svc, cam = lens_client
    client.post("/api/focus/lens", json={"position": 0.25})
    cam.capture_frame = lambda: (np.full((480, 640, 3), 180, np.uint8), {"lens_position": cam.lens_position,
                                                                       "actual_exposure_s": 0.01, "gain_db": 0.0})
    client.post("/api/focus/start", json={"method": "contrast", "roi_size": 256})
    n_samples = len(svc.lens_comp.samples)
    rec_before = svc.station_config.get("focus_record")
    client.post("/api/focus/lens/autofocus", json={"mode": "sweep"})
    deadline = time.time() + 60
    while svc.focus.sweep["state"] == "running" and time.time() < deadline:
        time.sleep(0.2)
    assert svc.focus.sweep["state"] == "failed" and "flat" in svc.focus.sweep["message"]
    assert cam.lens_position_target == 0.25 and svc.station_config["lens_focus"]["position"] == 0.25
    assert len(svc.lens_comp.samples) == n_samples and svc.station_config.get("focus_record") == rec_before


def test_find_focus_point_day_detail_night_star_and_blank_sky():
    from src.analytics.focus import find_focus_point
    rng = np.random.default_rng(1)
    day = np.full((480, 640, 3), 170, np.uint8)  # bright sky
    patch = rng.integers(30, 220, (60, 80), dtype=np.uint8)
    day[300:360, 420:500] = np.dstack([patch] * 3)  # a detailed rooftop
    pt = find_focus_point(day)
    assert pt["method"] == "contrast" and abs(pt["cx"] - 0.72) < 0.08 and abs(pt["cy"] - 0.69) < 0.1
    night = _star_field(1.2)
    assert find_focus_point(night)["method"] == "stars"
    assert find_focus_point(np.full((480, 640, 3), 160, np.uint8)) is None


def test_day_tracker_needs_a_clear_win():
    tr = FocusTracker(delta=0.04, interval_s=0.0)
    tr.reset(0.3)
    vals = iter([10.0, 9.9, 10.1])  # within 4 %: no move
    metric = lambda f: next(vals)
    frame = np.zeros((10, 10, 3), np.uint8)
    pos = 0.3
    for t in range(3):
        new = tr.step(t + 1.0, frame, pos, pos, (0, 12), is_night=False, day_metric=metric)
        pos = new if new is not None else pos
    assert pos == 0.3 and tr.log[-1]["kind"] == "day"
    vals = iter([10.0, 9.0, 11.0])  # +0.04 D clearly sharper (lower score)
    for t in range(3):
        new = tr.step(t + 10.0, frame, pos, pos, (0, 12), is_night=False, day_metric=metric)
        pos = new if new is not None else pos
    assert pos > 0.3


def test_near_records_are_not_last_or_reusable(lens_client):
    client, svc, cam = lens_client
    svc.station_config["focus_history"] = [
        {"finished_at": "2026-10-17T20:00:00+07:00", "source": "sweep", "lens_position": 0.2, "roi": {"cx": 0.5, "cy": 0.5, "size": 256}},
        {"finished_at": "2026-10-18T12:20:00+07:00", "source": "camera_af", "lens_position": 4.018, "roi": {"cx": 0.09, "cy": 0.87, "size": 256}},
    ]
    st = client.get("/api/focus").json()
    assert st["last_record"]["lens_position"] == 0.2
    assert client.post("/api/focus/apply-record?index=-1").status_code == 409
    started = client.post("/api/focus/start", json={}).json()
    assert started["active"] and "found" in started  # zoom placed by auto point, not the corner record
    assert (started["cx"], started["cy"]) != (0.09, 0.87)


def test_auto_mode_runs_sweep_by_itself(lens_client, monkeypatch):
    import src.calibration.auto_exposure as ae
    client, svc, cam = lens_client
    monkeypatch.setattr(ae, "calculate_solar_elevation", lambda *a, **k: -30.0)  # dark night
    client.post("/api/focus/lens", json={"mode": "manual"})
    client.post("/api/focus/lens", json={"position": 1.5})  # near: not a valid sky focus
    client.post("/api/focus/lens", json={"mode": "auto"})
    frame = _star_field(1.0)
    svc._track_focus(frame, {"lens_position": cam.lens_position}, time.time())
    deadline = time.time() + 90
    while time.time() < deadline and (svc.focus.sweep is None or svc.focus.sweep.get("state") == "running"):
        time.sleep(0.2)
    sw = svc.focus.sweep
    assert sw and sw["auto"] and sw["state"] == "done", sw
    assert abs(cam.lens_position_target - TRUE_FOCUS) <= 0.03
    assert not svc.focus.active
    assert svc.station_config["focus_record"]["source"] == "auto_sweep"
    assert svc.station_config["lens_focus"]["next_auto_sweep"] > time.time() + 3600
    # Done for now: the next capture does not start another sweep
    svc._track_focus(frame, {"lens_position": cam.lens_position}, time.time())
    assert not svc.focus.active


def test_auto_sweep_skips_twilight_and_blank_sky(lens_client, monkeypatch):
    import src.calibration.auto_exposure as ae
    client, svc, cam = lens_client
    client.post("/api/focus/lens", json={"mode": "auto"})
    monkeypatch.setattr(ae, "calculate_solar_elevation", lambda *a, **k: -5.0)
    assert svc._maybe_auto_sweep(_star_field(1.0), time.time(), svc._lens_cfg()) is False
    monkeypatch.setattr(ae, "calculate_solar_elevation", lambda *a, **k: 40.0)
    svc._auto_sweep_retry_ts = 0.0
    assert svc._maybe_auto_sweep(np.full((480, 640, 3), 170, np.uint8), time.time(), svc._lens_cfg()) is False


def test_start_with_autofocus_sweeps_saves_and_ends(lens_client):
    client, svc, cam = lens_client
    client.post("/api/focus/lens", json={"mode": "manual"})
    client.post("/api/focus/lens", json={"position": 1.4})
    st = client.post("/api/focus/start", json={"autofocus": True}).json()
    assert st["active"] and st["lens"]["sweep"]["state"] == "running"
    deadline = time.time() + 90
    while svc.focus.sweep.get("state") == "running" and time.time() < deadline:
        time.sleep(0.2)
    assert svc.focus.sweep["state"] == "done", svc.focus.sweep
    assert abs(cam.lens_position_target - TRUE_FOCUS) <= 0.03 and not svc.focus.active


def test_af_mf_modes(lens_client):
    client, svc, cam = lens_client
    client.post("/api/focus/lens", json={"mode": "manual"})
    st = client.post("/api/focus/start", json={}).json()  # MF: streams only
    assert st["active"] and not (svc.focus.sweep and svc.focus.sweep.get("state") == "running")
    st = client.post("/api/focus/lens", json={"mode": "auto"}).json()  # AF while focusing: sweep now
    assert st["lens"]["mode"] == "auto" and svc.focus.sweep["state"] == "running"
    client.post("/api/focus/lens", json={"position": 0.8})  # touching the slider = MF, AF run cancelled
    assert svc._lens_cfg()["mode"] == "manual" and cam.lens_position_target == 0.8
    assert svc.focus.sweep["state"] in ("cancelled", "done", "failed")


def test_sweep_is_one_pass_with_parabola_and_rejects_near_object(lens_client):
    client, svc, cam = lens_client
    client.post("/api/focus/lens", json={"mode": "manual"})
    client.post("/api/focus/lens", json={"position": 0.2})
    client.post("/api/focus/start", json={"method": "stars", "roi_size": 384})
    client.post("/api/focus/lens/autofocus", json={"mode": "sweep"})
    deadline = time.time() + 90
    while svc.focus.sweep["state"] == "running" and time.time() < deadline:
        time.sleep(0.2)
    sw = svc.focus.sweep
    out = [p["pos"] for p in sw["points"] if p["phase"] == "sweep"]
    back = [p["pos"] for p in sw["points"] if p["phase"] == "refine"]
    assert out == sorted(out) and back == sorted(back, reverse=True), "one way out, one way back"
    assert back and max(back) - min(back) <= 0.17 and abs(cam.lens_position_target - TRUE_FOCUS) <= 0.02
    assert sw["state"] == "done" and sw["fit"] and abs(sw["fit"]["vertex"] - TRUE_FOCUS) <= 0.03
    # Zoom on something 0.6 m away: sharpening all the way to the end of the sky range
    cam.true_focus = 1.6
    client.post("/api/focus/lens", json={"position": 0.2})
    client.post("/api/focus/start", json={"method": "stars", "roi_size": 384})
    client.post("/api/focus/lens/autofocus", json={"mode": "sweep"})
    deadline = time.time() + 90
    while svc.focus.sweep["state"] == "running" and time.time() < deadline:
        time.sleep(0.2)
    assert svc.focus.sweep["state"] == "rejected" and "nearby object" in svc.focus.sweep["message"]
    assert cam.lens_position_target == 0.2


class SceneLensCamera(LensSimCamera):
    """Day scene: a distant rooftop (sharpest at 0.30 D) on the left, a nearby wall (sharpest at 2.4 D) right."""

    def capture_frame(self):
        _, meta = SimulatedIMX335Controller.capture_frame(self)
        h, w = self.height, self.width
        rng = np.random.default_rng(5)
        img = np.full((h, w), 175, np.uint8)  # bright sky
        far = rng.integers(40, 220, (60, 60), dtype=np.uint8)
        near = rng.integers(30, 200, (60, 60), dtype=np.uint8)

        def blurred(patch, focus):
            sig = 0.4 + 6.0 * abs(self.lens_position - focus)
            return cv2.GaussianBlur(patch, (0, 0), sig)

        img[200:260, 120:180] = blurred(far, 0.30)
        img[200:260, 440:500] = blurred(near, 2.4)
        meta["lens_position"] = self.lens_position
        return np.dstack([img] * 3), meta


def test_day_sweep_uses_distant_detail_not_the_nearby_wall():
    AllSkyService.reset_instance()
    cam = SceneLensCamera(width=640, height=480, seed=42)
    cam.open()
    svc = AllSkyService.get_instance(camera=cam)
    svc.station_config["image_transform"] = {}  # the scene's left/right must stay where they are drawn
    with TestClient(create_app(camera=cam)) as client:
        client.post("/api/focus/lens", json={"mode": "manual"})
        client.post("/api/focus/lens", json={"position": 0.6})
        client.post("/api/focus/start", json={"cx": 470 / 640, "cy": 230 / 480, "roi_size": 64, "method": "contrast"})
        client.post("/api/focus/lens/autofocus", json={"mode": "sweep"})
        deadline = time.time() + 120
        while svc.focus.sweep["state"] == "running" and time.time() < deadline:
            time.sleep(0.2)
        sw = svc.focus.sweep
        assert sw["state"] == "done", sw.get("message")
        assert abs(cam.lens_position_target - 0.30) <= 0.04, sw.get("message")
        assert svc.focus.cx < 0.5  # the zoom moved to the distant rooftop that decided it
        assert max(p["pos"] for p in sw["points"]) < 2.0  # no need to walk the whole lens once the sky peak is known
    AllSkyService.reset_instance()


def test_day_sweep_with_only_a_nearby_wall_runs_on_until_its_peak():
    class WallOnly(SceneLensCamera):
        def capture_frame(self):
            f, meta = SceneLensCamera.capture_frame(self)
            f[150:310, 60:240] = 175  # no distant rooftop: sky and the wall only
            return f, meta

    AllSkyService.reset_instance()
    cam = WallOnly(width=640, height=480, seed=42)
    cam.open()
    svc = AllSkyService.get_instance(camera=cam)
    svc.station_config["image_transform"] = {}
    with TestClient(create_app(camera=cam)) as client:
        client.post("/api/focus/lens", json={"mode": "manual"})
        client.post("/api/focus/lens", json={"position": 0.3})
        client.post("/api/focus/start", json={"cx": 470 / 640, "cy": 230 / 480, "roi_size": 64, "method": "contrast"})
        client.post("/api/focus/lens/autofocus", json={"mode": "sweep"})
        deadline = time.time() + 180
        while svc.focus.sweep["state"] == "running" and time.time() < deadline:
            time.sleep(0.2)
        sw = svc.focus.sweep
        assert sw["state"] == "rejected" and abs(sw["best_position"] - 2.4) <= 0.15, sw.get("message")
        assert cam.lens_position_target == 0.3  # near focus is reported, not kept
    AllSkyService.reset_instance()


def test_sweep_stops_soon_after_the_peak_even_with_flat_regions():
    class WallAndCloud(SceneLensCamera):
        """Only a nearby wall peaks (2.4 D); a soft drifting 'cloud' tile never peaks."""
        def capture_frame(self):
            f, meta = SceneLensCamera.capture_frame(self)
            f[150:310, 60:240] = 175
            rng = np.random.default_rng(self.frames)
            self.frames += 1
            cloud = cv2.GaussianBlur(rng.integers(120, 220, (60, 60), dtype=np.uint8), (0, 0), 3)
            f[60:120, 300:360] = np.dstack([cloud] * 3)
            return f, meta

    AllSkyService.reset_instance()
    cam = WallAndCloud(width=640, height=480, seed=42)
    cam.open()
    svc = AllSkyService.get_instance(camera=cam)
    svc.station_config["image_transform"] = {}
    with TestClient(create_app(camera=cam)) as client:
        client.post("/api/focus/lens", json={"mode": "manual"})
        client.post("/api/focus/lens", json={"position": 0.3})
        client.post("/api/focus/start", json={"cx": 470 / 640, "cy": 230 / 480, "roi_size": 64, "method": "contrast"})
        client.post("/api/focus/lens/autofocus", json={"mode": "sweep"})
        deadline = time.time() + 180
        while svc.focus.sweep["state"] == "running" and time.time() < deadline:
            time.sleep(0.2)
        last = max(p["pos"] for p in svc.focus.sweep["points"])
        assert last <= 4.0, last  # wall peak 2.4 D: stop well before the 12 D end of the lens
    AllSkyService.reset_instance()
