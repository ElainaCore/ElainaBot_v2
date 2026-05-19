"""Suite 06: Mixed Load / Endurance Test

Long-running test with mixed event types.
Checks for memory leaks, task accumulation, and throughput stability.
"""

import asyncio
import gc
import os
import re
import tempfile
import time

from tests.stress.config import StressTestConfig
from tests.stress.mocks.bot_registry import MockBotRegistry
from tests.stress.mocks.event_factory import EventFactory
from tests.stress.mocks.hook_manager import (
    register_noop_hooks,
)
from tests.stress.suites.base import BaseStressTest


class MixedEnduranceTest(BaseStressTest):
    """Long-running mixed load test for memory leak and stability detection."""

    @property
    def suite_name(self):
        return "mixed_endurance"

    async def setup(self, config: StressTestConfig) -> None:
        self._durations = config.overrides.get("durations", [300])  # default 5min
        self._rate = config.rate_per_second
        self._sample_interval = config.overrides.get("sample_interval", 10)
        self._mem_threshold = config.overrides.get("memory_leak_threshold_mb", 50)
        self._task_threshold = config.overrides.get("task_growth_threshold", 10)

        self._bot_count = config.overrides.get("bot_count", 3)
        self._handler_count = config.overrides.get("handler_count", 100)
        self._interceptor_count = config.overrides.get("interceptor_count", 5)

        self._registry = MockBotRegistry(bot_count=self._bot_count)
        self._appid = self._registry.appids[0]
        self._sender = self._registry.get_sender()

        self._tmpdir = tempfile.mkdtemp(prefix="stress_endurance_")

        from core.module.hook import reset_hook_manager
        self._hook_mgr = reset_hook_manager()
        register_noop_hooks(self._hook_mgr, "before_send", 1)
        register_noop_hooks(self._hook_mgr, "after_send", 1)

        from core.plugin.manager import PluginManager
        self._pm = PluginManager(plugins_dir=self._tmpdir, bot_appid=self._appid)

        self._setup_handlers()

    async def run_phase(self, config: StressTestConfig) -> None:
        suite_label = {"suite": self.suite_name}

        for dur in self._durations:
            if self._stop_event.is_set():
                break

            print(f"    Duration: {dur}s ({dur / 60:.0f}min) ...")
            interval = 1.0 / self._rate if self._rate > 0 else 0
            end_time = time.time() + dur

            # Take baseline
            baseline_mem = _get_rss_mb()
            baseline_tasks = len(asyncio.all_tasks())
            gc.collect()

            events = EventFactory.batch_mixed_events(50000, self._appid)
            evt_iter = iter(events)
            last_sample = time.time()
            evt_count = 0

            async def fire(end_time=end_time, evt_iter=evt_iter, interval=interval):
                nonlocal evt_count, last_sample
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
                        self._metrics.record_latency("dispatch_latency_seconds", dt, suite_label)
                        evt_count += 1

                    # Periodic GC and memory check
                    now = time.time()
                    if now - last_sample >= self._sample_interval:
                        last_sample = now
                        gc.collect()
                        cur_mem = _get_rss_mb()
                        cur_tasks = len(asyncio.all_tasks())
                        self._metrics.gauge("memory_mb", suite_label).set(cur_mem)
                        self._metrics.gauge("task_count", suite_label).set(cur_tasks)

                    if interval > 0:
                        await asyncio.sleep(interval)

            try:
                await asyncio.wait_for(fire(), timeout=dur + 10)
            except TimeoutError as ex:
                print(f'TimeoutError: {ex}')

            # Final check
            gc.collect()
            final_mem = _get_rss_mb()
            final_tasks = len(asyncio.all_tasks())

            self._result.custom_metrics[f"dur_{dur}s_mem_delta"] = round(final_mem - baseline_mem, 1)
            self._result.custom_metrics[f"dur_{dur}s_task_delta"] = final_tasks - baseline_tasks

    def _setup_handlers(self):
        """Setup N handlers with mixed patterns."""
        handlers = []
        interceptors = []

        for i in range(self._handler_count):
            r = i % 4
            if r == 0:
                pattern = f"^/cmd_{i}\\b"
            elif r == 1:
                pattern = f"^/query_{i}\\s+(.+)"
            elif r == 2:
                pattern = f"^(?:查询_{i}|search_{i})\\s+(.+)"
            else:
                pattern = f"^/help_{i}"

            async def make_handler(_idx=i):
                async def h(event, match):
                    pass
                return h

            handlers.append({
                "name": f"h_{i:04d}",
                "func": asyncio.get_event_loop().create_future  # placeholder, will be replaced
                if False else _make_sync_handler(i),
                "pattern": pattern,
                "compiled": re.compile(pattern),
                "priority": 100 - i,
                "is_coro": False,
                "event_types": [],
                "group_only": False,
                "direct_only": False,
                "channel_only": False,
                "owner_only": False,
                "ignore_at_check": False,
                "_allowed_bots": None,
            })

        # Build interceptors
        for i in range(self._interceptor_count):
            async def ic_fn(event):
                pass
            interceptors.append({
                "func": ic_fn,
                "is_coro": True,
                "priority": 100 - i,
                "_plugin": f"test_ic_{i}",
            })

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


def _make_sync_handler(idx):
    """Create a sync handler function."""
    def handler(event, match):
        pass
    return handler


def _get_rss_mb():
    try:
        import os

        import psutil
        return round(psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024, 1)
    except ImportError:
        return 0
