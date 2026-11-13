"""
HTTP client for an optional AllSky ingestion server (sync is off unless a target URL is configured).
Supports multipart frames, JSON telemetry, meteor events, and summary products.
"""

import os
import json
from typing import Dict, Any, Optional, Union
import requests


def default_device_id() -> str:
    """Neutral device id: ZENIVO_DEVICE_ID env var, else "zenivo-<hostname>"."""
    import socket
    return os.environ.get("ZENIVO_DEVICE_ID") or f"zenivo-{socket.gethostname()}"


class SyncClient:
    """
    HTTP client for pushing frames, telemetry, events, and summaries to a configured ingestion server.
    """

    def __init__(
        self,
        base_url: str = "",
        device_id: Optional[str] = None,
        device_token: Optional[str] = None,
        timeout: float = 15.0,
        session: Optional[requests.Session] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.device_id = device_id or default_device_id()
        self.device_token = device_token
        self.timeout = timeout
        self.session = session or requests.Session()

    def _default_headers(self) -> Dict[str, str]:
        headers = {
            "X-Device-Id": self.device_id,
        }
        if self.device_token:
            headers["X-Device-Token"] = self.device_token
        return headers

    def send_frame(
        self,
        image_data: Union[bytes, str],
        exposure_sec: float = 0.0,
        gain_db: float = 0.0,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        POST /api/allsky/ingest/frame
        Uploads JPEG/WebP frame up to 10MB with camera exposure headers.
        """
        url = f"{self.base_url}/api/allsky/ingest/frame"
        headers = self._default_headers()
        headers["X-Exposure-Sec"] = f"{exposure_sec:.2f}"
        headers["X-Gain-Db"] = f"{gain_db:.1f}"
        if extra_headers:
            headers.update(extra_headers)

        if isinstance(image_data, str):
            with open(image_data, "rb") as f:
                content = f.read()
            filename = os.path.basename(image_data)
        else:
            content = image_data
            filename = "frame.jpg"

        files = {"image": (filename, content, "image/jpeg")}
        response = self.session.post(url, files=files, headers=headers, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def send_telemetry(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        POST /api/allsky/ingest/telemetry
        Uploads device and sky observation telemetry.
        """
        url = f"{self.base_url}/api/allsky/ingest/telemetry"
        headers = self._default_headers()
        headers["Content-Type"] = "application/json"

        # Ensure deviceId is populated
        if "deviceId" not in payload and "station_id" not in payload and "station" not in payload:
            payload["deviceId"] = self.device_id

        response = self.session.post(url, json=payload, headers=headers, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def send_event(
        self,
        metadata: Dict[str, Any],
        image_data: Optional[Union[bytes, str]] = None,
        clip_data: Optional[Union[bytes, str]] = None,
    ) -> Dict[str, Any]:
        """
        POST /api/allsky/ingest/event
        Uploads detected meteor or transient event with trajectory metadata and image crop.
        """
        url = f"{self.base_url}/api/allsky/ingest/event"
        headers = self._default_headers()

        data = {"metadata": json.dumps(metadata)}
        files = {}

        if image_data:
            if isinstance(image_data, str):
                with open(image_data, "rb") as f:
                    img_bytes = f.read()
                filename = os.path.basename(image_data)
            else:
                img_bytes = image_data
                filename = "event_crop.jpg"
            files["image"] = (filename, img_bytes, "image/jpeg")

        if clip_data:
            if isinstance(clip_data, str):
                with open(clip_data, "rb") as f:
                    clip_bytes = f.read()
                clip_filename = os.path.basename(clip_data)
            else:
                clip_bytes = clip_data
                clip_filename = "event_clip.mp4"
            files["clip"] = (clip_filename, clip_bytes, "video/mp4")

        response = self.session.post(
            url,
            data=data,
            files=files if files else None,
            headers=headers,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def send_summary(
        self,
        summary_type: str,
        date_str: str,
        file_data: Union[bytes, str],
    ) -> Dict[str, Any]:
        """
        POST /api/allsky/ingest/summary
        Uploads daily astronomical summary products (Keogram, Startrails, Timelapse).
        """
        url = f"{self.base_url}/api/allsky/ingest/summary"
        headers = self._default_headers()

        data = {
            "type": summary_type,
            "date": date_str,
        }

        if isinstance(file_data, str):
            with open(file_data, "rb") as f:
                content = f.read()
            filename = os.path.basename(file_data)
        else:
            content = file_data
            ext = ".mp4" if summary_type == "timelapse" else ".jpg"
            filename = f"{summary_type}_{date_str}{ext}"

        mime = "video/mp4" if summary_type == "timelapse" else "image/jpeg"
        files = {"file": (filename, content, mime)}

        response = self.session.post(url, data=data, files=files, headers=headers, timeout=self.timeout)
        response.raise_for_status()
        return response.json()
