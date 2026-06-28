"""Suite 09: Message Receive End-to-End Stress Test

Tests the full _on_event pipeline: dedup → log write → web push → user tracking → dispatch → reply.
This is the real-world receive path, not just isolated dispatch().

Key measurements:
- _on_event total latency breakdown
- Dedup HashMap O(1) performance at 10k entries
- log_service.add_sync() queue pressure
- User tracking create_task() memory impact
- Config lookup caching overhead
"""

import asyncio
import contextlib
import os
import re
import tempfile
import time

from tests.stress.config import StressTestConfig
from tests.stress.mocks.bot_registry import MockBotRegistry
from tests.stress.mocks.event_factory import EventFactory
from tests.stress.suites.base import BaseStressTest


class ReceiveEventHandler:
    """Minimal EventHandlerMixin-like harness for stress testing _on_event.

    Mirrors the real code path in core/bot/event.py:EventHandlerMixin._on_event()
    but with mock dependencies for log_service, sender, plugin_manager.
    """

    def __init__(self):
        # Event state (same as _init_event_state)
        self._dedup = {}
        self._known_users = {}
        self._cache_clean_ts = 0
        self._group_users_cache = {}
        self._group_locks = {}
        self._full_access_cache = {}

        # Mock components - set by setup
        self._bots = {}
        self._plugin_manager = None

        # Callback counters for stats
        self._push_web_log_count = 0
        self._log_add_count = 0
        self._track_user_count = 0
        self._dedup_hit_count = 0
        self._dedup_miss_count = 0
        self._early_return_count = 0

    async def _on_event(self, event):
        """Replicates the real _on_event hot path with mock components.

        This is intentionally kept structurally identical to core/bot/event.py
        so that the stress test measures real-world overhead.
        """
        import json

        from core.base.config import cfg
        from core.bot.event import (
            INTERACTION_CREATE,
            MESSAGE_TYPES,
            SILENT_TYPES,
            EventHandlerMixin,
            _EventDedup,
        )

        # 生命周期事件类型集 (镜像 core/bot/event.py 的 _LIFECYCLE_HANDLERS 字典键)
        lifecycle_types = EventHandlerMixin._LIFECYCLE_HANDLERS

        appid = event.appid
        bot = self._bots.get(appid)
        if not bot:
            return

        et = event.event_type

        # ── Dedup check ──
        if cfg.get_bot_setting(appid, 'dedup.enabled', False):
            dedup = self._dedup.setdefault(appid, _EventDedup())  # pyright: ignore[reportPossiblyUnboundVariable]
            if dedup.is_dup(event.message_id, event.event_id):
                self._dedup_hit_count += 1
                return
            self._dedup_miss_count += 1

        # ── Silent events: log + web push, no dispatch ──
        if et in SILENT_TYPES:
            raw_json = json.dumps(event.raw, ensure_ascii=False)
            bot.log_service.add_sync(
                'lifecycle',
                {
                    'type': et,
                    'user_id': event.user_id or '',
                    'group_id': event.group_id or '',
                    'extra': raw_json,
                },
            )
            self._push_web_log(
                'event',
                {
                    'appid': appid,
                    'event_type': et,
                    'content': raw_json,
                    'raw_message': raw_json,
                    'bot_name': bot.name,
                },
            )
            self._early_return_count += 1
            return

        # ── Lifecycle events (simplified: skip actual lifecycle handlers) ──
        if et in lifecycle_types:
            self._early_return_count += 1
            return

        # ── Unknown events ──
        if et not in MESSAGE_TYPES and et != INTERACTION_CREATE and et not in SILENT_TYPES:
            self._push_web_log(
                'error',
                {
                    'appid': appid,
                    'source': '未知事件',
                    'content': f'收到未预设事件类型: {et}',
                },
            )
            self._early_return_count += 1
            return

        _t0 = time.time()

        # ── Message log + web push ──
        if et in MESSAGE_TYPES or et == INTERACTION_CREATE:
            raw_json = json.dumps(event.raw, ensure_ascii=False)
            log_entry = {
                'type': et,
                'message_id': event.message_id or '',
                'user_id': event.user_id or '',
                'group_id': event.group_id or '',
                'content': event.content or '',
                'raw_message': raw_json,
                'direction': 'receive',
            }
            bot.log_service.add_sync('message', log_entry)
            self._log_add_count += 1
            self._push_web_log(
                'message',
                {
                    'type': et,
                    'message_id': event.message_id or '',
                    'user_id': event.user_id or '',
                    'group_id': event.group_id or '',
                    'content': event.content or '',
                    'direction': 'receive',
                    'appid': appid,
                    'bot_name': bot.name,
                    'bot_qq': getattr(bot, 'robot_qq', '') or '',
                    'event_type': et,
                },
            )

            # User tracking (fire-and-forget — simulates create_task overhead)
            if event.user_id:
                # In production this is asyncio.create_task(self._track_user(...))
                # We simulate the overhead by doing a db_queue call
                bot.log_service.db_queue(
                    'INSERT OR IGNORE INTO members (user_id) VALUES (?)',
                    (event.user_id,),
                )
                self._track_user_count += 1

        # ── Full access group record ──
        if et == 'GROUP_MESSAGE_CREATE' and event.group_id:
            ts = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
            bot.log_service.db_queue(
                'INSERT OR IGNORE INTO full_access_groups (group_id, first_seen) VALUES (?, ?)',
                (event.group_id, ts),
            )

        # ── @all filter ──
        if et == 'GROUP_MESSAGE_CREATE' and getattr(event, 'is_at_all', False):
            self._early_return_count += 1
            return

        # ── Bot sender filter ──
        if getattr(event, 'is_bot', False) and cfg.get_bot_setting(appid, 'non_at_message.ignore_bot_sender', False):
            self._early_return_count += 1
            return

        # ── Plugin dispatch (core performance hot path) ──
        if not self._plugin_manager:
            return
        with contextlib.suppress(Exception):
            await self._plugin_manager.dispatch(event, bot.sender)

        _dt = time.time() - _t0
        if _dt > 1:
            # Performance warning (logged but not acted on in stress test)
            pass

    def _push_web_log(self, log_type, entry):
        """Noop web panel push — we count calls for stats."""
        self._push_web_log_count += 1

    def stats(self):
        return {
            'push_web_log': self._push_web_log_count,
            'log_add': self._log_add_count,
            'track_user': self._track_user_count,
            'dedup_hit': self._dedup_hit_count,
            'dedup_miss': self._dedup_miss_count,
            'early_return': self._early_return_count,
        }


