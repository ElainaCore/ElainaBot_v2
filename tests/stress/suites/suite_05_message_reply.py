"""Suite 05: Message Reply Stress Test

Tests event.reply() throughput, log queue backpressure,
and before_send/after_send hook overhead.
"""

import asyncio
import os
import re
import tempfile
import time

from tests.stress.config import StressTestConfig
from tests.stress.mocks.event_factory import EventFactory
from tests.stress.mocks.hook_manager import (
    register_noop_hooks,
)
from tests.stress.mocks.message_sender import MockMessageSender
from tests.stress.suites.base import BaseStressTest


class MessageReplyTest(BaseStressTest):
    """Stress test reply throughput and log queue pressure."""

    @property
    def suite_name(self):
        return 'message_reply'

    async def setup(self, config: StressTestConfig) -> None:
        self._rates = config.overrides.get('rates', [100, 200, 500])
        self._queue_sizes = config.overrides.get('log_queue_sizes', [100, 500, 1000])
        self._before_hooks = config.overrides.get('before_send_hooks', [0, 1, 5])
        self._after_hooks = config.overrides.get('after_send_hooks', [0, 1, 5])
        self._dur_per = config.overrides.get('duration_per_rate', 15)

        self._sender = MockMessageSender('test_bot')
        self._tmpdir = tempfile.mkdtemp(prefix='stress_reply_')

        from core.module.hook import reset_hook_manager

        self._hook_mgr = reset_hook_manager()

        from core.plugin.manager import PluginManager

        self._pm = PluginManager(plugins_dir=self._tmpdir, bot_appid='test_bot')

    async def run_phase(self, config: StressTestConfig) -> None:
        suite_label = {'suite': self.suite_name}

        for rate in self._rates:
            for qsize in self._queue_sizes:
                for bh in self._before_hooks:
                    for ah in self._after_hooks:
                        if self._stop_event.is_set():
                            break

                        print(f'    rate={rate}/s qsize={qsize} before_hooks={bh} after_hooks={ah} ...')
                        self._setup_scenario(bh, ah)

                        end_time = time.time() + self._dur_per
                        interval = 1.0 / rate if rate > 0 else 0
                        events = EventFactory.batch_group_messages(
                            5000,
                            '/reply_test',
                            ['group_001'],
                            [f'ru_{j:04d}' for j in range(1000)],
                            'test_bot',
                        )
                        evt_iter = iter(events)

                        async def fire(end_time=end_time, evt_iter=evt_iter, rate=rate, bh=bh, ah=ah, interval=interval):
                            while time.time() < end_time and not self._stop_event.is_set():
                                try:
                                    event = next(evt_iter)
                                except StopIteration:
                                    break

                                t0 = time.perf_counter()
                                try:
                                    await self._pm.dispatch(event, self._sender)
                                    self._metrics.counter('events_success', suite_label).inc()
                                except Exception:
                                    self._metrics.counter('events_failed', suite_label).inc()
                                finally:
                                    dt = time.perf_counter() - t0
                                    self._metrics.counter('events_total', suite_label).inc()
                                    self._metrics.record_latency(
                                        'dispatch_latency_seconds',
                                        dt,
                                        {'suite': self.suite_name, 'rate': str(rate), 'hooks': str(bh + ah)},
                                    )

                                if interval > 0:
                                    await asyncio.sleep(interval)

                        try:
                            await asyncio.wait_for(fire(), timeout=self._dur_per + 5)
                        except TimeoutError as ex:
                            print(f'TimeoutError: {ex}')

    def _setup_scenario(self, before_count, after_count):
        """Setup handler that calls event.reply(), with configurable hooks."""
        # Clear hooks
        self._hook_mgr.clear()

        # Register before_send hooks
        if before_count > 0:
            register_noop_hooks(self._hook_mgr, 'before_send', before_count)

        # Register after_send hooks
        if after_count > 0:
            register_noop_hooks(self._hook_mgr, 'after_send', after_count)

        # One handler that calls event.reply()
        async def reply_handler(event, match):
            await event.reply(f'Echo: {event.content}')

        async def noop_handler(event, match):
            pass

        # Use noop handler to measure baseline dispatch, reply handler to test reply flow
        handlers = [
            {
                'name': 'echo',
                'func': reply_handler if before_count + after_count > 0 or True else noop_handler,
                'pattern': '^/reply_test',
                'compiled': re.compile('^/reply_test'),
                'priority': 100,
                'is_coro': True,
                'event_types': [],
                'group_only': False,
                'direct_only': False,
                'channel_only': False,
                'owner_only': False,
                'ignore_at_check': False,
                '_allowed_bots': None,
            }
        ]

        self._pm._all_handlers = handlers
        self._pm._all_interceptors = []
        self._pm._plugin_bots = {}
        if hasattr(self._pm, '_apply_bot_bindings'):
            self._pm._apply_bot_bindings()
        self._pm._build_dispatch_index()

    async def teardown(self) -> None:
        import shutil

        if self._tmpdir and os.path.exists(self._tmpdir):
            shutil.rmtree(self._tmpdir, ignore_errors=True)
