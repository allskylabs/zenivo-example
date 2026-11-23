"""
Integration Tests for IMX335 AllSky Edge Web UI Dashboard.
Verifies:
- F17: Edge Web UI static asset delivery, MIME types, and responsive dark-mode DOM structure
- Live View canvas/image integration
- Telemetry HUD gauges, register displays, and histogram canvas
- Hardware control sliders, mode switches, and calibration trigger
- Gallery & meteor streak event table viewer
- End-to-end UI API integration workflow
"""

import os
import pytest
from fastapi.testclient import TestClient

from src.hardware.simulator import SimulatedIMX335Controller
from src.api.service import AllSkyService
from src.api.main import create_app

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
WEB_DIR = os.path.join(PROJECT_ROOT, "src", "web")


@pytest.fixture(scope="module")
def web_client():
    """Provides a TestClient configured with a fresh simulator camera."""
    cam = SimulatedIMX335Controller(width=640, height=480, seed=42)
    cam.open()
    AllSkyService.reset_instance()
    service = AllSkyService.get_instance(camera=cam)
    app = create_app(camera=cam)
    with TestClient(app) as client:
        yield client
    service.close()
    AllSkyService.reset_instance()


class TestWebUIStaticAssets:
    """Verifies that all frontend assets exist and are served with appropriate MIME types."""

    def test_web_directory_files_exist(self):
        assert os.path.isfile(os.path.join(WEB_DIR, "index.html"))
        assert os.path.isfile(os.path.join(WEB_DIR, "style.css"))
        assert os.path.isdir(os.path.join(WEB_DIR, "app"))

    def test_serve_root_index_html(self, web_client):
        res = web_client.get("/")
        assert res.status_code == 200
        assert "text/html" in res.headers["content-type"]
        html = res.text
        assert "Zenivo" in html
        assert ">AllSky<" not in html
        assert "<html" in html
        assert "</html>" in html

    def test_version_and_brand_files(self, web_client):
        from src.version import __version__
        res = web_client.get("/api/version")
        assert res.status_code == 200
        assert res.json() == {"product": "Zenivo", "version": __version__}
        ico = web_client.get("/favicon.ico")
        assert ico.status_code == 200 and ico.content[:4] == b"\x00\x00\x01\x00"
        assert web_client.get("/site.webmanifest").json()["short_name"] == "Zenivo"
        assert web_client.get("/static/brand/mark.svg").status_code == 200

    def test_asset_urls_get_content_stamp(self, tmp_path):
        from src.api.main import make_asset_stamper
        from src.version import __version__
        (tmp_path / "style.css").write_text("a{}")
        stamp = make_asset_stamper(str(tmp_path))
        html = '<link href="/style.css?v=hand-bumped"><script src="/static/missing.js?v=1"></script><a href="https://x/y.js?v=1">'
        out = stamp(html)
        assert f'/style.css?v={__version__}-' in out and "hand-bumped" not in out
        assert "/static/missing.js?v=1" in out and "https://x/y.js?v=1" in out  # unknown / external URLs untouched
        (tmp_path / "style.css").write_text("a{color:red}")
        assert stamp(html) != out  # a changed file changes the stamp

    def test_community_page_has_nav(self, web_client):
        res = web_client.get("/community")
        assert res.status_code == 200
        assert 'href="/community" data-paths="/community"' in res.text  # active state is set client-side (shell.js)
        assert web_client.get("/static/community.js").status_code == 200

    def test_overhead_page_is_public_and_has_nav(self, web_client):
        res = web_client.get("/overhead")
        assert res.status_code == 200
        assert 'href="/overhead" data-paths="/overhead"' in res.text
        assert web_client.get("/static/overlay_decode.js").status_code == 200

    def test_serve_index_html_direct(self, web_client):
        res = web_client.get("/index.html")
        assert res.status_code == 200
        assert "text/html" in res.headers["content-type"]

    def test_serve_style_css(self, web_client):
        res = web_client.get("/style.css")
        assert res.status_code == 200
        assert "text/css" in res.headers["content-type"]
        css = res.text
        # style.css is a list of @imports of css/*.css parts: follow them
        import re
        for part in re.findall(r'@import url\("(/static/css/[^"?]+)', css):
            sub = web_client.get(part)
            assert sub.status_code == 200 and "text/css" in sub.headers["content-type"]
            css += sub.text
        # Red vision palette lives in tokens.css; style.css holds the layout classes
        assert "viewport-wrapper" in css
        assert "histogram-card" in css

    def test_serve_app_scripts(self, web_client):
        import glob
        js = ""
        for path in sorted(glob.glob(os.path.join(WEB_DIR, "app", "*.js"))):
            res = web_client.get("/static/app/" + os.path.basename(path))
            assert res.status_code == 200
            assert "javascript" in res.headers["content-type"]
            js += res.text
        assert "drawHistogram" in js
        assert "/ws/live" in js
        assert "/api/camera/ae-lock" in js

    def test_static_mount_endpoint(self, web_client):
        res = web_client.get("/static/style.css")
        assert res.status_code == 200
        assert "text/css" in res.headers["content-type"]


