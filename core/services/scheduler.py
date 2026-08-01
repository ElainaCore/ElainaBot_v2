"""定时重启调度服务"""

import asyncio
import logging
from datetime import datetime, timedelta

from core.base.config import cfg

log = logging.getLogger('ElainaBot.scheduler')


class RestartScheduler:
    """按配置定时触发重启 (daily / interval 模式)"""

    def __init__(self, on_restart=None):
        self._on_restart = on_restart
        self._task = None

    async def __call__(self):
        """可直接作为 asyncio.create_task() 的协程"""
        restart_cfg = cfg.get('settings', 'restart') or {}
        if not restart_cfg.get('enabled', False):
            return

        mode = restart_cfg.get('mode', 'daily')
        now = datetime.now()

        if mode == 'daily':
            time_str = str(restart_cfg.get('daily_time', '04:00'))
            parts = time_str.split(':')
            h, m = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
            target = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if now >= target:
                target += timedelta(days=1)
            wait_secs = (target - now).total_seconds()
        elif mode == 'interval':
            hours = float(restart_cfg.get('interval_hours', 24))
            wait_secs = hours * 3600
            target = now + timedelta(seconds=wait_secs)
        else:
            return

        log.info(f'定时重启: {target.strftime("%m-%d %H:%M")}')

        await asyncio.sleep(wait_secs)

        log.info('定时重启触发')
        if self._on_restart:
            self._on_restart()

    def start(self):
        self._task = asyncio.create_task(self())

    def stop(self):
        if self._task:
            self._task.cancel()
            self._task = None
