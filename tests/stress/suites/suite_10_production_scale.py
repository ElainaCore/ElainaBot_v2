"""Suite 10: Production-Scale Receive Stress Test

模拟生产稳态规模驱动真实 core/bot/event.py:EventHandlerMixin._on_event 热路径:
    - 20w 群 (group id 空间)
    - 150w 用户 (user id 空间)
    - 每天 100w 消息 (≈ 11.6 msg/s 日均, 50 msg/s 峰值)
    - 验证 50 msg/s 持续处理无积压

与 suite_09 不同, 本 suite:
    1. 使用真实 EventHandlerMixin (而非镜像副本), 保证测的就是生产代码;
    2. 使用 ScaleLogService 真实保留群成员 JSON / 用户集合, 暴露 O(群规模)
       的序列化开销与 _group_users_cache 命中率随群数量的退化;
    3. 跑两个相位:
       - sustained: 以 50 msg/s 持续灌入, 测尾延迟 / 内存 / 有无积压;
       - ceiling:   尽力灌入, 测单进程吞吐上限。

可通过 overrides 调整规模 (CI 用小规模, 生产基线用全量)。
"""

import asyncio
import random
import re
import time

from tests.stress.config import StressTestConfig
from tests.stress.mocks.event_factory import EventFactory
from tests.stress.mocks.message_sender import MockMessageSender
from tests.stress.mocks.scale_store import (
    ScaleLogService,
    _StubApp,
    estimate_group_members_json,
)
from tests.stress.suites.base import BaseStressTest


class _ScaleBot:
    """轻量 BotInstance 替身, 携带 ScaleLogService 与 sender。"""

    def __init__(self, appid, config):
        self.appid = appid
        self.name = f'Bot_{appid}'
        self.robot_qq = appid
        self.owner_ids = []
        self.secret = 'test'
        self.bot_id = ''
        self.avatar_url = ''
        self.log_service = ScaleLogService(appid)
        self.sender = MockMessageSender(appid, config)
        self.sender.bind_instance(log_service=self.log_service, bot_name=self.name, bot_qq=self.robot_qq)


def _make_harness(bots, plugin_manager):
    """构造承载真实 EventHandlerMixin 的最小宿主 (继承以获得 _LIFECYCLE_HANDLERS 等)。"""
    from core.bot.event import EventHandlerMixin

    class _ScaleHarness(EventHandlerMixin):
        def __init__(self, bots, plugin_manager):
            self._bots = bots
            self._plugin_manager = plugin_manager
            self._web_log_count = 0
            self._init_event_state()

        def _push_web_log(self, log_type, entry):
            self._web_log_count += 1

    return _ScaleHarness(bots, plugin_manager)


