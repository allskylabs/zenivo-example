"""
AllSky API Application Exporter.
Allows importing app directly via `from src.api.app import app`.
"""

from src.api.main import app, create_app

__all__ = ["app", "create_app"]
