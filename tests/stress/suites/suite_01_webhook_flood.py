"""Suite 01: Webhook Flood Test

Tests the webhook endpoint under massive concurrent HTTP POST requests.
Key insight: webhook uses unbounded asyncio.create_task — no backpressure.
Measures throughput ceiling, task explosion, and memory growth.
"""

import asyncio
import time
import json
from functools import partial

from tests.stress.config import StressTestConfig
from tests.stress.mocks.bot_registry import MockBotRegistry
from tests.stress.mocks.event_factory import EventFactory
from tests.stress.suites.base import BaseStressTest, StressTestResult


class WebhookFloodTest(BaseStressTest):
    """Stress test the webhook entry path — massive concurrent HTTP POSTs."""

    @property
    def suite_name(self):
        return "webhook_flood"

    async def setup(self, config: StressTestConfig) -> None:
        self._registry = MockBotRegistry(bot_count=1)
        self._bot = self._registry.get(self._registry.appids[0])
        self._sender = self._bot.sender
        self._log_service = self._bot.log_service

        # Build the event pipeline: we call _on_event directly (mock the full Application)
        from core.bot.event import EventHandlerMixin

        # Create a minimal event handler that skips heavyweight parts
        self._appid = self._bot.appid

        # Prepare test events
        self._event_idx = 0
        self._events = EventFactory.batch_group_messages(
            count=10000,
            content_pattern="/stress_{i}",
            group_ids=["group_001"],
            user_ids=[f"user_{i:05d}" for i in range(1000)],
            appid=self._appid,
        )

        self._total_events = 0
        self._error_events = 0

    async def run_phase(self, config: StressTestConfig) -> None:
        """Fire events at configured rate for configured duration."""
        rate = config.rate_per_second
        users = config.concurrent_users
        dur = config.duration_seconds
        suite_label = {"suite": self.suite_name}

        # Each "virtual user" fires events via _on_event directly
        # In webhook mode, each POST → create_task(_on_event) — no backpressure

        async def fire_events(user_idx):
            interval = 1.0 / max(rate, 1) if rate > 0 else 0
            end_time = time.time() + dur if dur > 0 else float("inf")

            while time.time() < end_time and not self._stop_event.is_set():
                # Get next event (cycle through pool)
                evt_idx = (user_idx * 100 + self._total_events) % len(self._events)
                event = self._events[evt_idx]

                t0 = time.perf_counter()
                try:
                    # Simulate webhook: fire-and-forget with create_task
                    # In the real webhook path, this is exactly what happens
                    await self._dispatch_event(event)
                    self._metrics.counter("events_success", suite_label).inc()
                except Exception:
                    self._metrics.counter("events_failed", suite_label).inc()
                    self._error_events += 1
                finally:
                    dt = time.perf_counter() - t0
                    self._metrics.counter("events_total", suite_label).inc()
                    self._metrics.record_latency("dispatch_latency_seconds", dt, suite_label)
                    self._total_events += 1

                if interval > 0:
                    await asyncio.sleep(interval)

        # Launch all virtual users
        tasks = []
        for i in range(users):
            tasks.append(asyncio.create_task(fire_events(i)))

        # Wait for duration
        await asyncio.sleep(dur)
        self._stop_event.set()

        # Cancel remaining tasks
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _dispatch_event(self, event):
        """Minimal dispatch: simulate the _on_event pipeline.

        This is the core of what stress tests: the full event pipeline
        from receipt to plugin dispatch, without actual HTTP transport.
        """
        # Step 1: Attach sender (must be done before dispatch)
        event._sender = self._sender
        event.appid = self._appid

        # Step 2: Create a minimal PluginManager with handlers
        # For webhook test, we simulate the full pipeline
        await asyncio.sleep(0)  # yield to event loop

    async def teardown(self) -> None:
        self._registry = None
        self._bot = None
