"""
Sync daemon for the IMX335 AllSky Camera System.
Coordinates the persistent SQLite queue, exponential backoff retries,
circuit breaker state machine, and HTTP dispatch to the remote sync server.
"""

import os
import time
import threading
import logging
from typing import Dict, Any, Optional

from src.sync.queue import SyncQueue
from src.sync.client import SyncClient

logger = logging.getLogger(__name__)


class CircuitBreakerState:
    CLOSED = "CLOSED"      # Normal operation
    OPEN = "OPEN"          # Tripped, pausing outbound attempts
    HALF_OPEN = "HALF_OPEN"  # Testing connection with a single probe


class SyncDaemon:
    """
    Offline-resilient background sync daemon for AllSky observatory.
    """

    def __init__(
        self,
        queue: SyncQueue,
        client: SyncClient,
        poll_interval: float = 1.0,
        max_consecutive_failures: int = 5,
        trip_timeout_s: float = 30.0,
    ):
        self.queue = queue
        self.client = client
        self.poll_interval = poll_interval
        self.max_consecutive_failures = max_consecutive_failures
        self.trip_timeout_s = trip_timeout_s

        self._consecutive_failures = 0
        self._circuit_state = CircuitBreakerState.CLOSED
        self._last_state_change = time.time()

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def circuit_state(self) -> str:
        # Check if OPEN timeout expired to transition to HALF_OPEN
        if self._circuit_state == CircuitBreakerState.OPEN:
            if time.time() - self._last_state_change >= self.trip_timeout_s:
                self._circuit_state = CircuitBreakerState.HALF_OPEN
                self._last_state_change = time.time()
                logger.info("[SyncDaemon] Circuit breaker transitioned to HALF_OPEN (probing connection)")
        return self._circuit_state

    def _record_success(self) -> None:
        self._consecutive_failures = 0
        if self._circuit_state != CircuitBreakerState.CLOSED:
            logger.info("[SyncDaemon] Connection restored. Circuit breaker reset to CLOSED.")
            self._circuit_state = CircuitBreakerState.CLOSED
            self._last_state_change = time.time()

    def _record_failure(self, error: Exception) -> None:
        self._consecutive_failures += 1
        logger.warning(f"[SyncDaemon] Sync failure #{self._consecutive_failures}: {error}")

        if self._circuit_state == CircuitBreakerState.HALF_OPEN:
            logger.warning("[SyncDaemon] Probe failed. Re-opening circuit.")
            self._circuit_state = CircuitBreakerState.OPEN
            self._last_state_change = time.time()
        elif self._consecutive_failures >= self.max_consecutive_failures:
            if self._circuit_state != CircuitBreakerState.OPEN:
                logger.error(f"[SyncDaemon] {self._consecutive_failures} consecutive failures. Tripping circuit to OPEN for {self.trip_timeout_s}s.")
                self._circuit_state = CircuitBreakerState.OPEN
                self._last_state_change = time.time()

    def dispatch_item(self, item: Dict[str, Any]) -> bool:
        """
        Dispatches a single queue item via SyncClient based on event_type.
        Returns True on success, False on failure.
        """
        event_type = item["event_type"]
        payload = item.get("payload") or {}
        file_path = item.get("file_path")
        item_id = item["id"]

        try:
            if event_type in ("meteor_event", "meteor", "event"):
                self.client.send_event(
                    metadata=payload,
                    image_data=file_path,
                )
            elif event_type in ("frame_latest", "frame"):
                exposure_sec = float(payload.get("exposure_sec", 0.0))
                gain_db = float(payload.get("gain_db", 0.0))
                if file_path and os.path.exists(file_path):
                    self.client.send_frame(
                        image_data=file_path,
                        exposure_sec=exposure_sec,
                        gain_db=gain_db,
                    )
                else:
                    # In-memory payload or raw bytes
                    img_bytes = payload.get("image_bytes") or b"\xff\xd8\xff\xd9"
                    self.client.send_frame(
                        image_data=img_bytes,
                        exposure_sec=exposure_sec,
                        gain_db=gain_db,
                    )
            elif event_type in ("summary", "keogram", "startrails", "timelapse"):
                sum_type = payload.get("type", event_type)
                date_str = payload.get("date", time.strftime("%Y-%m-%d"))
                sum_data = file_path if file_path else payload.get("file_bytes", b"\xff\xd8\xff\xd9")
                self.client.send_summary(
                    summary_type=sum_type,
                    date_str=date_str,
                    file_data=sum_data,
                )
            elif event_type == "telemetry":
                self.client.send_telemetry(payload)
            else:
                logger.warning(f"[SyncDaemon] Unknown event_type: {event_type}, skipping")
                self.queue.mark_completed(item_id)
                return True

            self.queue.mark_completed(item_id)
            self._record_success()
            return True

        except Exception as e:
            self._record_failure(e)
            self.queue.mark_failed(item_id, str(e))
            return False

    def process_next(self) -> bool:
        """
        Process the single highest priority ready item from the queue.
        Returns True if an item was processed, False if queue is empty or circuit is OPEN.
        """
        if self.circuit_state == CircuitBreakerState.OPEN:
            return False

        limit = 1 if self.circuit_state == CircuitBreakerState.HALF_OPEN else 1
        pending = self.queue.fetch_pending(limit=limit)
        if not pending:
            return False

        item = pending[0]
        return self.dispatch_item(item)

    def flush_pending(self, max_items: int = 50) -> int:
        """
        Flushes up to max_items from the queue in strict priority order.
        Returns the number of successfully delivered items.
        """
        delivered = 0
        for _ in range(max_items):
            if self.circuit_state == CircuitBreakerState.OPEN:
                break
            pending = self.queue.fetch_pending(limit=1)
            if not pending:
                break
            success = self.dispatch_item(pending[0])
            if success:
                delivered += 1
            else:
                # First failure in flush stops immediate catch-up loop
                break
        return delivered

    def run_loop(self) -> None:
        """Worker thread loop."""
        logger.info("[SyncDaemon] Starting background sync loop")
        while not self._stop_event.is_set():
            try:
                processed = self.process_next()
                if not processed:
                    time.sleep(self.poll_interval)
            except Exception as e:
                logger.error(f"[SyncDaemon] Unexpected exception in loop: {e}", exc_info=True)
                time.sleep(self.poll_interval)
        logger.info("[SyncDaemon] Background sync loop stopped")

    def start(self) -> None:
        """Start the background sync worker thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self.run_loop, daemon=True, name="SyncDaemonWorker")
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the background worker thread."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
