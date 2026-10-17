"""
FastAPI Application Entry Point for IMX335 AllSky Observatory Edge API.
Initializes middleware, API routes, static file serving for Edge Web UI, and lifecycle hooks.
"""

import os
from typing import Optional
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

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
        no_cache = {"Cache-Control": "no-cache"}

        # Mount static directory
        app.mount("/static", StaticFiles(directory=web_dir), name="static")

        @app.get("/")
        def serve_root():
            index_path = os.path.join(web_dir, "index.html")
            if os.path.exists(index_path):
                return FileResponse(index_path, media_type="text/html", headers=no_cache)
            return {"status": "online", "message": "IMX335 AllSky Edge API"}

        # Each UI section has its own URL; all share the same page shell
        @app.get("/live")
        @app.get("/gallery")
        @app.get("/settings")
        @app.get("/calibration")
        @app.get("/logs")
        @app.get("/focus")
        def serve_section_page():
            return FileResponse(os.path.join(web_dir, "index.html"), media_type="text/html", headers=no_cache)

        @app.get("/index.html")
        def serve_index_html():
            return FileResponse(os.path.join(web_dir, "index.html"), media_type="text/html", headers=no_cache)

        @app.get("/app.js")
        def serve_app_js():
            return FileResponse(os.path.join(web_dir, "app.js"), media_type="application/javascript")

        @app.get("/style.css")
        def serve_style_css():
            return FileResponse(os.path.join(web_dir, "style.css"), media_type="text/css")

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app_instance: FastAPI):
        yield
        service.close()

    app.router.lifespan_context = lifespan

    return app


# Default singleton application (skipped if run_allsky.py manages initialization)
app = None if os.environ.get("ALLSKY_RUNNER") == "1" else create_app()
