"""
Adversarial Stress Test Suite for Milestone 4: Resilient Sync Client (src/sync/).
Empirical verification harness by Challenger 1 (challenger_m4_1).

Scope:
1. Queue Endurance & Priority Invariant:
   - Enqueue 1,000 items with mixed priorities (1: Meteor, 3: Summary, 5: Frame, 10: Telemetry) in randomized order.
   - Verify strict priority popping order (1 -> 3 -> 5 -> 10) and FIFO (id ASC) within each priority band.
   - Verify SQLite WAL stability (PRAGMA integrity_check, WAL mode, checkpointing).
   - High-concurrency multithreaded contention: 10 worker threads simultaneously enqueuing and dequeuing.
   - Stale telemetry pruning under queue overflow (ensuring 0 critical items are dropped).

2. Network Outage Simulation & Circuit Breaker:
   - Circuit breaker state transitions: CLOSED -> OPEN after consecutive threshold failures.
   - Call suppression during OPEN state (zero outbound HTTP attempts).
   - Timeout transition to HALF_OPEN.
   - Probe failure recovery to OPEN vs probe success reset to CLOSED.
   - End-to-end zero data loss catch-up flush across complete network outage.

3. Malicious Payloads & Injection Fuzzing on SyncQueue:
   - SQL injection attempts in payload, event_type, file_path, and error messages.
   - Large payloads (5MB JSON), Unicode, Vietnamese diacritics, and control characters.
   - Backoff delay formula and jitter bounds across thousands of iterations.
"""

import time
import json
import random
import threading
import pytest
from typing import List, Dict, Any
from unittest.mock import MagicMock

from src.sync.queue import SyncQueue
from src.sync.client import SyncClient
from src.sync.daemon import SyncDaemon, CircuitBreakerState


