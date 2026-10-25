"""
FastAPI Application Entry Point for IMX335 AllSky Observatory Edge API.
Initializes middleware, API routes, static file serving for Edge Web UI, and lifecycle hooks.
"""

import os
from typing import Optional
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse

from src.hardware.driver_interface import CameraDriverInterface
from src.api.service import AllSkyService
from src.api.routes import router


def create_app(camera: Optional[CameraDriverInterface] = None) -> FastAPI:
    """
    Factory function creating and configuring the FastAPI AllSky Edge application.
    """
    app = FastAPI(
        title="IMX335 AllSky Observatory Edge API",
        description="Edge Web UI and Real-Time REST/Streaming API for Sony IMX335 + RTS5869 Camera Subsystem.",
        version="1.0.0",
    )

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
        # The page shell must revalidate so a new ?v= on app.js/style.css reaches browsers
        no_cache = {
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        }

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
            return HTMLResponse(html, headers=no_cache)

        @app.get("/")
        def serve_root():
            if os.path.exists(index_path):
                return page_shell()
            return {"status": "online", "message": "IMX335 AllSky Edge API"}

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
            return FileResponse(os.path.join(web_dir, "flights3d.html"), media_type="text/html", headers=no_cache)

        # Standalone full-bleed WebSocket live image view for OBS / Kiosk
        @app.api_route("/full", methods=["GET", "HEAD"])
        @app.api_route("/live/full", methods=["GET", "HEAD"])
        @app.api_route("/obs", methods=["GET", "HEAD"])
        def serve_full_live():
            with open(os.path.join(web_dir, "full.html"), encoding="utf-8") as f:
                return HTMLResponse(f.read(), headers=no_cache)

        # Dedicated OBS Custom Browser Dock control panel
        @app.api_route("/obs/dock", methods=["GET", "HEAD"])
        @app.api_route("/obs/control", methods=["GET", "HEAD"])
        @app.api_route("/dock", methods=["GET", "HEAD"])
        def serve_obs_dock():
            with open(os.path.join(web_dir, "obs_dock.html"), encoding="utf-8") as f:
                return HTMLResponse(f.read(), headers=no_cache)

        @app.get("/index.html")
        def serve_index_html():
            return page_shell()

        @app.get("/app.js")
        def serve_app_js():
            return FileResponse(os.path.join(web_dir, "app.js"), media_type="application/javascript", headers=no_cache)

        @app.get("/style.css")
        def serve_style_css():
            return FileResponse(os.path.join(web_dir, "style.css"), media_type="text/css", headers=no_cache)

        @app.get("/static/focus.js")
        def serve_focus_js():
            return FileResponse(os.path.join(web_dir, "focus.js"), media_type="application/javascript", headers=no_cache)

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
