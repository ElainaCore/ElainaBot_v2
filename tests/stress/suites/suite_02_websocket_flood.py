"""Suite 02: WebSocket Flood Test

Tests WebSocket backpressure with Semaphore(256).
Measures: admission rate, semaphore wait time, throughput ceiling vs webhook.
"""

import asyncio
import contextlib
import time

from tests.stress.config import StressTestConfig
from tests.stress.mocks.bot_registry import MockBotRegistry
from tests.stress.mocks.event_factory import EventFactory
from tests.stress.suites.base import BaseStressTest


class WebSocketFloodTest(BaseStressTest):
    """Stress test WebSocket path with Semaphore(256) backpressure."""

    @property
    def suite_name(self):
        return "websocket_flood"

    async def setup(self, config: StressTestConfig) -> None:
        sem_size = config.overrides.get("semaphore_size", 256)
        self._semaphore = asyncio.Semaphore(sem_size)
        self._sem_wait_times = []
        self._sem_acquired = 0
        self._sem_rejected = 0

        self._registry = MockBotRegistry(bot_count=1)
        self._bot = self._registry.get(self._registry.appids[0])
        self._sender = self._bot.sender
        self._appid = self._bot.appid

        self._events = EventFactory.batch_group_messages(
            count=10000,
            content_pattern="/ws_{i}",
            group_ids=["group_001"],
            user_ids=[f"wu_{i:05d}" for i in range(1000)],
            appid=self._appid,
        )
        self._evt_idx = 0

    async def run_phase(self, config: StressTestConfig) -> None:
        rates = config.overrides.get("rates", [100, 500, 1000, 2000])
        dur_per_rate = config.overrides.get("duration_per_rate", 30)
        suite_label = {"suite": self.suite_name}

        for rate in rates:
            if self._stop_event.is_set():
                break

            print(f"    Rate: {rate}/s ...")
            rate_start = time.time()
            rate_end = rate_start + dur_per_rate
            interval = 1.0 / rate if rate > 0 else 0

            async def producer(rate_end=rate_end, interval=interval):
                while time.time() < rate_end and not self._stop_event.is_set():
                    event = self._events[self._evt_idx % len(self._events)]
                    self._evt_idx += 1

                    # Simulate WSClient._dispatch_with_backpressure
                    t_wait = time.perf_counter()
                    async with self._semaphore:
                        wait_dt = time.perf_counter() - t_wait
                        self._sem_wait_times.append(wait_dt)
                        self._sem_acquired += 1

                        t0 = time.perf_counter()
                        try:
                            event._sender = self._sender
                            event.appid = self._appid
                            # Simulate processing (mock 5ms work)
                            await asyncio.sleep(0.005)
                            self._metrics.counter("events_success", suite_label).inc()
                        except Exception:
                            self._metrics.counter("events_failed", suite_label).inc()
                        finally:
                            dt = time.perf_counter() - t0
                            self._metrics.counter("events_total", suite_label).inc()
                            self._metrics.record_latency("dispatch_latency_seconds", dt, suite_label)

                    if interval > 0:
                        await asyncio.sleep(interval)

            # Run producer
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(producer(), timeout=dur_per_rate + 5)

    async def teardown(self) -> None:
        self._result.custom_metrics["sem_acquired"] = self._sem_acquired
        self._result.custom_metrics["sem_wait_avg_ms"] = round(
            (sum(self._sem_wait_times) / max(len(self._sem_wait_times), 1)) * 1000, 2
        )
        self._result.custom_metrics["sem_wait_p99_ms"] = round(
            _p99(self._sem_wait_times) * 1000, 2
        ) if self._sem_wait_times else 0


def _p99(data):
    if not data:
        return 0
    s = sorted(data)
    idx = int(len(s) * 0.99)
    return s[min(idx, len(s) - 1)]