class MessageReceiveTest(BaseStressTest):
    """End-to-end stress test for the complete _on_event receive path.

    Tests the full pipeline: dedup → log write → web push → user tracking → dispatch.

    Scenarios:
        1. Baseline: minimal config (no dedup, 1 handler), varying rates
        2. With dedup: dedup enabled, test HashMap scaling
        3. Multi-bot: events spread across N bots, test per-bot isolation
        4. Mixed event types: message + silent + lifecycle, test early-return paths
    """

    @property
    def suite_name(self):
        return 'message_receive'

    async def setup(self, config: StressTestConfig) -> None:
        self._rates = config.overrides.get('rates', [100, 500, 1000])
        self._scenarios = config.overrides.get('scenarios', ['baseline', 'with_dedup'])
        self._bot_count = config.overrides.get('bot_count', 1)
        self._enable_dedup = config.overrides.get('enable_dedup', False)
        self._dur_per = config.overrides.get('duration_per_scenario', 10)
        self._handler_count = config.overrides.get('handler_count', 10)

        # Create bot registry
        self._registry = MockBotRegistry(bot_count=self._bot_count)

        # Create EventHandlerMixin-like harness
        self._handler = ReceiveEventHandler()
        self._handler._bots = self._registry.bots

        # Setup PluginManager with N handlers (for dispatch)
        self._tmpdir = tempfile.mkdtemp(prefix='stress_receive_')
        from core.plugin.manager import PluginManager

        self._pm = PluginManager(plugins_dir=self._tmpdir, bot_appid=self._registry.appids[0])
        self._setup_handlers(self._handler_count)
        self._handler._plugin_manager = self._pm

    def _setup_handlers(self, count):
        """Register N handlers in PluginManager."""
        handlers = []
        for i in range(count):

            async def handler_fn(event, match, _idx=i):
                # Simulate light work: 1ms
                await asyncio.sleep(0.001)

            handlers.append(
                {
                    'name': f'receive_h_{i:04d}',
                    'func': handler_fn,
                    'pattern': f'^/cmd_{i}\\b' if i < count - 1 else '(.|\\n)*',
                    'compiled': re.compile(f'^/cmd_{i}\\b' if i < count - 1 else '(.|\\n)*'),
                    'priority': 100 - i,
                    'is_coro': True,
                    'event_types': [],
                    'group_only': False,
                    'direct_only': False,
                    'channel_only': False,
                    'owner_only': False,
                    'ignore_at_check': False,
                    '_allowed_bots': None,
                }
            )
        self._pm._all_handlers = handlers
        self._pm._all_interceptors = []
        self._pm._plugin_bots = {}
        self._pm._apply_bot_bindings()
        self._pm._build_dispatch_index()

    async def run_phase(self, config: StressTestConfig) -> None:
        suite_label = {'suite': self.suite_name}
        appid = self._registry.appids[0]
        concurrency = config.overrides.get('concurrency', 1)

        for scenario in self._scenarios:
            if self._stop_event.is_set():
                break

            for rate in self._rates:
                if self._stop_event.is_set():
                    break

                print(f'    Scenario={scenario} Rate={rate}/s Concurrency={concurrency} ...')
                self._configure_scenario(scenario, appid)

                events = EventFactory.batch_group_messages(
                    count=max(rate * self._dur_per, 1000),
                    content_pattern='/cmd_0 msg_{i}',
                    group_ids=['group_001'],
                    user_ids=[f'user_{i:05d}' for i in range(min(rate * self._dur_per, 5000))],
                    appid=appid,
                )

                # Dedup hit scenario: interleave duplicates so they hit within time limit
                if scenario == 'with_dedup':
                    dup_count = len(events) // 5
                    dups = events[:dup_count]
                    interleaved = []
                    for i in range(len(events)):
                        interleaved.append(events[i])
                        if i < dup_count:
                            interleaved.append(dups[i])
                    events = interleaved

                # Shared queue for concurrent consumers
                queue = asyncio.Queue(maxsize=len(events) + concurrency)
                for evt in events:
                    await queue.put(evt)
                for _ in range(concurrency):
                    await queue.put(None)  # sentinel

                end_time = time.time() + self._dur_per
                evt_count = 0
                evt_lock = asyncio.Lock()

                async def consumer(cid, end_time=end_time, queue=queue, scenario=scenario, rate=rate, evt_lock=evt_lock):
                    nonlocal evt_count
                    while time.time() < end_time and not self._stop_event.is_set():
                        try:
                            event = await asyncio.wait_for(queue.get(), timeout=1.0)
                        except TimeoutError:
                            continue
                        if event is None:
                            break

                        t0 = time.perf_counter()
                        try:
                            await self._handler._on_event(event)
                            self._metrics.counter('events_success', suite_label).inc()
                        except Exception:
                            self._metrics.counter('events_failed', suite_label).inc()
                        finally:
                            dt = time.perf_counter() - t0
                            self._metrics.counter('events_total', suite_label).inc()
                            self._metrics.record_latency(
                                'dispatch_latency_seconds',
                                dt,
                                {'suite': self.suite_name, 'scenario': scenario, 'rate': str(rate)},
                            )
                            async with evt_lock:
                                evt_count += 1

                consumers = [asyncio.create_task(consumer(i)) for i in range(concurrency)]
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*consumers, return_exceptions=True),
                        timeout=self._dur_per + 10,
                    )
                except TimeoutError:
                    for c in consumers:
                        c.cancel()

                stats = self._handler.stats()
                print(
                    f'      Events: {evt_count} | '
                    f'LogWrites: {stats["log_add"]} | '
                    f'TrackUser: {stats["track_user"]} | '
                    f'DedupHit: {stats["dedup_hit"]} | '
                    f'DedupMiss: {stats["dedup_miss"]} | '
                    f'EarlyReturn: {stats["early_return"]}'
                )

    def _configure_scenario(self, scenario, appid):
        """Configure the handler for a specific test scenario."""
        # Reset stats
        self._handler._push_web_log_count = 0
        self._handler._log_add_count = 0
        self._handler._track_user_count = 0
        self._handler._dedup_hit_count = 0
        self._handler._dedup_miss_count = 0
        self._handler._early_return_count = 0
        self._handler._dedup.clear()

        # Override dedup config via cfg (already initialized)
        from core.base.config import cfg

        if scenario == 'with_dedup':
            cfg._bot_setting_cache[(str(appid), 'dedup.enabled')] = True
        else:
            cfg._bot_setting_cache[(str(appid), 'dedup.enabled')] = False

    async def teardown(self) -> None:
        import shutil

        if self._tmpdir and os.path.exists(self._tmpdir):
            shutil.rmtree(self._tmpdir, ignore_errors=True)
