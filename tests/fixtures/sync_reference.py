"""
Authoritative Reference SQLite Sync Queue and Mock sync.example.org Ingestion Server for AC4 Testing.
Derived directly from ORIGINAL_REQUEST.md § R4, PROJECT.md § 4, and the sync survey § 4.
"""

import json
import sqlite3
import random
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI, UploadFile, File, Form, Header, HTTPException, status
from fastapi.responses import JSONResponse


class ReferenceSyncQueue:
    """
    SQLite WAL-backed persistent queue for resilient offline synchronization.
    Guarantees Zero Data Loss during observatory network dropouts.
    """

    PRIORITY_MAP = {
        "meteor_event": 1,
        "summary": 3,
        "keogram": 3,
        "startrails": 3,
        "timelapse": 3,
        "frame_latest": 5,
        "frame": 5,
        "telemetry": 10,
    }

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sync_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    priority INTEGER DEFAULT 5,
                    payload_json TEXT,
                    file_path TEXT,
                    status TEXT DEFAULT 'pending',
                    retry_count INTEGER DEFAULT 0,
                    max_retries INTEGER DEFAULT 50,
                    next_retry_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_error TEXT
                );
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_sync_queue_status_retry
                ON sync_queue (status, next_retry_at, priority);
            """)

    def enqueue(
        self,
        event_type: str,
        payload: Optional[Dict[str, Any]] = None,
        file_path: Optional[str] = None,
        priority: Optional[int] = None,
    ) -> int:
        """Enqueue an item into the persistent SQLite queue."""
        prio = priority if priority is not None else self.PRIORITY_MAP.get(event_type, 5)
        payload_str = json.dumps(payload) if payload is not None else None

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO sync_queue (event_type, priority, payload_json, file_path, status, next_retry_at)
                VALUES (?, ?, ?, ?, 'pending', datetime('now'))
                """,
                (event_type, prio, payload_str, file_path),
            )
            item_id = cursor.lastrowid
            conn.commit()
            return item_id

    def fetch_pending(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Fetch pending items sorted by priority (1 is highest) and creation time."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, event_type, priority, payload_json, file_path, retry_count
                FROM sync_queue
                WHERE status = 'pending' AND next_retry_at <= datetime('now')
                ORDER BY priority ASC, id ASC
                LIMIT ?
                """,
                (limit,),
            )
            rows = cursor.fetchall()
            items = []
            for r in rows:
                items.append({
                    "id": r["id"],
                    "event_type": r["event_type"],
                    "priority": r["priority"],
                    "payload": json.loads(r["payload_json"]) if r["payload_json"] else None,
                    "file_path": r["file_path"],
                    "retry_count": r["retry_count"],
                })
            return items

    def mark_completed(self, item_id: int):
        """Mark item as completed and remove or set status='completed'."""
        with self._get_connection() as conn:
            conn.execute("UPDATE sync_queue SET status = 'completed' WHERE id = ?", (item_id,))
            conn.commit()

    def mark_failed(self, item_id: int, error_msg: str):
        """Record retry attempt and compute next retry time using exponential backoff with jitter."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT retry_count FROM sync_queue WHERE id = ?", (item_id,))
            row = cursor.fetchone()
            if not row:
                return

            new_retries = row["retry_count"] + 1
            # Exponential backoff: t = min(300, 2.0 * 2^retries) * (1 +/- 0.2 jitter)
            base_delay = min(300.0, 2.0 * (2.0 ** min(new_retries, 8)))
            jitter = random.uniform(-0.2, 0.2) * base_delay
            delay_s = max(1.0, base_delay + jitter)

            next_retry = datetime.now(timezone.utc) + timedelta(seconds=delay_s)
            next_retry_str = next_retry.strftime("%Y-%m-%d %H:%M:%S")

            conn.execute(
                """
                UPDATE sync_queue
                SET retry_count = ?, next_retry_at = ?, last_error = ?, status = 'pending'
                WHERE id = ?
                """,
                (new_retries, next_retry_str, str(error_msg), item_id),
            )
            conn.commit()

    def get_stats(self) -> Dict[str, int]:
        """Return counts of pending, completed, and total items."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status, count(*) as count FROM sync_queue GROUP BY status")
            counts = {r["status"]: r["count"] for r in cursor.fetchall()}
            return {
                "pending": counts.get("pending", 0),
                "completed": counts.get("completed", 0),
                "total": sum(counts.values()),
            }


def create_mock_sync_app(network_online: bool = True) -> FastAPI:
    """
    Mock sync.example.org ingestion API matching server/api/routes/allskyIngest.js exactly.
    """
    app = FastAPI(title="Mock sync.example.org Ingestion API", version="1.0.0")
    app.state.network_online = network_online
    app.state.received_frames = []
    app.state.received_telemetry = []
    app.state.received_events = []
    app.state.received_summaries = []

    @app.middleware("http")
    async def simulate_network_condition(request, call_next):
        if not app.state.network_online:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"error": "Network unreachable or sync.example.org offline"},
            )
        return await call_next(request)

    @app.post("/api/allsky/ingest/frame")
    async def ingest_frame(
        image: UploadFile = File(...),
        x_device_id: Optional[str] = Header(None, alias="X-Device-Id"),
        x_exposure_sec: Optional[str] = Header(None, alias="X-Exposure-Sec"),
        x_gain_db: Optional[str] = Header(None, alias="X-Gain-Db"),
    ):
        content = await image.read()
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Payload exceeds 10MB ceiling")

        record = {
            "device_id": x_device_id or "example-station",
            "size_bytes": len(content),
            "exposure_sec": float(x_exposure_sec or 0.0),
            "gain_db": float(x_gain_db or 0.0),
            "filename": image.filename,
            "received_at": datetime.now(timezone.utc).isoformat(),
        }
        app.state.received_frames.append(record)
        return {"success": True, "url": "/uploads/allsky/current/image.webp"}

    @app.post("/api/allsky/ingest/telemetry")
    async def ingest_telemetry(payload: Dict[str, Any]):
        app.state.received_telemetry.append(payload)
        return {"success": True}

    @app.post("/api/allsky/ingest/event")
    async def ingest_event(
        metadata: str = Form(...),
        image: Optional[UploadFile] = File(None),
    ):
        event_meta = json.loads(metadata)
        record = {
            "metadata": event_meta,
            "has_image": image is not None,
            "received_at": datetime.now(timezone.utc).isoformat(),
        }
        app.state.received_events.append(record)
        return {"success": True, "event_id": event_meta.get("event_id", "evt_unknown")}

    @app.post("/api/allsky/ingest/summary")
    async def ingest_summary(
        summary_type: str = Form(..., alias="type"),
        date: str = Form(...),
        file: UploadFile = File(...),
    ):
        content = await file.read()
        record = {
            "type": summary_type,
            "date": date,
            "size": len(content),
            "filename": file.filename,
        }
        app.state.received_summaries.append(record)
        return {"success": True, "url": f"/uploads/allsky/{summary_type}/{date}.jpg"}

    return app
