#!/usr/bin/env python
"""每日定时扫描服务基类 — DAU / 累加统计共用调度与路径逻辑"""

import asyncio
import contextlib
import os
import re
from datetime import datetime, timedelta


class DailyScanService:
    """每日定时任务基类: 子类实现 _run_scheduled() 执行到点任务"""

    __slots__ = ('_log_dir', '_running', '_task', '_schedule_hour', '_schedule_minute')

    _logger = None  # 子类设置为自己的 log

    # 日期目录格式 (SharedLogService 的 framework/error 日志), 不是 bot appid
    _DATE_DIR_PATTERN = re.compile(r'^\d{4}-\d{2}-\d{2}$')

    def __init__(self, log_base_dir, schedule_hour, schedule_minute):
        self._log_dir = os.path.abspath(log_base_dir)
        self._schedule_hour = schedule_hour
        self._schedule_minute = schedule_minute
        self._running = False
        self._task = None

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None

    async def _run_scheduled(self):
        raise NotImplementedError

    async def _scheduler_loop(self):
        while self._running:
            try:
                next_run = self._next_run_time()
                wait = (next_run - datetime.now()).total_seconds()
                if wait > 0:
                    await asyncio.sleep(wait)
                if not self._running:
                    break
                await self._run_scheduled()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.warning(f'调度异常: {e}')
                await asyncio.sleep(60)

    def _next_run_time(self):
        now = datetime.now()
        today_run = now.replace(
            hour=self._schedule_hour,
            minute=self._schedule_minute,
            second=0,
            microsecond=0,
        )
        return today_run if today_run > now else today_run + timedelta(days=1)

    def list_appids(self):
        """列出所有有日志记录的 appid (排除 SharedLogService 的日期目录)"""
        if not os.path.isdir(self._log_dir):
            return []
        return [
            name
            for name in os.listdir(self._log_dir)
            if os.path.isdir(os.path.join(self._log_dir, name)) and not self._DATE_DIR_PATTERN.match(name)
        ]

    def _message_db_path(self, appid, date_str):
        return os.path.join(self._log_dir, appid, date_str, 'message.db')
