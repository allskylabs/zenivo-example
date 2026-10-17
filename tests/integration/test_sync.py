"""
Integration test suite for Resilient Sync Client (src/sync/).
Tests SQLite WAL queue persistence, priority queueing, backoff jitter,
SyncClient multipart/JSON transport, and SyncDaemon circuit breaker/recovery.
"""

import os
import time
import json
import sqlite3
import pytest
from unittest.mock import MagicMock, patch
import requests

from src.sync.queue import SyncQueue
from src.sync.client import SyncClient
from src.sync.daemon import SyncDaemon, CircuitBreakerState


class TestSyncQueue:
    """Tests for persistent SQLite WAL queue."""

    def test_wal_mode_and_priority_ordering(self, tmp_path):
        db_file = str(tmp_path / "sync_wal_test.db")
        q = SyncQueue(db_file)

        # Verify WAL mode
        with q._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode;")
            mode = cur.fetchone()[0]
            assert mode.upper() == "WAL"

        # Enqueue with different priorities
        id_telem = q.enqueue("telemetry", payload={"t": 1}, priority=10)
        id_frame = q.enqueue("frame_latest", payload={"frame": 1}, priority=5)
        id_sum = q.enqueue("summary", payload={"type": "keogram"}, priority=3)
        id_meteor = q.enqueue("meteor_event", payload={"meteor": 1}, priority=1)

        pending = q.fetch_pending(limit=10)
        assert len(pending) == 4
        assert [p["event_type"] for p in pending] == ["meteor_event", "summary", "frame_latest", "telemetry"]
        assert [p["priority"] for p in pending] == [1, 3, 5, 10]

    def test_backoff_jitter_bounds(self):
        # Backoff formula: min(300, 2.0 * 2^n) * (1 +/- 20%)
        for retries in range(10):
            delays = [SyncQueue.calculate_backoff_delay(retries) for _ in range(50)]
            base = min(300.0, 2.0 * (2.0 ** min(retries, 8)))
            min_allowed = max(1.0, base * 0.8)
            max_allowed = min(360.0, base * 1.2)

            for d in delays:
                assert min_allowed - 0.001 <= d <= max_allowed + 0.001, (
                    f"Delay {d} out of bounds [{min_allowed}, {max_allowed}] for retries={retries}"
                )

    def test_mark_failed_and_mark_completed(self, tmp_path):
        db_file = str(tmp_path / "sync_status_test.db")
        q = SyncQueue(db_file)

        item_id = q.enqueue("telemetry", payload={"reading": 42})
        stats1 = q.get_stats()
        assert stats1["pending"] == 1
        assert stats1["completed"] == 0

        # Mark failed once
        delay = q.mark_failed(item_id, "HTTP 500 Connection Refused")
        assert delay >= 1.0

        with q._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT retry_count, last_error FROM sync_queue WHERE id = ?", (item_id,))
            row = cur.fetchone()
            assert row["retry_count"] == 1
            assert "HTTP 500" in row["last_error"]

        # Mark completed
        q.mark_completed(item_id)
        stats2 = q.get_stats()
        assert stats2["pending"] == 0
        assert stats2["completed"] == 1

    def test_prune_stale_telemetry(self, tmp_path):
        db_file = str(tmp_path / "sync_prune_test.db")
        q = SyncQueue(db_file)

        # Enqueue 10 telemetry and 2 critical meteors
        for i in range(10):
            q.enqueue("telemetry", payload={"i": i})
        q.enqueue("meteor_event", payload={"id": "meteor_1"})
        q.enqueue("meteor_event", payload={"id": "meteor_2"})

        assert q.get_stats()["pending"] == 12

        # Prune with threshold 6
        pruned = q.prune_stale_telemetry(max_items=6)
        assert pruned == 6

        # Check surviving items: meteors must never be pruned
        remaining = q.fetch_pending(limit=20)
        assert len(remaining) == 6
        meteor_count = sum(1 for r in remaining if r["event_type"] == "meteor_event")
        assert meteor_count == 2


class TestSyncClient:
    """Tests for HTTP transport client."""

    @patch("requests.Session.post")
    def test_send_frame(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"success": True, "url": "/uploads/allsky/current/image.webp"}
        mock_post.return_value = mock_resp

        client = SyncClient(base_url="http://192.168.3.24:3000", device_token="secret_tok_123")
        res = client.send_frame(image_data=b"\xff\xd8\xff\xd9", exposure_sec=4.0, gain_db=36.0)

        assert res["success"] is True
        assert mock_post.called
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["headers"]["X-Device-Id"] == "hoan-uk-hanoi"
        assert call_kwargs["headers"]["X-Device-Token"] == "secret_tok_123"
        assert call_kwargs["headers"]["X-Exposure-Sec"] == "4.00"
        assert call_kwargs["headers"]["X-Gain-Db"] == "36.0"

    @patch("requests.Session.post")
    def test_send_telemetry(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"success": True}
        mock_post.return_value = mock_resp

        client = SyncClient(base_url="http://192.168.3.24:3000")
        payload = {"star_count": 142, "cloud_cover_pct": 12.0}
        res = client.send_telemetry(payload)

        assert res["success"] is True
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"]["deviceId"] == "hoan-uk-hanoi"
        assert call_kwargs["json"]["star_count"] == 142

    @patch("requests.Session.post")
    def test_send_event(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"success": True, "event_id": "meteor_001"}
        mock_post.return_value = mock_resp

        client = SyncClient(base_url="http://192.168.3.24:3000")
        meta = {"event_id": "meteor_001", "aspect_ratio": 5.2}
        res = client.send_event(metadata=meta, image_data=b"\xff\xd8\xff\xd9")

        assert res["success"] is True
        assert res["event_id"] == "meteor_001"

    @patch("requests.Session.post")
    def test_send_summary(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"success": True, "url": "/uploads/allsky/keograms/2026-10-14.jpg"}
        mock_post.return_value = mock_resp

        client = SyncClient(base_url="http://192.168.3.24:3000")
        res = client.send_summary("keogram", "2026-10-14", b"\xff\xd8\xff\xd9")

        assert res["success"] is True
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["data"]["type"] == "keogram"
        assert call_kwargs["data"]["date"] == "2026-10-14"


