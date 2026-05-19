"""Suite 04: Interceptor Pipeline Stress Test

Measures overhead of sequential interceptor execution.
Tests: noop, sleep, block_all work types at varying interceptor counts.
"""

import asyncio
import re
import time
import os
import tempfile

from tests.stress.config import StressTestConfig
from tests.stress.mocks.bot_registry import MockBotRegistry
from tests.stress.mocks.event_factory import EventFactory
from tests.stress.mocks.message_sender import MockMessageSender
from tests.stress.suites.base import BaseStressTest


class InterceptorPipelineTest(BaseStressTest):
    """Stress test interceptor pipeline sequential execution."""

    @property
    def suite_name(self):
        return "interceptor_pipeline"

    async def setup(self, config: StressTestConfig) -> None:
        self._counts = config.overrides.get("interceptor_counts", [1, 5, 10, 20])
        self._work_types = config.overrides.get("interceptor_work_types", ["noop", "check_10ms", "block_all"])
        self._events_per = config.overrides.get("events_per_scenario", 200)

        self._sender = MockMessageSender("test_bot")

        self._tmpdir = tempfile.mkdtemp(prefix="stress_intercept_")

        from core.plugin.manager import PluginManager
        self._pm = PluginManager(plugins_dir=self._tmpdir, bot_appid="test_bot")

    async def run_phase(self, config: StressTestConfig) -> None:
        suite_label = {"suite": self.suite_name}

        for count in self._counts:
            for work_type in self._work_types:
                if self._stop_event.is_set():
                    break

                print(f"    Interceptors={count} Work={work_type} ...")

                self._setup_scenario(count, work_type)
                events = EventFactory.batch_group_messages(
                    self._events_per, "/ping", ["group_001"],
                    [f"u_{i:04d}" for i in range(min(self._events_per, 1000))],
                    "test_bot",
                )

                for event in events:
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
                            {"suite": self.suite_name,
                             "interceptor_count": str(count),
                             "work_type": work_type},
                        )

    def _setup_scenario(self, interceptor_count, work_type):
        """Setup interceptors and handler for this scenario."""
        # Build interceptors
        interceptors = []
        for i in range(interceptor_count):
            if work_type == "noop":
                async def noop_fn(event):
                    pass
                ic_fn = noop_fn
                is_coro = True
            elif work_type == "check_10ms":
                async def sleep_fn(event, _d=0.01):
                    await asyncio.sleep(_d)
                ic_fn = sleep_fn
                is_coro = True
            elif work_type == "check_50ms":
                async def sleep50_fn(event, _d=0.05):
                    await asyncio.sleep(_d)
                ic_fn = sleep50_fn
                is_coro = True
            elif work_type == "block_all":
                async def block_fn(event):
                    return True
                ic_fn = block_fn
                is_coro = True
            else:
                async def unknown_fn(event):
                    pass
                ic_fn = unknown_fn
                is_coro = True

            interceptors.append({
                "func": ic_fn,
                "is_coro": is_coro,
                "priority": 100 - i,
                "_plugin": f"test_ic_{i}",
            })

        # One catch-all handler
        async def handler_fn(event, match):
            pass
        handlers = [{
            "name": "catch_all",
            "func": handler_fn,
            "pattern": "(.|\\n)*",
            "compiled": re.compile("(.|\\n)*"),
            "priority": 0,
            "is_coro": True,
            "event_types": [],
            "group_only": False,
            "direct_only": False,
            "channel_only": False,
            "owner_only": False,
            "ignore_at_check": False,
            "_allowed_bots": None,
        }]

        self._pm._all_handlers = handlers
        self._pm._all_interceptors = interceptors
        self._pm._plugin_bots = {}
        if hasattr(self._pm, '_apply_bot_bindings'):
            self._pm._apply_bot_bindings()
        self._pm._build_dispatch_index()

    async def teardown(self) -> None:
        import shutil
        if self._tmpdir and os.path.exists(self._tmpdir):
            shutil.rmtree(self._tmpdir, ignore_errors=True)
