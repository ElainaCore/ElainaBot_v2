#!/usr/bin/env python
"""SQLite 日志 + 数据服务 — LogService / SharedLogService"""

import asyncio
import contextlib
import json
import os
import sqlite3
from datetime import datetime

from core.base.logger import SERVICE, get_logger, on_error
from core.storage._base import _BaseLogService
from core.storage._schema import (
    _QUEUE_MAXSIZE,
    ALL_TYPES,
    _json_field,
)
from core.storage.share import ShareMixin
from core.storage.wakeup import WakeupMixin

log = get_logger(SERVICE, '日志')


class LogService(_BaseLogService, ShareMixin, WakeupMixin):
    """SQLite 日志服务 (每个 bot 一个实例, 异步)"""

    _global_callbacks_registered = False
    _all_instances: list['LogService'] = []  # 所有活跃实例 (全局回调分发到每个实例)

    def __init__(
        self,
        base_dir,
        appid,
        wal_mode=True,
        insert_interval=2,
        batch_size=0,
        retention_days=5,
    ):
        super().__init__(
            os.path.join(base_dir, str(appid)),
            wal_mode,
            insert_interval,
            batch_size,
            retention_days,
            ALL_TYPES,
        )
        self._appid = str(appid)
        self._log_tag = self._appid
        self._data_write_queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)

    async def start(self):
        """启动日志服务"""
        LogService._all_instances.append(self)
        if not LogService._global_callbacks_registered:
            LogService._global_callbacks_registered = True
            on_error(LogService._global_error_dispatch)
        await self._start_tasks()

    async def shutdown(self):
        """关闭日志服务, 刷写缓冲"""
        with contextlib.suppress(ValueError):
            LogService._all_instances.remove(self)
        await self._shutdown_base()
        log.info(f'[{self._appid}] 日志服务已关闭')

    async def add(self, log_type, data):
        """添加日志条目到队列 (队列满时丢弃, 不阻塞)"""
        if log_type not in ALL_TYPES:
            return False
        try:
            self._queues[log_type].put_nowait(data)
        except asyncio.QueueFull:
            return False
        # DAU 立即刷写 (异步后台, 不阻塞调用方)
        if log_type == 'dau':
            asyncio.create_task(self._flush_type('dau'))
        return True

    def query_data(self, sql, params=()):
        """同步查询 data.db (users/groups/members 表)"""
        return self.query('data', sql, params)

    def _extract_row(self, log_type, data):
        """dict → INSERT 参数元组"""
        ts = data.get('timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        if log_type == 'message':
            def _s(v): return str(v) if not isinstance(v, str) else v
            return (
                ts,
                _s(data.get('type', '')),
                _s(data.get('message_id', '')),
                _s(data.get('user_id', '')),
                _s(data.get('group_id', '')),
                _s(data.get('content', '')),
                _s(data.get('raw_message', '')),
                _s(data.get('plugin_name', '')),
                _s(data.get('direction', '')),
            )
        common = self._extract_common_row(log_type, data, ts)
        if common:
            return common
        if log_type == 'dau':
            return (
                data.get('date', datetime.now().strftime('%Y-%m-%d')),
                data.get('active_users', 0),
                data.get('active_groups', 0),
                data.get('total_messages', 0),
                data.get('private_messages', 0),
                data.get('group_join_count', 0),
                data.get('group_leave_count', 0),
                data.get('friend_add_count', 0),
                data.get('friend_remove_count', 0),
                _json_field(data, 'message_stats_detail'),
                _json_field(data, 'user_stats_detail'),
                _json_field(data, 'command_stats_detail'),
            )
        if log_type == 'lifecycle':
            extra = {k: v for k, v in data.items() if k not in ('timestamp', 'type', 'user_id', 'group_id')}
            return (
                ts,
                data.get('type', ''),
                data.get('user_id', ''),
                data.get('group_id', ''),
                json.dumps(extra, ensure_ascii=False) if extra else '',
            )
        return None

    async def _flush_all(self):
        await super()._flush_all()
        await self._flush_data_queue()

    def db_queue(self, sql, params=()):
        """写操作放入队列, 随下次 flush 批量执行"""
        with contextlib.suppress(asyncio.QueueFull):
            self._data_write_queue.put_nowait((sql, params))

    async def _flush_data_queue(self):
        q = self._data_write_queue
        if q.empty():
            return
        ops = []
        while not q.empty():
            try:
                ops.append(q.get_nowait())
            except asyncio.QueueEmpty:
                break
        if ops:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._flush_data_queue_sync, ops)

    def _flush_data_queue_sync(self, ops):
        conn = self._data_conn
        lock = self._data_lock
        try:
            with lock:
                for sql, params in ops:
                    conn.execute(sql, params)
                conn.commit()
        except Exception as e:
            log.error(f'[{self._appid}] data.db 批量写入失败: {e}')
            with contextlib.suppress(Exception):
                conn.rollback()

    @property
    def _data_conn(self):
        return self._get_conn(self._resolve_db_path('data'), 'data')

    @property
    def _data_lock(self):
        return self._conn_locks.get(self._resolve_db_path('data'))

    async def db_execute(self, sql, params=()):
        """执行写操作, 返回 lastrowid"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._db_execute_sync, sql, params)

    def _db_execute_sync(self, sql, params):
        with self._data_lock:
            cursor = self._data_conn.execute(sql, params)
            self._data_conn.commit()
            return cursor.lastrowid

    async def db_execute_many(self, sql, params_list):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._db_execute_many_sync, sql, params_list)

    def _db_execute_many_sync(self, sql, params_list):
        with self._data_lock:
            self._data_conn.executemany(sql, params_list)
            self._data_conn.commit()

    async def db_execute_script(self, script):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._db_execute_script_sync, script)

    def _db_execute_script_sync(self, script):
        with self._data_lock:
            self._data_conn.executescript(script)

    async def db_fetch_one(self, sql, params=()):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._db_fetch_one_sync, sql, params)

    def _db_fetch_one_sync(self, sql, params):
        conn = self._data_conn
        with self._data_lock:
            conn.row_factory = sqlite3.Row
            row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    async def db_fetch_all(self, sql, params=()):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._db_fetch_all_sync, sql, params)

    def _db_fetch_all_sync(self, sql, params):
        conn = self._data_conn
        with self._data_lock:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    async def db_fetch_value(self, sql, params=(), default=None):
        """查询单个值"""
        row = await self.db_fetch_one(sql, params)
        return list(row.values())[0] if row else default

    async def db_upsert(self, table, data, conflict_columns):
        """INSERT OR UPDATE"""
        columns = list(data.keys())
        values = list(data.values())
        placeholders = ','.join(['?'] * len(columns))
        col_str = ','.join(columns)
        update_cols = [c for c in columns if c not in conflict_columns]
        conflict_str = ','.join(conflict_columns)
        sql = f'INSERT INTO {table} ({col_str}) VALUES ({placeholders})'
        if update_cols:
            update_str = ','.join(f'{c}=excluded.{c}' for c in update_cols)
            sql += f' ON CONFLICT({conflict_str}) DO UPDATE SET {update_str}'
        else:
            sql += f' ON CONFLICT({conflict_str}) DO NOTHING'
        return await self.db_execute(sql, values)

    async def db_table_exists(self, table_name):
        row = await self.db_fetch_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        )
        return row is not None

    @staticmethod
    def _global_error_dispatch(error_data):
        if SharedLogService._instance:
            SharedLogService._instance.add_sync('error', error_data)

    @staticmethod
    def _global_framework_dispatch(log_data):
        if SharedLogService._instance:
            SharedLogService._instance.add_sync('framework', log_data)


# ==================== 通用日志服务 (框架+错误, 不分机器人) ====================


class SharedLogService(_BaseLogService):
    """通用日志服务 — framework.db / error.db, 不分机器人"""

    _instance = None  # 单例, 供 LogService 回调桥接使用

    def __init__(self, base_dir, wal_mode=True, insert_interval=2, retention_days=5):
        super().__init__(
            base_dir,
            wal_mode,
            insert_interval,
            0,
            retention_days,
            ('framework', 'error'),
        )
        self._log_tag = '通用日志'

    async def start(self):
        SharedLogService._instance = self
        await self._start_tasks()

    async def shutdown(self):
        SharedLogService._instance = None
        await self._shutdown_base()
        log.info('[通用日志] 已关闭')

    def _extract_row(self, log_type, data):
        ts = data.get('timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        return self._extract_common_row(log_type, data, ts)
