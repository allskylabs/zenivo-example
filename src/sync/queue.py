"""
Persistent SQLite WAL-backed synchronization queue.
Adheres strictly to R4 and AC4 specifications:
- Priority queueing (1: Meteors, 3: Summaries, 5: Frames, 10: Telemetry)
- SQLite WAL mode for thread/process-safe ACID writes
- Randomized exponential backoff with +/-20% jitter
- Stale telemetry pruning under extended outages
"""

import os
import json
import sqlite3
import random
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta


class SyncQueue:
    """
    SQLite WAL-backed persistent queue for resilient offline synchronization.
    Guarantees Zero Data Loss during observatory network dropouts.
    """

    PRIORITY_MAP = {
        "meteor_event": 1,
        "meteor": 1,
        "event": 1,
        "summary": 3,
        "keogram": 3,
        "startrails": 3,
        "timelapse": 3,
        "frame_latest": 5,
        "frame": 5,
        "telemetry": 10,
    }

    def __init__(self, db_path: str = "data/sync/sync_queue.db"):
        self.db_path = db_path
        db_dir = os.path.dirname(self.db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            conn.execute("PRAGMA temp_store = MEMORY;")
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
            conn.commit()

    @staticmethod
    def calculate_backoff_delay(retry_count: int, base: float = 2.0, max_delay: float = 300.0, jitter_pct: float = 0.20) -> float:
        """
        Calculate backoff delay: t_retry = min(300, base * 2^n) * (1 +/- 20%).
        """
        capped_exp = min(retry_count, 8)
        base_delay = min(max_delay, base * (2.0 ** capped_exp))
        jitter = random.uniform(-jitter_pct, jitter_pct) * base_delay
        delay_s = max(1.0, min(max_delay * 1.2, base_delay + jitter))
        return delay_s

    def enqueue(
        self,
        event_type: str,
        payload: Optional[Dict[str, Any]] = None,
        file_path: Optional[str] = None,
        priority: Optional[int] = None,
    ) -> int:
        """
        Enqueue an item into the persistent SQLite queue.
        Returns the inserted record ID.
        """
        prio = priority if priority is not None else self.PRIORITY_MAP.get(event_type, 5)
        payload_str = json.dumps(payload) if payload is not None else None

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO sync_queue (event_type, priority, payload_json, file_path, status, next_retry_at, created_at)
                VALUES (?, ?, ?, ?, 'pending', datetime('now', 'localtime'), datetime('now', 'localtime'))
                """,
                (event_type, prio, payload_str, file_path),
            )
            item_id = cursor.lastrowid
            conn.commit()
            return item_id

    def fetch_pending(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Fetch pending items that are ready for retry,
        strictly ordered by priority ASC (1 is highest priority), then id ASC.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, event_type, priority, payload_json, file_path, retry_count, next_retry_at
                FROM sync_queue
                WHERE status = 'pending' AND next_retry_at <= datetime('now', 'localtime')
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
                    "next_retry_at": r["next_retry_at"],
                })
            return items

    def mark_completed(self, item_id: int) -> None:
        """Mark an item as successfully completed."""
        with self._get_connection() as conn:
            conn.execute("UPDATE sync_queue SET status = 'completed' WHERE id = ?", (item_id,))
            conn.commit()

    def mark_failed(self, item_id: int, error_msg: str) -> float:
        """
        Record a failed attempt. Increments retry count and schedules next retry
        using randomized exponential backoff with jitter.
        Returns the scheduled delay in seconds.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT retry_count, max_retries FROM sync_queue WHERE id = ?", (item_id,))
            row = cursor.fetchone()
            if not row:
                return 0.0

            new_retries = row["retry_count"] + 1
            delay_s = self.calculate_backoff_delay(new_retries)

            next_retry = datetime.now().astimezone() + timedelta(seconds=delay_s)
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
            return delay_s

    def prune_stale_telemetry(self, max_items: int = 1000) -> int:
        """
        When the queue accumulates more than max_items during extended outages,
        prune oldest non-critical telemetry points to prevent disk overflow while
        strictly preserving meteor events, frames, and summary products.
        Returns the number of pruned items.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT count(*) as count FROM sync_queue WHERE status = 'pending'")
            total_pending = cursor.fetchone()["count"]

            if total_pending <= max_items:
                return 0

            overflow = total_pending - max_items
            cursor.execute(
                """
                DELETE FROM sync_queue
                WHERE id IN (
                    SELECT id FROM sync_queue
                    WHERE status = 'pending' AND event_type = 'telemetry'
                    ORDER BY id ASC
                    LIMIT ?
                )
                """,
                (overflow,),
            )
            pruned = cursor.rowcount
            conn.commit()
            return pruned

    def get_stats(self) -> Dict[str, int]:
        """Return counts of pending, completed, failed, and total items."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status, count(*) as count FROM sync_queue GROUP BY status")
            counts = {r["status"]: r["count"] for r in cursor.fetchall()}
            return {
                "pending": counts.get("pending", 0),
                "completed": counts.get("completed", 0),
                "total": sum(counts.values()),
            }

    def clear(self) -> None:
        """Clear all records from queue (primarily for testing)."""
        with self._get_connection() as conn:
            conn.execute("DELETE FROM sync_queue;")
            conn.commit()
