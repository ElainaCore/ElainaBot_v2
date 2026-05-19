"""MockLogService — in-memory log service for stress testing.

Wraps the LogService interface without SQLite writes.
Records queue depth over time, drop counts, and flush latency.
"""

import time
from collections import deque

from tests.stress.config import MockConfig


class MockLogService:
    """In-memory log service for stress testing.

    Mimics core.storage.log.LogService interface but keeps all data
    in memory. Tracks queue pressure and drop events.
    """

    def __init__(self, appid="0000", config=None):
        self._appid = str(appid)
        self._config = config or MockConfig()
        self._logs = {"message": deque(maxlen=10000), "lifecycle": deque(maxlen=5000)}
        self._queue_maxsize = 1000
        self._queue_current = 0
        self._queue_peak = 0
        self._drop_count = 0
        self._add_count = 0
        self._flush_count = 0
        self._total_flush_time = 0.0
        self._data_store = {}  # Simulated DB queries
        self._started = False
        self._running = True

    # ---- Public interface (matches LogService) ----

    async def start(self):
        self._started = True

    async def stop(self):
        self._running = False

    def add_sync(self, log_type, data):
        """Synchronous add to in-memory queue (matches LogService.add_sync)."""
        self._add_count += 1
        self._queue_current += 1
        if self._queue_current > self._queue_peak:
            self._queue_peak = self._queue_current

        if log_type in self._logs:
            q = self._logs[log_type]
            if len(q) >= q.maxlen:
                self._drop_count += 1
                return False
            q.append({"ts": time.time(), "data": data})
        return True

    async def add(self, log_type, data):
        """Async add (used by asyncio.ensure_future in sender)."""
        return self.add_sync(log_type, data)

    def db_queue(self, sql, params=()):
        """Simulated DB write queue."""
        self._add_count += 1

    async def db_execute(self, sql, params=()):
        """Simulated DB execute."""
        return None

    async def db_fetch_one(self, sql, params=()):
        """Simulated DB fetch — returns None (simulates 'not found')."""
        return None

    async def db_fetch_value(self, sql, default=0, params=()):
        """Simulated DB fetch value."""
        return default

    def query_data(self, sql, params=()):
        """Sync query data (used via executor)."""
        return []

    def query(self, log_type, sql, params=(), date=None):
        """Sync query."""
        return []

    # ---- Sharing / Wakeup stubs ----

    async def share_record(self, sharer_id, user_id, scene):
        pass

    async def wakeup_update(self, user_id):
        pass

    async def wakeup_can_send(self, user_id):
        return (False, 0, -1)

    async def wakeup_mark_sent(self, user_id, stage):
        pass

    # ---- Queue monitoring ----

    def queue_snapshot(self):
        """Return current queue state for monitoring."""
        return {
            "current_depth": self._queue_current,
            "peak_depth": self._queue_peak,
            "drop_count": self._drop_count,
            "add_count": self._add_count,
            "flush_count": self._flush_count,
            "queue_maxsize": self._queue_maxsize,
        }

    def drain_queue(self):
        """Simulate a flush cycle, reducing queue depth."""
        t0 = time.perf_counter()
        # In real LogService, this writes batches to SQLite.
        # Here we just pop items.
        for log_type in self._logs:
            self._logs[log_type].clear()
        dropped = self._queue_current
        self._queue_current = 0
        self._flush_count += 1
        self._total_flush_time += time.perf_counter() - t0
        return dropped

    def stats(self):
        return {
            "appid": self._appid,
            "add_count": self._add_count,
            "queue_peak": self._queue_peak,
            "drop_count": self._drop_count,
            "flush_count": self._flush_count,
            "avg_flush_ms": round(
                (self._total_flush_time / max(self._flush_count, 1)) * 1000, 2
            ),
        }
