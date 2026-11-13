"""
FastAPI Application Entry Point for Zenivo (all-sky observatory software) Edge API.
Initializes middleware, API routes, static file serving for Edge Web UI, and lifecycle hooks.
"""

import hashlib
import os
import re
from typing import Dict, Optional, Tuple
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse

from src.hardware.driver_interface import CameraDriverInterface
from src.api.service import AllSkyService
from src.api.routes import router
from src.version import PRODUCT, __version__

# Local scripts/styles in the pages carry a hand-written ?v=...: replaced at serve time by <version>-<hash of mtime+size>
_ASSET_URL = re.compile(r'((?:src|href)=")(/static/[^"?#]+|/style\.css)\?v=[^"]*(")')


def make_asset_stamper(web_dir: str):
    """Return stamp(html): rewrite ?v=<anything> on local /static/... and /style.css URLs so a changed file busts the cache.
    The per-file stamp is recomputed only when its (mtime, size) changes (one stat per asset per page view)."""
    cache: Dict[str, Tuple[Tuple[int, int], str]] = {}

    def stamp_of(url_path: str) -> Optional[str]:
        rel = "style.css" if url_path == "/style.css" else url_path[len("/static/"):]
        full = os.path.normpath(os.path.join(web_dir, rel))
        if not full.startswith(web_dir + os.sep):
            return None
        try:
            st = os.stat(full)
        except OSError:
            return None
        key = (st.st_mtime_ns, st.st_size)
        hit = cache.get(url_path)
        if hit is None or hit[0] != key:
            digest = hashlib.sha1(f"{key[0]}:{key[1]}".encode()).hexdigest()[:8]
            hit = (key, f"{__version__}-{digest}")
            cache[url_path] = hit
        return hit[1]

    def repl(m):
        s = stamp_of(m.group(2))
        return m.group(0) if s is None else f"{m.group(1)}{m.group(2)}?v={s}{m.group(3)}"

    return lambda html: _ASSET_URL.sub(repl, html)