class ProductionScaleTest(BaseStressTest):
    """生产稳态规模端到端压测。"""

    @property
    def suite_name(self):
        return 'production_scale'

    async def setup(self, config: StressTestConfig) -> None:
        ov = config.overrides
        self._groups = int(ov.get('groups', 200_000))
        self._users = int(ov.get('users', 1_500_000))
        self._daily_messages = int(ov.get('daily_messages', 1_000_000))
        self._peak_rate = int(ov.get('peak_rate', 50))
        self._sustained_seconds = int(ov.get('sustained_seconds', 30))
        self._ceiling_seconds = int(ov.get('ceiling_seconds', 15))
        self._handler_count = int(ov.get('handler_count', 50))
        self._dedup = bool(ov.get('dedup', True))
        self._workers = int(ov.get('workers', 32))
        # 预热: 灌入稳态群成员/用户人口 (默认占总量一部分, 兼顾内存)
        self._prewarm_groups = int(ov.get('prewarm_groups', min(self._groups, 50_000)))
        self._prewarm_users = int(ov.get('prewarm_users', min(self._users, 300_000)))
        self._prewarm_group_size = int(ov.get('prewarm_group_size', 30))
        # 热点流量模型: hot_traffic_share 比例的消息落在 hot_group_fraction 的群上
        # (贴近真实: 少量活跃群承载大部分消息)。配 LRU 群缓存以体现命中率收益。
        self._hot_group_fraction = float(ov.get('hot_group_fraction', 0.025))
        self._hot_traffic_share = float(ov.get('hot_traffic_share', 0.8))
        self._hot_groups = max(1, int(self._groups * self._hot_group_fraction))
        self._rng = random.Random(20240628)
        # SLO 判定阈值 (用作回归护栏)
        self._sustained_lag_ms_threshold = float(ov.get('sustained_lag_ms_threshold', 2000.0 / max(self._peak_rate, 1)))
        self._task_peak_threshold = int(ov.get('task_peak_threshold', 50_000))
        # 相位指标 (run_phase 填充)
        self._sustained_max_lag_ms = 0.0
        self._sustained_count = 0
        self._sustained_track_drops = 0
        self._ceiling_count = 0

        from core.base.config import cfg

        self._cfg = cfg
        self._appid = '102000001'
        cfg._bot_setting_cache[(self._appid, 'dedup.enabled')] = self._dedup
        # 开启全量群消息处理, 使非@消息也走完整链路 (更贴近真实负载)
        cfg._bot_setting_cache[(self._appid, 'non_at_message.enabled')] = True

        from core.plugin.manager import PluginManager

        self._pm = PluginManager(plugins_dir='/tmp/_scale_pm', bot_appid=self._appid)
        self._setup_handlers(self._handler_count)

        self._bot = _ScaleBot(self._appid, config)
        self._bots = {self._appid: self._bot}

        # 让 dispatch 的 _get_log_service 直接命中本地 bot, 规避 get_app() 噪声
        from core.plugin._dispatch import _DispatchMixin

        _DispatchMixin._cached_app = _StubApp(self._bots)

        self._harness = _make_harness(self._bots, self._pm)

        self._prewarm()

    def _setup_handlers(self, count):
        handlers = []
        for i in range(count):

            async def handler_fn(event, match, _idx=i):
                await asyncio.sleep(0)  # 让出事件循环, 模拟极轻处理

            pat = rf'^/cmd_{i}\b' if i < count - 1 else r'(.|\n)*'
            handlers.append({
                'name': f'scale_h_{i:04d}',
                'func': handler_fn,
                'pattern': pat,
                'compiled': re.compile(pat),
                'priority': 100 - i,
                'is_coro': True,
                'event_types': [],
                'group_only': False,
                'direct_only': False,
                'channel_only': False,
                'owner_only': False,
                'ignore_at_check': True,
                '_allowed_bots': None,
            })
        self._pm._all_handlers = handlers
        self._pm._all_interceptors = []
        self._pm._plugin_bots = {}
        self._pm._apply_bot_bindings()
        self._pm._build_dispatch_index()

    def _prewarm(self):
        # 解析后的 user_id 形如 'mem_user_<i>' (见 EventFactory.group_at_message)
        ls = self._bot.log_service
        # 灌入已知用户集合 (users 表)
        ls.prewarm_users(f'mem_user_{i}' for i in range(self._prewarm_users))
        # 灌入部分群成员 JSON blob (模拟稳态群规模)
        blob = estimate_group_members_json(self._prewarm_group_size)
        for g in range(self._prewarm_groups):
            ls.prewarm_group(f'group_{g}', blob)
        # 预热 harness 的 _known_users 缓存 (避免冷启动全部走 DB fetch)
        now = time.time()
        ku = self._harness._known_users
        for i in range(min(self._prewarm_users, 500_000)):
            ku[f'mem_user_{i}'] = now + 3600

    def _make_event(self, i):
        # 热点倾斜: 多数消息落在少量热点群; 其余均匀散布到全量群空间
        g = self._rng.randrange(self._hot_groups) if self._rng.random() < self._hot_traffic_share else self._rng.randrange(self._groups)
        uid = f'user_{self._rng.randrange(self._users)}'
        return EventFactory.group_at_message(f'/cmd_0 hello_{i}', uid, f'group_{g}', self._appid)

    async def run_phase(self, config: StressTestConfig) -> None:
        await self._run_sustained()
        if not self._stop_event.is_set():
            await self._run_ceiling()
        self._print_store_stats()

    async def _process(self, event, scenario):
        t0 = time.perf_counter()
        try:
            await self._harness._on_event(event)
            self._metrics.counter('events_success', {'suite': self.suite_name}).inc()
        except Exception:
            self._metrics.counter('events_failed', {'suite': self.suite_name}).inc()
        finally:
            dt = time.perf_counter() - t0
            self._metrics.counter('events_total', {'suite': self.suite_name}).inc()
            self._metrics.record_latency(
                'dispatch_latency_seconds', dt,
                {'suite': self.suite_name, 'scenario': scenario},
            )
        return dt

    async def _run_sustained(self):
        """以 peak_rate msg/s 持续灌入, 监控积压 (调度延迟)。"""
        rate = self._peak_rate
        interval = 1.0 / rate
        end = time.time() + self._sustained_seconds
        print(f'    [sustained] {rate} msg/s for {self._sustained_seconds}s '
              f'(groups={self._groups} users={self._users}) ...')
        i = 0
        max_lag = 0.0
        next_t = time.perf_counter()
        inflight = set()
        while time.time() < end and not self._stop_event.is_set():
            now = time.perf_counter()
            lag = now - next_t
            if lag > max_lag:
                max_lag = lag
            event = self._make_event(i)
            i += 1
            task = asyncio.create_task(self._process(event, 'sustained'))
            inflight.add(task)
            task.add_done_callback(inflight.discard)
            next_t += interval
            sleep_for = next_t - time.perf_counter()
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
        if inflight:
            await asyncio.gather(*list(inflight), return_exceptions=True)
        self._sustained_count = i
        self._sustained_max_lag_ms = max_lag * 1000
        # 记录持续相位结束时的追踪丢弃数 (50 msg/s 下应为 0)
        self._sustained_track_drops = getattr(self._harness, '_track_drop_count', 0)
        backlog = 'NONE' if max_lag < interval else f'{max_lag * 1000:.1f}ms'
        print(f'      processed={i} max_schedule_lag={max_lag * 1000:.2f}ms backlog={backlog} '
              f'sustained_track_drops={self._sustained_track_drops}')

    async def _run_ceiling(self):
        """有界并发尽力灌入, 测单进程吞吐上限。"""
        workers = self._workers
        end = time.time() + self._ceiling_seconds
        print(f'    [ceiling] best-effort with {workers} workers for {self._ceiling_seconds}s ...')
        counter = {'i': 0}

        async def worker():
            while time.time() < end and not self._stop_event.is_set():
                i = counter['i']
                counter['i'] += 1
                await self._process(self._make_event(i), 'ceiling')

        await asyncio.gather(*[asyncio.create_task(worker()) for _ in range(workers)])
        dur = max(self._ceiling_seconds, 0.001)
        self._ceiling_count = counter['i']
        print(f'      processed={counter["i"]} throughput≈{counter["i"] / dur:.0f} msg/s')

    def _print_store_stats(self):
        s = self._bot.log_service.stats()
        print(f'    [store] known_users={s["known_users"]} known_groups={s["known_groups"]} '
              f'group_selects={s["group_select_count"]} group_writes={s["group_write_count"]} '
              f'user_fetches={s["user_fetch_count"]}')
        print(f'    [cache] group_cache={len(self._harness._group_users_cache)} '
              f'known_users_cache={len(self._harness._known_users)} '
              f'dirty_groups={len(self._harness._dirty_groups)} '
              f'track_dropped={getattr(self._harness, "_track_drop_count", 0)} '
              f'track_queue={self._harness._track_queue.qsize() if self._harness._track_queue else 0}')

    def _compute_verdict(self):
        """生产稳态 SLO 判定 (覆盖基类基于内存增量的判定)。

        本 suite 的内存增量主要来自合法的稳态人口预热 + ceiling 洪峰下的人口增长,
        故不以内存增量定生死, 而是检验真正的 SLO 与回归护栏:
          - 持续 50 msg/s 必须无积压 (调度延迟 < 阈值);
          - 持续相位不得因追踪队列溢出而丢弃 (说明 50/s 下处理不过来);
          - 任务峰值必须有界 (回归护栏: 防止 fire-and-forget create_task 无界堆积复发)。
        """
        r = self._result
        reasons = []
        if self._sustained_max_lag_ms > self._sustained_lag_ms_threshold:
            reasons.append(f'sustained backlog {self._sustained_max_lag_ms:.0f}ms > {self._sustained_lag_ms_threshold:.0f}ms')
        if self._sustained_track_drops > 0:
            reasons.append(f'sustained dropped {self._sustained_track_drops} track events')
        if r.task_count_peak > self._task_peak_threshold:
            reasons.append(f'task peak {r.task_count_peak} > {self._task_peak_threshold} (unbounded task growth)')
        if r.error_rate >= (getattr(self._config, 'fail_on_error_rate', 0.01) or 0.01):
            reasons.append(f'error rate {r.error_rate:.1%}')

        r.custom_metrics.update({
            'sustained_max_lag_ms': round(self._sustained_max_lag_ms, 2),
            'sustained_count': self._sustained_count,
            'sustained_track_drops': self._sustained_track_drops,
            'ceiling_count': self._ceiling_count,
            'task_peak': r.task_count_peak,
            'verdict_reasons': reasons,
        })
        r.verdict = 'FAIL' if reasons else 'PASS'
        if reasons:
            print(f'    [verdict] FAIL: {"; ".join(reasons)}')
        else:
            print('    [verdict] PASS: 50 msg/s 无积压, 任务峰值有界')

    async def teardown(self) -> None:
        # 取消后台 flush 任务
        ft = getattr(self._harness, '_flush_task', None)
        if ft and not ft.done():
            ft.cancel()
        # 取消用户追踪后台 worker
        for w in getattr(self._harness, '_track_workers', []):
            if not w.done():
                w.cancel()
        from core.plugin._dispatch import _DispatchMixin
        _DispatchMixin._cached_app = None
