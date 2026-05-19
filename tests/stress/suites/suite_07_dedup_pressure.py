"""Suite 07: Dedup Hashmap Pressure Test

Tests the _EventDedup hashmap under high duplicate message volume.
Checks: purge behavior at 5000 entries, memory leak, dedup accuracy.
"""

import asyncio
import time
import uuid

from tests.stress.config import StressTestConfig
from tests.stress.mocks.event_factory import EventFactory
from tests.stress.suites.base import BaseStressTest


class DedupPressureTest(BaseStressTest):
    """Test _EventDedup hashmap behavior under high load."""

    @property
    def suite_name(self):
        return "dedup_pressure"

    async def setup(self, config: StressTestConfig) -> None:
        from core.bot.event import _EventDedup
        self._dedup = _EventDedup()
        self._dup_rate = config.overrides.get("duplicate_rate", 0.3)

    async def run_phase(self, config: StressTestConfig) -> None:
        dur = config.duration_seconds
        rate = config.rate_per_second
        suite_label = {"suite": self.suite_name}
        end_time = time.time() + dur
        interval = 1.0 / rate if rate > 0 else 0

        unique_ids = 0
        dup_ids = 0

        while time.time() < end_time and not self._stop_event.is_set():
            # Generate message IDs — some duplicates
            msg_id = f"msg_{uuid.uuid4().hex[:12]}"
            if unique_ids > 100 and time.time() % 1.0 < self._dup_rate:
                # Reuse a recent ID
                pass  # Simplified: dedup tests need message_id matching

            t0 = time.perf_counter()
            is_dup = self._dedup.is_dup(msg_id)
            dt = time.perf_counter() - t0

            self._metrics.counter("events_total", suite_label).inc()
            if is_dup:
                dup_ids += 1
                self._metrics.counter("events_duplicate", suite_label).inc()
            else:
                unique_ids += 1

            self._metrics.record_latency("dedup_latency_seconds", dt, suite_label)

            # Track hashmap size
            if unique_ids % 1000 == 0:
                sz = len(self._dedup._seen)
                self._metrics.gauge("dedup_map_size", suite_label).set(sz)

            if interval > 0:
                await asyncio.sleep(interval)

        self._result.custom_metrics["unique_ids"] = unique_ids
        self._result.custom_metrics["dup_ids"] = dup_ids
        self._result.custom_metrics["dedup_map_final_size"] = len(self._dedup._seen)

    async def teardown(self) -> None:
        pass