class TestSyncDaemonCircuitBreaker:
    """Tests for daemon coordination, circuit breaker, and zero data loss."""

    def test_circuit_breaker_tripping_and_probing(self, tmp_path):
        db_file = str(tmp_path / "daemon_cb_test.db")
        q = SyncQueue(db_file)

        mock_client = MagicMock()
        mock_client.send_telemetry.side_effect = requests.RequestException("Connection refused")

        daemon = SyncDaemon(
            queue=q,
            client=mock_client,
            poll_interval=0.1,
            max_consecutive_failures=3,
            trip_timeout_s=0.2,
        )

        assert daemon.circuit_state == CircuitBreakerState.CLOSED

        # Fail 1
        q.enqueue("telemetry", payload={"p": 1})
        daemon.process_next()
        assert daemon.circuit_state == CircuitBreakerState.CLOSED

        # Fail 2
        q.enqueue("telemetry", payload={"p": 2})
        # Force immediate retry for test
        with q._get_connection() as conn:
            conn.execute("UPDATE sync_queue SET next_retry_at = datetime('now')")
            conn.commit()
        daemon.process_next()
        assert daemon.circuit_state == CircuitBreakerState.CLOSED

        # Fail 3 -> Trips to OPEN
        q.enqueue("telemetry", payload={"p": 3})
        with q._get_connection() as conn:
            conn.execute("UPDATE sync_queue SET next_retry_at = datetime('now')")
            conn.commit()
        daemon.process_next()
        assert daemon.circuit_state == CircuitBreakerState.OPEN

        # While OPEN, process_next returns False immediately without calling client
        call_count_before = mock_client.send_telemetry.call_count
        res = daemon.process_next()
        assert res is False
        assert mock_client.send_telemetry.call_count == call_count_before

        # Wait for trip timeout to expire -> transitions to HALF_OPEN
        time.sleep(0.25)
        assert daemon.circuit_state == CircuitBreakerState.HALF_OPEN

        # If next call succeeds, transitions back to CLOSED
        mock_client.send_telemetry.side_effect = None
        mock_client.send_telemetry.return_value = {"success": True}
        with q._get_connection() as conn:
            conn.execute("UPDATE sync_queue SET next_retry_at = datetime('now')")
            conn.commit()

        success = daemon.process_next()
        assert success is True
        assert daemon.circuit_state == CircuitBreakerState.CLOSED

    def test_outage_simulation_and_zero_data_loss_catchup(self, tmp_path):
        """Full simulated outage: queue accumulates, network recovers, full catch-up."""
        db_file = str(tmp_path / "outage_sim.db")
        q = SyncQueue(db_file)

        mock_client = MagicMock()
        # Network is initially DOWN
        mock_client.send_frame.side_effect = requests.ConnectionError("Down")
        mock_client.send_telemetry.side_effect = requests.ConnectionError("Down")
        mock_client.send_event.side_effect = requests.ConnectionError("Down")

        daemon = SyncDaemon(queue=q, client=mock_client, max_consecutive_failures=10)

        # Enqueue 5 items during outage
        q.enqueue("telemetry", payload={"i": 1})
        q.enqueue("telemetry", payload={"i": 2})
        q.enqueue("frame_latest", payload={"exposure_sec": 4.0})
        q.enqueue("meteor_event", payload={"id": "meteor_during_outage"})
        q.enqueue("summary", payload={"type": "keogram", "date": "2026-10-14"})

        # Try to flush during outage
        daemon.flush_pending(max_items=10)
        stats = q.get_stats()
        assert stats["pending"] == 5
        assert stats["completed"] == 0

        # Network RESTORED
        mock_client.send_frame.side_effect = None
        mock_client.send_frame.return_value = {"success": True}
        mock_client.send_telemetry.side_effect = None
        mock_client.send_telemetry.return_value = {"success": True}
        mock_client.send_event.side_effect = None
        mock_client.send_event.return_value = {"success": True, "event_id": "meteor_during_outage"}
        mock_client.send_summary.return_value = {"success": True}

        # Force immediate eligibility for test catch-up
        with q._get_connection() as conn:
            conn.execute("UPDATE sync_queue SET next_retry_at = datetime('now')")
            conn.commit()

        # Catch-up flush
        flushed = daemon.flush_pending(max_items=10)
        assert flushed == 5

        final_stats = q.get_stats()
        assert final_stats["pending"] == 0
        assert final_stats["completed"] == 5
        assert final_stats["total"] == 5