def create_app(camera: Optional[CameraDriverInterface] = None) -> FastAPI:
    """
    Factory function creating and configuring the FastAPI AllSky Edge application.
    """
    app = FastAPI(
        title=PRODUCT,
        description="Zenivo: all-sky observatory software. Edge Web UI and real-time REST/streaming API for the all-sky camera (Sony IMX335 + RTS5869).",
        version=__version__,
    )

    app.add_middleware(GZipMiddleware, minimum_size=1000)

    # Allow CORS for development and cross-origin network access
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Initialize AllSkyService singleton
    service = AllSkyService.get_instance(camera=camera)

    # Include REST and streaming routes
    app.include_router(router)

    # Observational products static mount (/uploads/allsky/timelapses/latest.mp4, keograms, startrails)
    uploads_root = os.path.join(service.output_dir, "uploads")
    os.makedirs(uploads_root, exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=uploads_root), name="uploads")

    # Edge Web UI static files
    web_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "web"))
    if os.path.isdir(web_dir):
        # The page shell must revalidate so a new ?v= on app/*.js/style.css reaches browsers
        no_cache = {
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        }

        stamp = make_asset_stamper(web_dir)

        def read_page(name: str) -> HTMLResponse:
            with open(os.path.join(web_dir, name), encoding="utf-8") as f:
                return HTMLResponse(stamp(f.read()), headers=no_cache)

        index_path = os.path.join(web_dir, "index.html")
        viewport_tag = '<div class="viewport-wrapper" id="viewport-container">'
        shell_cache = {"mtime": None, "html": ""}

        def frame_aspect() -> Optional[str]:
            # Live frame size if one exists, else the driver's configured output size
            frame = service.last_frame
            if frame is not None:
                h, w = frame.shape[:2]
            else:
                w, h = getattr(service.camera, "width", 0), getattr(service.camera, "height", 0)
            return f"{int(w)} / {int(h)}" if w and h else None

        def page_shell() -> HTMLResponse:
            # Pre-size the live viewport to the camera's aspect ratio so the page doesn't jump (CLS)
            mtime = os.path.getmtime(index_path)
            if shell_cache["mtime"] != mtime:
                with open(index_path, encoding="utf-8") as f:
                    shell_cache["html"] = f.read()
                shell_cache["mtime"] = mtime
            html = shell_cache["html"]
            ratio = frame_aspect()
            if ratio:
                html = html.replace(
                    viewport_tag,
                    f'<div class="viewport-wrapper" id="viewport-container" '
                    f'style="aspect-ratio: {ratio}" data-frame-ratio="{ratio}">',
                    1,
                )
            return HTMLResponse(stamp(html), headers=no_cache)

        @app.get("/")
        def serve_root():
            if os.path.exists(index_path):
                return page_shell()
            return {"status": "online", "message": "Zenivo API"}

        # Each UI section has its own URL; all share the same page shell
        @app.get("/live")
        @app.get("/gallery")
        @app.get("/settings")
        @app.get("/calibration")
        @app.get("/logs")
        @app.get("/focus")
        def serve_section_page():
            return page_shell()

        # 3D map of the aircraft the ADS-B receiver hears (standalone page; loads MapLibre + three.js)
        @app.get("/flights3d")
        def serve_flights3d():
            return read_page("flights3d.html")

        # 3D map of the satellites above the station (standalone page; loads MapLibre + three.js)
        @app.get("/satellites3d")
        def serve_satellites3d():
            return read_page("satellites3d.html")

        # Map of the Zenivo stations that share themselves with the community (standalone page; loads Leaflet)
        @app.get("/community")
        def serve_community():
            return read_page("community.html")

        # Interactive planetarium (standalone page; WebGL sky engine bundle under /static/sky, star data under /static/skydata)
        @app.get("/sky")
        def serve_sky():
            return read_page("sky.html")

        # Standalone full-bleed WebSocket live image view for OBS / Kiosk
        @app.api_route("/full", methods=["GET", "HEAD"])
        @app.api_route("/live/full", methods=["GET", "HEAD"])
        @app.api_route("/obs", methods=["GET", "HEAD"])
        def serve_full_live():
            return read_page("full.html")

        # Dedicated OBS Custom Browser Dock control panel
        @app.api_route("/obs/dock", methods=["GET", "HEAD"])
        @app.api_route("/obs/control", methods=["GET", "HEAD"])
        @app.api_route("/dock", methods=["GET", "HEAD"])
        def serve_obs_dock():
            return read_page("obs_dock.html")

        @app.get("/index.html")
        def serve_index_html():
            return page_shell()

        @app.get("/style.css")
        def serve_style_css():
            return FileResponse(os.path.join(web_dir, "style.css"), media_type="text/css", headers=no_cache)

        @app.get("/static/focus.js")
        def serve_focus_js():
            return FileResponse(os.path.join(web_dir, "focus.js"), media_type="application/javascript", headers=no_cache)

        # Brand files at their conventional root URLs (the rest of src/web/brand/ is under /static/brand/)
        @app.get("/favicon.ico", include_in_schema=False)
        def serve_favicon():
            return FileResponse(os.path.join(web_dir, "brand", "favicon.ico"), media_type="image/x-icon",
                                headers={"Cache-Control": "public, max-age=604800"})

        @app.get("/site.webmanifest", include_in_schema=False)
        def serve_manifest():
            return FileResponse(os.path.join(web_dir, "brand", "site.webmanifest"), media_type="application/manifest+json",
                                headers={"Cache-Control": "public, max-age=86400"})

        app.mount("/static", StaticFiles(directory=web_dir), name="static")

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app_instance: FastAPI):
        yield
        service.close()

    app.router.lifespan_context = lifespan

    return app


# Default singleton application (skipped if run_allsky.py manages initialization)
app = None if os.environ.get("ALLSKY_RUNNER") == "1" else create_app()