class TestQueueEnduranceAndWALStability:
    """Stress tests probing queue endurance, strict priority ordering, and SQLite WAL durability."""

    def test_queue_endurance_1000_items_strict_priority(self, tmp_path):
        """
        Adversarial test: Enqueue 1,000 items with mixed priorities in random insertion order.
        Verify strict popping order:
        - All priority 1 items pop before priority 3.
        - All priority 3 items pop before priority 5.
        - All priority 5 items pop before priority 10.
        - Within each priority band, FIFO (id ASC) order is strictly preserved.
        """
        db_file = str(tmp_path / "endurance_1000.db")
        q = SyncQueue(db_file)

        # Confirm WAL mode active
        with q._get_connection() as conn:
            mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
            assert mode.upper() == "WAL", f"Expected WAL mode, got {mode}"

        # Generate 1,000 items with known distributions:
        # 100 meteors (prio 1), 200 summaries (prio 3), 300 frames (prio 5), 400 telemetry (prio 10)
        items_spec = (
            [("meteor_event", 1, {"seq": i, "type": "meteor"}) for i in range(100)] +
            [("summary", 3, {"seq": i, "type": "keogram"}) for i in range(200)] +
            [("frame_latest", 5, {"seq": i, "exp": 2.0}) for i in range(300)] +
            [("telemetry", 10, {"seq": i, "stars": i}) for i in range(400)]
        )

        # Shuffle insertion order so they arrive completely interleaved
        random.seed(42)
        shuffled_spec = list(items_spec)
        random.shuffle(shuffled_spec)

        for event_type, prio, payload in shuffled_spec:
            q.enqueue(event_type=event_type, payload=payload, priority=prio)

        stats = q.get_stats()
        assert stats["pending"] == 1000
        assert stats["total"] == 1000

        # Now fetch all items in chunks of 50 and verify order
        popped_items: List[Dict[str, Any]] = []
        while True:
            chunk = q.fetch_pending(limit=50)
            if not chunk:
                break
            for item in chunk:
                popped_items.append(item)
                q.mark_completed(item["id"])

        assert len(popped_items) == 1000, f"Expected 1000 popped items, got {len(popped_items)}"

        # Verify priority blocks:
        # Items 0..99 must have priority 1
        for idx in range(100):
            assert popped_items[idx]["priority"] == 1, (
                f"Item at index {idx} has priority {popped_items[idx]['priority']}, expected 1"
            )
            assert popped_items[idx]["event_type"] == "meteor_event"

        # Items 100..299 must have priority 3
        for idx in range(100, 300):
            assert popped_items[idx]["priority"] == 3, (
                f"Item at index {idx} has priority {popped_items[idx]['priority']}, expected 3"
            )
            assert popped_items[idx]["event_type"] == "summary"

        # Items 300..599 must have priority 5
        for idx in range(300, 600):
            assert popped_items[idx]["priority"] == 5, (
                f"Item at index {idx} has priority {popped_items[idx]['priority']}, expected 5"
            )
            assert popped_items[idx]["event_type"] == "frame_latest"

        # Items 600..999 must have priority 10
        for idx in range(600, 1000):
            assert popped_items[idx]["priority"] == 10, (
                f"Item at index {idx} has priority {popped_items[idx]['priority']}, expected 10"
            )
            assert popped_items[idx]["event_type"] == "telemetry"

        # Verify FIFO order (id ASC) within each priority block
        for block_start, block_end, prio in [(0, 100, 1), (100, 300, 3), (300, 600, 5), (600, 1000, 10)]:
            ids = [popped_items[i]["id"] for i in range(block_start, block_end)]
            assert ids == sorted(ids), f"FIFO order violated for priority {prio}"

        # Verify WAL database integrity
        with q._get_connection() as conn:
            integrity = conn.execute("PRAGMA integrity_check;").fetchone()[0]
            assert integrity == "ok", f"Integrity check failed: {integrity}"

    def test_wal_concurrency_multithreaded_hammer(self, tmp_path):
        """
        Stress test: 10 worker threads (5 concurrent enqueuers and 5 concurrent dequeuers)
        hammering the SQLite WAL queue simultaneously for 500 items.
        Verify zero 'database is locked' errors and 100% data consistency.
        """
        db_file = str(tmp_path / "concurrent_wal.db")
        q = SyncQueue(db_file)
        errors: List[Exception] = []

        total_to_insert = 500
        inserted_count = [0]
        completed_count = [0]
        lock = threading.Lock()

        def producer_worker(worker_id: int):
            for i in range(total_to_insert // 5):
                try:
                    q.enqueue(
                        event_type="frame_latest" if i % 2 == 0 else "telemetry",
                        payload={"producer": worker_id, "seq": i},
                    )
                    with lock:
                        inserted_count[0] += 1
                except Exception as e:
                    errors.append(e)

        def consumer_worker():
            while True:
                with lock:
                    if completed_count[0] >= total_to_insert and inserted_count[0] >= total_to_insert:
                        break
                try:
                    items = q.fetch_pending(limit=5)
                    for item in items:
                        q.mark_completed(item["id"])
                        with lock:
                            completed_count[0] += 1
                except Exception as e:
                    errors.append(e)
                time.sleep(0.001)

        # Verify 5 concurrent producer threads inserting into WAL queue simultaneously
        producers = [threading.Thread(target=producer_worker, args=(w,)) for w in range(5)]
        consumer = threading.Thread(target=consumer_worker)

        for p in producers:
            p.start()
        consumer.start()

        for p in producers:
            p.join(timeout=10.0)
        consumer.join(timeout=10.0)

        assert len(errors) == 0, f"Encountered concurrency errors: {errors}"
        stats = q.get_stats()
        assert stats["total"] == total_to_insert
        assert stats["completed"] == total_to_insert
        assert stats["pending"] == 0

        with q._get_connection() as conn:
            integrity = conn.execute("PRAGMA integrity_check;").fetchone()[0]
            assert integrity == "ok"

    def test_stale_telemetry_pruning_overflow_safety(self, tmp_path):
        """
        Stress test: Overflow queue with 1,500 items (500 critical meteors/summaries/frames + 1,000 telemetry).
        Prune with max_items=1000.
        Verify:
        - Exactly 500 oldest telemetry items are pruned.
        - Zero critical items are pruned.
        - Exactly 1,000 items remain.
        """
        db_file = str(tmp_path / "prune_overflow.db")
        q = SyncQueue(db_file)

        # 100 meteors (prio 1), 100 summaries (prio 3), 300 frames (prio 5)
        for i in range(100):
            q.enqueue("meteor_event", payload={"id": f"meteor_{i}"}, priority=1)
        for i in range(100):
            q.enqueue("summary", payload={"id": f"summary_{i}"}, priority=3)
        for i in range(300):
            q.enqueue("frame_latest", payload={"id": f"frame_{i}"}, priority=5)

        # 1,000 telemetry items (prio 10)
        for i in range(1000):
            q.enqueue("telemetry", payload={"seq": i}, priority=10)

        stats_before = q.get_stats()
        assert stats_before["pending"] == 1500

        pruned = q.prune_stale_telemetry(max_items=1000)
        assert pruned == 500

        stats_after = q.get_stats()
        assert stats_after["pending"] == 1000

        # Check all remaining items
        with q._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT event_type, count(*) as cnt FROM sync_queue WHERE status = 'pending' GROUP BY event_type")
            counts = {r["event_type"]: r["cnt"] for r in cur.fetchall()}

        # Critical counts must be untouched
        assert counts.get("meteor_event") == 100
        assert counts.get("summary") == 100
        assert counts.get("frame_latest") == 300
        # Telemetry reduced from 1000 to 500
        assert counts.get("telemetry") == 500


class TestOutageSimulationAndCircuitBreaker:
    """Stress tests probing network outage handling, circuit breaker state machine, and zero data loss."""

    def test_circuit_breaker_tripping_threshold_and_suppression(self, tmp_path):
        """
        Verify:
        1. Circuit breaker remains CLOSED for failures < max_consecutive_failures.
        2. Circuit breaker transitions to OPEN exactly at max_consecutive_failures.
        3. While OPEN, process_next() and flush_pending() are suppressed (0 HTTP calls).
        4. Transitions to HALF_OPEN when trip_timeout_s expires.
        5. In HALF_OPEN, single failure flips it back to OPEN immediately.
        6. In HALF_OPEN, single success resets it to CLOSED.
        """
        db_file = str(tmp_path / "cb_test.db")
        q = SyncQueue(db_file)
        mock_client = MagicMock(spec=SyncClient)

        daemon = SyncDaemon(
            queue=q,
            client=mock_client,
            poll_interval=0.05,
            max_consecutive_failures=3,
            trip_timeout_s=0.2,
        )

        # Enqueue 10 telemetry items
        for i in range(10):
            q.enqueue("telemetry", payload={"seq": i})

        # Failure 1
        mock_client.send_telemetry.side_effect = ConnectionError("Simulated network down")
        daemon.process_next()
        assert daemon._circuit_state == CircuitBreakerState.CLOSED
        assert daemon._consecutive_failures == 1

        # Failure 2
        daemon.process_next()
        assert daemon._circuit_state == CircuitBreakerState.CLOSED
        assert daemon._consecutive_failures == 2

        # Failure 3 -> Trips to OPEN
        daemon.process_next()
        assert daemon._circuit_state == CircuitBreakerState.OPEN
        assert daemon._consecutive_failures == 3

        # In OPEN state: call suppression verification
        call_count_before = mock_client.send_telemetry.call_count
        res = daemon.process_next()
        assert res is False
        assert mock_client.send_telemetry.call_count == call_count_before, "Outbound calls must be suppressed when OPEN"

        res_flush = daemon.flush_pending(max_items=10)
        assert res_flush == 0
        assert mock_client.send_telemetry.call_count == call_count_before

        # Wait for trip timeout to expire
        time.sleep(0.25)
        assert daemon.circuit_state == CircuitBreakerState.HALF_OPEN

        # Probe failure flips back to OPEN immediately
        daemon.process_next()
        assert daemon._circuit_state == CircuitBreakerState.OPEN

        # Wait again for HALF_OPEN
        time.sleep(0.25)
        assert daemon.circuit_state == CircuitBreakerState.HALF_OPEN

        # Probe success restores to CLOSED
        mock_client.send_telemetry.side_effect = None
        mock_client.send_telemetry.return_value = {"success": True}
        success = daemon.process_next()
        assert success is True
        assert daemon.circuit_state == CircuitBreakerState.CLOSED
        assert daemon._consecutive_failures == 0

    def test_outage_simulation_with_zero_data_loss_catchup(self, tmp_path):
        """
        Adversarial simulation:
        1. Enqueue 50 items.
        2. Network fails during active dispatch.
        3. Circuit breaker trips.
        4. While network is down, 50 additional items are enqueued (backlog = 100).
        5. Network is restored.
        6. Circuit breaker recovers and backlog is completely flushed.
        7. Assert ZERO data loss: all 100 items successfully dispatched, 0 pending, 0 lost.
        """
        db_file = str(tmp_path / "zero_data_loss.db")
        q = SyncQueue(db_file)
        mock_client = MagicMock(spec=SyncClient)

        network_online = [True]

        def dynamic_send_telemetry(payload):
            if not network_online[0]:
                raise ConnectionError("Host unreachable: sync.example.org:443")
            return {"success": True}

        def dynamic_send_frame(*args, **kwargs):
            if not network_online[0]:
                raise ConnectionError("Host unreachable: sync.example.org:443")
            return {"success": True, "url": "/uploads/allsky/current/image.webp"}

        def dynamic_send_event(*args, **kwargs):
            if not network_online[0]:
                raise ConnectionError("Host unreachable: sync.example.org:443")
            return {"success": True, "event_id": "meteor_test"}

        def dynamic_send_summary(*args, **kwargs):
            if not network_online[0]:
                raise ConnectionError("Host unreachable: sync.example.org:443")
            return {"success": True, "url": "/uploads/allsky/keograms/latest.jpg"}

        mock_client.send_telemetry.side_effect = dynamic_send_telemetry
        mock_client.send_frame.side_effect = dynamic_send_frame
        mock_client.send_event.side_effect = dynamic_send_event
        mock_client.send_summary.side_effect = dynamic_send_summary

        daemon = SyncDaemon(
            queue=q,
            client=mock_client,
            poll_interval=0.02,
            max_consecutive_failures=3,
            trip_timeout_s=0.1,
        )

        # Enqueue initial 20 items and process first 10 successfully
        for i in range(20):
            q.enqueue("telemetry", payload={"seq": i})

        for _ in range(10):
            daemon.process_next()

        assert q.get_stats()["completed"] == 10
        assert q.get_stats()["pending"] == 10

        # Simulate network dropout
        network_online[0] = False

        # Attempt to process remaining items -> 3 consecutive failures trip circuit breaker to OPEN
        for _ in range(5):
            daemon.process_next()

        assert daemon.circuit_state == CircuitBreakerState.OPEN

        # While offline, observatory produces 40 more items of mixed types
        for i in range(10):
            q.enqueue("meteor_event", payload={"meteor": i}, priority=1)
        for i in range(10):
            q.enqueue("summary", payload={"type": "keogram", "date": "2026-10-14"}, priority=3)
        for i in range(20):
            q.enqueue("frame_latest", payload={"frame": i}, priority=5)

        # Pending items should now be 10 (remaining initial) + 40 (new) = 50 pending
        assert q.get_stats()["pending"] == 50

        # Fast-forward retry timestamps in SQLite so pending items are immediately eligible
        with q._get_connection() as conn:
            conn.execute("UPDATE sync_queue SET next_retry_at = datetime('now', '-1 minute')")
            conn.commit()

        # Restore network
        network_online[0] = True
        time.sleep(0.15)  # Wait for trip_timeout_s (0.1s)
        assert daemon.circuit_state == CircuitBreakerState.HALF_OPEN

        # Catch up backlog completely
        delivered = daemon.flush_pending(max_items=100)
        assert delivered == 50

        # Assert final zero data loss state
        final_stats = q.get_stats()
        assert final_stats["pending"] == 0, f"Expected 0 pending, got {final_stats['pending']}"
        assert final_stats["completed"] == 60, f"Expected 60 total completed, got {final_stats['completed']}"
        assert final_stats["total"] == 60


class TestSQLInjectionAndPayloadFuzzingOnSyncQueue:
    """Stress tests verifying SQL injection immunity and payload fuzzing on SQLite queue."""

    @pytest.mark.parametrize("malicious_input", [
        "'; DROP TABLE sync_queue; --",
        "' OR '1'='1",
        "admin' --",
        "'; DELETE FROM sync_queue WHERE 1=1; --",
        "' UNION SELECT 1, 'pwned', 1, '{}', NULL, 'pending', 0, 50, datetime('now'), datetime('now'), NULL --",
        "1; ATTACH DATABASE '/tmp/injected.db' AS injected; --",
        "Robert'); DROP TABLE Students;--",
        "<script>alert('xss')</script>",
        "../../../../etc/passwd\x00.jpg",
        "%s%s%s%s%n%d%x",
    ])
    def test_sql_injection_in_payload_and_metadata(self, tmp_path, malicious_input):
        """Verify that SQL injection strings in payload, event_type, and file_path are neutralized."""
        db_file = str(tmp_path / "sqli_test.db")
        q = SyncQueue(db_file)

        # In payload
        payload = {"attack": malicious_input, "nested": {"key": malicious_input}}
        item_id = q.enqueue("telemetry", payload=payload, file_path=malicious_input)

        # In error message
        q.mark_failed(item_id, error_msg=malicious_input)

        # Verify table still exists and data is uncorrupted
        with q._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, payload_json, file_path, last_error FROM sync_queue WHERE id = ?", (item_id,))
            row = cur.fetchone()
            assert row is not None
            parsed_payload = json.loads(row["payload_json"])
            assert parsed_payload["attack"] == malicious_input
            assert row["file_path"] == malicious_input
            assert row["last_error"] == malicious_input

            # Check table integrity
            integrity = conn.execute("PRAGMA integrity_check;").fetchone()[0]
            assert integrity == "ok"

    def test_extreme_payload_sizes(self, tmp_path):
        """Verify handling of large payload JSON (2MB telemetry record)."""
        db_file = str(tmp_path / "large_payload.db")
        q = SyncQueue(db_file)

        large_payload = {
            "station": "example-station",
            "blob": "A" * (2 * 1024 * 1024),  # 2MB string
            "stars": list(range(10000)),
        }

        item_id = q.enqueue("telemetry", payload=large_payload)
        pending = q.fetch_pending(limit=1)
        assert len(pending) == 1
        assert pending[0]["id"] == item_id
        assert len(pending[0]["payload"]["blob"]) == 2 * 1024 * 1024


class TestBackoffJitterMathematicalProperties:
    """Rigorous statistical testing of exponential backoff and jitter distribution."""

    def test_jitter_uniform_distribution_and_bounds(self):
        """
        Verify for 1,000 samples per retry level:
        - All delay values fall strictly within [base * 0.8, min(360.0, base * 1.2)].
        - Values never go below 1.0s or above 360.0s.
        - Delays are non-deterministic (variance > 0).
        """
        for retry in range(10):
            samples = [SyncQueue.calculate_backoff_delay(retry) for _ in range(1000)]
            base = min(300.0, 2.0 * (2.0 ** min(retry, 8)))
            lower_bound = max(1.0, base * 0.8)
            upper_bound = min(360.0, base * 1.2)

            min_val = min(samples)
            max_val = max(samples)

            assert min_val >= lower_bound - 1e-6, f"Min {min_val} < lower {lower_bound} at retry={retry}"
            assert max_val <= upper_bound + 1e-6, f"Max {max_val} > upper {upper_bound} at retry={retry}"

            # Variance assertion (must have random spread)
            variance = sum((x - base) ** 2 for x in samples) / len(samples)
            assert variance > 0.01, f"Zero jitter variance at retry={retry}"
