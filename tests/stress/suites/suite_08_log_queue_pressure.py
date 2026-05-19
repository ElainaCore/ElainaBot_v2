"""Suite 08: Log Queue Pressure Test

Tests LogService queue behavior under high write volume.
Measures: queue growth, QueueFull drops, flush throughput.
"""

import asyncio
import time

from tests.stress.config import StressTestConfig
from tests.stress.mocks.log_service import MockLogService
from tests.stress.suites.base import BaseStressTest


class LogQueuePressureTest(BaseStressTest):
    """Test log queue backpressure with configurable queue sizes."""

    @property
    def suite_name(self):
        return "log_queue_pressure"

    async def setup(self, config: StressTestConfig) -> None:
        self._queue_sizes = config.overrides.get("queue_sizes", [100, 500, 1000, 10000])
        self._write_rate = config.rate_per_second
        self._dur_per = config.overrides.get("duration_per_size", 15)

    async def run_phase(self, config: StressTestConfig) -> None:
        suite_label = {"suite": self.suite_name}

        for qsize in self._queue_sizes:
            if self._stop_event.is_set():
                break

            print(f"    Queue size: {qsize} ...")
            log_svc = MockLogService("test_bot")
            log_svc._queue_maxsize = qsize

            end_time = time.time() + self._dur_per
            interval = 1.0 / self._write_rate if self._write_rate > 0 else 0
            write_count = 0

            async def writer():
                nonlocal write_count
                while time.time() < end_time and not self._stop_event.is_set():
                    data = {
                        "type": "GROUP_AT_MESSAGE_CREATE",
                        "message_id": f"msg_{write_count}",
                        "user_id": f"user_{write_count % 1000:04d}",
                        "group_id": "group_001",
                        "content": f"test message {write_count}",
                        "raw_message": '{"test": true}',
                        "direction": "receive",
                    }

                    t0 = time.perf_counter()
                    ok = log_svc.add_sync("message", data)
                    dt = time.perf_counter() - t0

                    if not ok:
                        self._metrics.counter("log_drops", suite_label).inc()

                    self._metrics.record_latency("log_write_latency", dt, suite_label)
                    self._metrics.counter("log_writes", suite_label).inc()
                    write_count += 1

                    # Sample queue snapshot
                    if write_count % 100 == 0:
                        snap = log_svc.queue_snapshot()
                        self._metrics.gauge("log_queue_depth").set(snap["current_depth"])

                    if interval > 0:
                        await asyncio.sleep(interval)

                # Flush remaining
                log_svc.drain_queue()

            try:
                await asyncio.wait_for(writer(), timeout=self._dur_per + 5)
            except asyncio.TimeoutError:
                pass

            stats = log_svc.stats()
            self._result.custom_metrics[f"qsize_{qsize}"] = stats

    async def teardown(self) -> None:
        pass
