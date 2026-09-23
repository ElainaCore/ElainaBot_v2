"""健康巡检服务 — 周期性记录关键指标并在超阈值时告警。

不引入监控后端: 每分钟采样一次, 每 5 分钟打一条 INFO 摘要, 命中阈值
时打 WARNING。阈值来自内存审计报告 (主进程 RSS 与缓存/队列水位)。
"""

import asyncio
import contextlib

from core.base.logger import SERVICE, get_logger
from core.base.metrics import gauge, snapshot

log = get_logger(SERVICE, '健康巡检')

_INTERVAL = 60
_SUMMARY_EVERY = 5  # 每 5 次采样打一条摘要
_RSS_WARN_MB = 512
_RSS_CRIT_MB = 768
_BACKLOG_WARN = 1000


class HealthWatchService:
    """采样进程 RSS、追踪队列积压与业务计数器。"""

    def __init__(self, app=None, interval: float = _INTERVAL):
        self._app = app
        self._interval = interval
        self._task = None
        self._ticks = 0
        self._warned: set[str] = set()

    async def __call__(self):
        while True:
            await asyncio.sleep(self._interval)
            with contextlib.suppress(Exception):
                self.sample()

    def sample(self):
        """采样一次并输出 (可被测试直接调用)。"""
        self._ticks += 1
        rss_mb = self._rss_mb()
        backlog = self._track_backlog()
        if rss_mb is not None:
            gauge('process.rss_mb', round(rss_mb, 1))
        if backlog is not None:
            gauge('track.queue_backlog', backlog)

        if rss_mb is not None and rss_mb >= _RSS_CRIT_MB:
            self._warn_once('rss_crit', f'主进程 RSS {rss_mb:.0f} MB 超过高危阈值 {_RSS_CRIT_MB} MB')
        elif rss_mb is not None and rss_mb >= _RSS_WARN_MB:
            self._warn_once('rss_warn', f'主进程 RSS {rss_mb:.0f} MB 超过预警阈值 {_RSS_WARN_MB} MB')
        if backlog is not None and backlog >= _BACKLOG_WARN:
            self._warn_once('backlog', f'用户追踪队列积压 {backlog} 项')

        if self._ticks % _SUMMARY_EVERY == 0:
            data = snapshot()
            counters = data['counters']
            parts = [f'RSS {rss_mb:.0f} MB' if rss_mb is not None else 'RSS n/a']
            if backlog is not None:
                parts.append(f'追踪队列 {backlog}')
            for key in ('group_member_writes', 'group_member_removes',
                        'group_record_cache_hit', 'group_record_cache_miss',
                        'group_migration_rows', 'group_migration_failures'):
                if key in counters:
                    parts.append(f'{key}={int(counters[key])}')
            log.info('健康巡检: ' + ' | '.join(parts))

    def _warn_once(self, key, message):
        """同一类告警只打一次, 避免刷屏。"""
        if key in self._warned:
            return
        self._warned.add(key)
        log.warning(message)

    def _rss_mb(self):
        try:
            import psutil
            return psutil.Process().memory_info().rss / (1024 ** 2)
        except Exception:
            return None

    def _track_backlog(self):
        queue = getattr(self._app, '_track_queue', None)
        try:
            return queue.qsize() if queue is not None else None
        except Exception:
            return None

    def start(self):
        self._task = asyncio.create_task(self())

    def stop(self):
        if self._task:
            self._task.cancel()
            self._task = None