@pytest.fixture(scope="module")
def html_content(web_client):
    res = web_client.get("/")
    assert res.status_code == 200
    return res.text


class TestWebUIDOMStructure:
    """Verifies that index.html contains all mandated astronomical controls and telemetry gauges."""

    def test_live_view_elements(self, html_content):
        assert 'id="live-stream-img"' in html_content
        assert 'id="reticle-canvas"' in html_content
        assert 'id="btn-layers"' in html_content
        assert 'id="obj-card"' in html_content
        assert 'id="btn-fullscreen"' in html_content
        assert 'id="chk-grid-overlay"' in html_content

    def test_telemetry_hud_gauges(self, html_content):
        # Mandatory telemetry gauges
        assert 'id="telem-exp"' in html_content
        assert 'id="telem-gain"' in html_content
        assert 'id="telem-stars"' in html_content
        assert 'id="telem-clouds"' in html_content
        assert 'id="telem-sky-status"' in html_content
        assert 'id="telem-fps"' in html_content
        assert 'id="telem-temp"' in html_content
        assert 'id="hud-hw-delta"' in html_content

        # Sensor registers
        assert 'id="reg-vmax"' in html_content
        assert 'id="reg-hmax"' in html_content
        assert 'id="reg-shr0"' in html_content
        assert 'id="reg-pgc"' in html_content

    def test_histogram_canvas_and_stats(self, html_content):
        assert 'id="histogram-canvas"' in html_content
        assert 'id="hist-mean"' in html_content
        assert 'id="hist-stddev"' in html_content
        assert 'id="hist-median"' in html_content
        assert 'id="hist-clip-low"' in html_content
        assert 'id="hist-clip-high"' in html_content

    def test_hardware_controls_and_mode_toggle(self, html_content):
        assert 'id="range-exposure"' in html_content
        assert 'id="val-exposure"' in html_content
        assert 'id="range-gain"' in html_content
        assert 'id="val-gain"' in html_content
        assert 'id="btn-night-vision"' in html_content

    def test_calibration_and_gallery_tabs(self, html_content):
        assert 'id="btn-trigger-dark"' in html_content
        assert 'id="dark-frames-count"' in html_content
        assert 'id="dark-exposure"' in html_content
        assert 'id="dark-gain"' in html_content
        assert 'id="products-grid"' in html_content
        assert 'id="live-events-list"' in html_content


class TestWebUIWorkflowIntegration:
    """Simulates realistic browser interaction flows via Edge API."""

    def test_browser_dashboard_startup_sequence(self, web_client):
        # 1. Browser loads UI
        ui_res = web_client.get("/")
        assert ui_res.status_code == 200

        # 2. Browser fetches initial status
        status_res = web_client.get("/api/status")
        assert status_res.status_code == 200
        status = status_res.json()
        assert status["camera_connected"] is True

        # 3. Browser fetches telemetry & histogram
        telem_res = web_client.get("/api/telemetry")
        assert telem_res.status_code == 200
        telem = telem_res.json()
        assert "histogram" in telem
        assert len(telem["histogram"]["y"]) == 256

        # 4. User modifies exposure slider to 5.0s and gain to 36.0 dB
        apply_res = web_client.post("/api/camera/exposure", json={"exposure_s": 5.0, "gain_db": 36.0})
        assert apply_res.status_code == 200
        assert apply_res.json()["success"] is True

        # 5. User switches to Gallery tab and checks products
        gallery_res = web_client.get("/api/gallery")
        assert gallery_res.status_code == 200
        assert len(gallery_res.json()["items"]) >= 1

        # 6. User checks meteor events
        events_res = web_client.get("/api/events/meteors")
        assert events_res.status_code == 200
        assert "events" in events_res.json()

        # 7. User triggers Master Dark stack
        dark_res = web_client.post(
            "/api/calibration/master-dark",
            json={"num_frames": 3, "exposure_s": 1.0, "gain_db": 24.0},
        )
        assert dark_res.status_code == 200
        assert dark_res.json()["frames_stacked"] == 3
