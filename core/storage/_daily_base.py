#!/usr/bin/env python
"""每日定时扫描服务基类 — DAU / 累加统计共用调度与路径逻辑"""

import asyncio
import contextlib
import json
import os
import re
import sqlite3
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
            with contextlib.suppress(asyncio.CancelledError):
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
                raise
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

    @staticmethod
    def _open_ro(path):
        """只读打开 SQLite, 返回 conn 或 None"""
        if not os.path.isfile(path):
            return None
        try:
            conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=10)
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.Error:
            return None

    @staticmethod
    def _decode_json_fields(d, keys):
        """就地解码 dict 中的 JSON 字符串字段, 返回原 dict"""
        for k in keys:
            v = d.get(k)
            if isinstance(v, str) and v:
                with contextlib.suppress(Exception):
                    d[k] = json.loads(v)
        return d

    async def _run_date_all(self, date_str, per_appid):
        """对所有 appid 执行指定日期的统计任务, 返回 {appid: bool}"""
        results = {}
        appids = self.list_appids()
        self._logger.info(f'开始统计 [{date_str}], {len(appids)} 个机器人')
        for appid in appids:
            try:
                results[appid] = bool(await per_appid(appid, date_str))
            except Exception as e:
                self._logger.warning(f'[{appid}] 统计失败: {e}')
                results[appid] = False
        self._logger.info(f'统计完成 [{date_str}]: {sum(results.values())}/{len(results)} 成功')
        return results
