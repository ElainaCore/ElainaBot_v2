"""Suite 03: Plugin Dispatch Stress Test

Measures dispatch latency scaling with many registered handlers.
Tests regex matching performance at different match depths.
"""

import asyncio
import contextlib
import os
import re
import tempfile
import time

from tests.stress.config import StressTestConfig
from tests.stress.mocks.event_factory import EventFactory
from tests.stress.mocks.message_sender import MockMessageSender
from tests.stress.suites.base import BaseStressTest


class PluginDispatchTest(BaseStressTest):
    """Stress test plugin dispatch: handler matching with N registered patterns."""

    @property
    def suite_name(self):
        return "plugin_dispatch"

    async def setup(self, config: StressTestConfig) -> None:
        handler_counts = config.overrides.get("handler_counts", [10, 50, 100, 200])
        match_positions = config.overrides.get("match_positions", ["first", "middle", "last", "none"])

        self._scenarios = []
        for n in handler_counts:
            for pos in match_positions:
                self._scenarios.append((n, pos))

        self._sender = MockMessageSender("test_bot")

        # Create a temp plugin dir for PluginManager
        self._tmpdir = tempfile.mkdtemp(prefix="stress_plugins_")

        from core.plugin.manager import PluginManager
        self._pm = PluginManager(plugins_dir=self._tmpdir, bot_appid="test_bot")

    async def run_phase(self, config: StressTestConfig) -> None:
        rate = config.rate_per_second
        dur_per_scenario = config.overrides.get("duration_per_scenario", 10)
        suite_label = {"suite": self.suite_name}

        for handler_count, match_pos in self._scenarios:
            if self._stop_event.is_set():
                break

            print(f"    Handlers={handler_count} Match={match_pos} ...")

            # Rebuild handlers for this scenario
            self._setup_handlers(handler_count, match_pos)
            events = self._generate_events(handler_count, match_pos, 500)
            evt_iter = iter(events)

            end_time = time.time() + dur_per_scenario

            async def fire(end_time=end_time, evt_iter=evt_iter, handler_count=handler_count):
                while time.time() < end_time and not self._stop_event.is_set():
                    try:
                        event = next(evt_iter)
                    except StopIteration:
                        break

                    t0 = time.perf_counter()
                    try:
                        await self._pm.dispatch(event, self._sender)
                        self._metrics.counter("events_success", suite_label).inc()
                    except Exception:
                        self._metrics.counter("events_failed", suite_label).inc()
                    finally:
                        dt = time.perf_counter() - t0
                        self._metrics.counter("events_total", suite_label).inc()
                        self._metrics.record_latency(
                            "dispatch_latency_seconds", dt,
                            {"suite": self.suite_name, "handler_count": str(handler_count)},
                        )

                    # Rate limiting
                    if rate > 0:
                        await asyncio.sleep(1.0 / rate)

            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(fire(), timeout=dur_per_scenario + 5)

    def _setup_handlers(self, count, match_pos):
        """Register N handlers with various regex patterns.

        Handlers are named h_000 to h_{count-1}, sorted by priority (descending).
        match_pos determines which handler the test content will match:
            first: matches h_000 (highest priority)
            middle: matches h_{count//2}
            last: matches h_{count-1} (lowest priority)
            none: matches nothing (all regex tried)
        """
        handlers = []
        for i in range(count):
            # Distribute pattern complexity
            r = i % 10
            if r < 3:  # 30% simple
                pattern = f"^/cmd_{i}\\b"
            elif r < 7:  # 40% medium
                pattern = f"^/calc_{i}\\s+(\\d+)\\s*([+\\-*/])\\s*(\\d+)"
            elif r < 9:  # 20% complex
                pattern = f"^(?:query_{i}|查询_{i}|search_{i})\\s+(.+?)(?:\\s+(?:in|在)\\s+(.+))?$"
            else:  # 10% catch-all (low priority)
                pattern = "(.|\\n)*"

            async def handler_fn(event, match, _idx=i):
                pass

            handlers.append({
                "name": f"h_{i:04d}",
                "func": handler_fn,
                "pattern": pattern,
                "compiled": re.compile(pattern),
                "priority": 100 - i,  # descending: first=100, last=100-count
                "is_coro": True,
                "event_types": [],
                "group_only": False,
                "direct_only": False,
                "channel_only": False,
                "owner_only": False,
                "ignore_at_check": False,
                "_allowed_bots": None,
            })

        # Inject into PluginManager
        self._pm._all_handlers = handlers
        self._pm._all_interceptors = []
        self._pm._plugin_bots = {}
        self._pm._apply_bot_bindings()
        self._pm._build_dispatch_index()

    def _generate_events(self, handler_count, match_pos, n):
        """Generate events that match at the specified position."""
        events = []
        for i in range(n):
            if match_pos == "first":
                content = f"/cmd_0 test_{i}"
            elif match_pos == "middle":
                mid = handler_count // 2
                # Find a medium-complexity handler near middle
                content = f"/cmd_{mid} hello_{i}"
            elif match_pos == "last":
                last = handler_count - 1
                content = f"/cmd_{last} bye_{i}"
            else:  # none
                content = f"zzz_no_match_{i}"

            events.append(EventFactory.group_at_message(
                content, f"user_{i:04d}", "group_001", "test_bot"))
        return events

    async def teardown(self) -> None:
        import shutil
        if self._tmpdir and os.path.exists(self._tmpdir):
            shutil.rmtree(self._tmpdir, ignore_errors=True)
