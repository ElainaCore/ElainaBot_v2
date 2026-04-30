#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""SQLite 日志 + 数据服务 — 异步, 按类型独立文件, 按机器人/日期分目录"""

import os
import re
import json
import asyncio
import sqlite3
import shutil
import logging
from datetime import datetime, timedelta
from collections import defaultdict

from core.base.logger import get_logger, SERVICE, on_error
from core.storage.share import ShareMixin
from core.storage.wakeup import WakeupMixin

log = get_logger(SERVICE, "日志")


def _json_field(data, key, default=''):
    """将 dict/list 字段序列化为 JSON, 其它直接转 str"""
    v = data.get(key, default)
    return json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v)

# ==================== 日志类型定义 ====================

# 按日期分目录的类型
DAILY_TYPES = frozenset({'message', 'framework', 'error', 'lifecycle'})
# 不分日期的类型
STATIC_TYPES = frozenset({'data', 'dau', 'share', 'wakeup'})
ALL_TYPES = DAILY_TYPES | STATIC_TYPES

# 表结构 (类型 -> CREATE TABLE SQL)
_SCHEMAS = {
    'message': """
        CREATE TABLE IF NOT EXISTS log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            type TEXT DEFAULT '',
            message_id TEXT DEFAULT '',
            user_id TEXT DEFAULT '',
            group_id TEXT DEFAULT '',
            content TEXT DEFAULT '',
            raw_message TEXT DEFAULT '',
            plugin_name TEXT DEFAULT ''
        )
    """,
    'framework': """
        CREATE TABLE IF NOT EXISTS log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            content TEXT DEFAULT '',
            level TEXT DEFAULT 'INFO'
        )
    """,
    'error': """
        CREATE TABLE IF NOT EXISTS log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            appid TEXT DEFAULT '0000',
            module_type TEXT DEFAULT '',
            module_name TEXT DEFAULT '',
            content TEXT DEFAULT '',
            traceback TEXT DEFAULT '',
            context TEXT DEFAULT ''
        )
    """,
    'dau': """
        CREATE TABLE IF NOT EXISTS log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE NOT NULL,
            active_users INTEGER DEFAULT 0,
            active_groups INTEGER DEFAULT 0,
            total_messages INTEGER DEFAULT 0,
            private_messages INTEGER DEFAULT 0,
            group_join_count INTEGER DEFAULT 0,
            group_leave_count INTEGER DEFAULT 0,
            friend_add_count INTEGER DEFAULT 0,
            friend_remove_count INTEGER DEFAULT 0,
            message_stats_detail TEXT DEFAULT '',
            user_stats_detail TEXT DEFAULT '',
            command_stats_detail TEXT DEFAULT ''
        )
    """,
    'share': """
        CREATE TABLE IF NOT EXISTS log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            openid TEXT UNIQUE NOT NULL,
            referrals TEXT DEFAULT '{}',
            created_at TEXT,
            updated_at TEXT
        )
    """,
    'wakeup': """
        CREATE TABLE IF NOT EXISTS log (
            openid TEXT PRIMARY KEY,
            last_msg_date TEXT NOT NULL,
            wakeup_stage INTEGER DEFAULT 0,
            last_wakeup_date TEXT,
            updated_at TEXT
        )
    """,
    'lifecycle': """
        CREATE TABLE IF NOT EXISTS log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            type TEXT DEFAULT '',
            user_id TEXT DEFAULT '',
            group_id TEXT DEFAULT '',
            extra TEXT DEFAULT ''
        )
    """,
    'data': """
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            name TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS members (
            user_id TEXT PRIMARY KEY
        );
        CREATE TABLE IF NOT EXISTS groups_users (
            group_id TEXT PRIMARY KEY,
            users TEXT DEFAULT '[]'
        );
    """,
}

# INSERT SQL
_INSERTS = {
    'message': "INSERT INTO log (timestamp, type, message_id, user_id, group_id, content, raw_message, plugin_name) VALUES (?,?,?,?,?,?,?,?)",
    'framework': "INSERT INTO log (timestamp, content, level) VALUES (?,?,?)",
    'error': "INSERT INTO log (timestamp, appid, module_type, module_name, content, traceback, context) VALUES (?,?,?,?,?,?,?)",
    'lifecycle': "INSERT INTO log (timestamp, type, user_id, group_id, extra) VALUES (?,?,?,?,?)",
    'dau': """INSERT INTO log (date, active_users, active_groups, total_messages, private_messages,
              group_join_count, group_leave_count, friend_add_count, friend_remove_count,
              message_stats_detail, user_stats_detail, command_stats_detail)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(date) DO UPDATE SET
              active_users=MAX(active_users, excluded.active_users),
              active_groups=MAX(active_groups, excluded.active_groups),
              total_messages=MAX(total_messages, excluded.total_messages),
              private_messages=MAX(private_messages, excluded.private_messages),
              group_join_count=group_join_count+excluded.group_join_count,
              group_leave_count=group_leave_count+excluded.group_leave_count,
              friend_add_count=friend_add_count+excluded.friend_add_count,
              friend_remove_count=friend_remove_count+excluded.friend_remove_count""",
}


async def _shutdown_tasks(tasks, stop_event):
    """stop + cancel 所有后台任务"""
    stop_event.set()
    for t in tasks:
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass


def _close_all_conns(conns):
    """关闭并清空所有 SQLite 连接"""
    for conn in conns.values():
        try:
            conn.close()
        except Exception:
            pass
    conns.clear()


class LogService(ShareMixin, WakeupMixin):
    """SQLite 日志服务 (每个 bot 一个实例, 异步)"""

    _global_callbacks_registered = False
    _all_instances = []  # 所有活跃实例 (全局回调分发到每个实例)

    def __init__(self, base_dir, appid, wal_mode=True, insert_interval=2, batch_size=0, retention_days=5):
        self._base_dir = os.path.join(base_dir, str(appid))
        self._appid = str(appid)
        self._wal = wal_mode
        self._interval = insert_interval
        self._batch_size = batch_size
        self._retention_days = retention_days

        self._queues = {t: asyncio.Queue() for t in ALL_TYPES}
        self._data_write_queue = asyncio.Queue()  # data.db 通用写入队列 (sql, params)
        self._conns = {}          # {db_path: sqlite3.Connection}
        self._initialized = set() # 已建表的 db_path
        self._stop = asyncio.Event()
        self._tasks = []

    async def start(self):
        """启动日志服务"""
        os.makedirs(self._base_dir, exist_ok=True)
        # 注册到全局实例列表 (回调分发到所有 bot)
        LogService._all_instances.append(self)
        if not LogService._global_callbacks_registered:
            LogService._global_callbacks_registered = True
            on_error(LogService._global_error_dispatch)
        # 启动批量写入任务
        self._tasks.append(asyncio.create_task(self._periodic_flush()))
        if self._retention_days > 0:
            self._tasks.append(asyncio.create_task(self._periodic_cleanup()))
        log.info(f"[{self._appid}] 日志服务已启动 (SQLite, WAL={self._wal})")

    async def shutdown(self):
        """关闭日志服务, 刷写缓冲"""
        try:
            LogService._all_instances.remove(self)
        except ValueError:
            pass
        await _shutdown_tasks(self._tasks, self._stop)
        await self._flush_all()
        _close_all_conns(self._conns)
        log.info(f"[{self._appid}] 日志服务已关闭")

    async def add(self, log_type, data):
        """添加日志条目到队列"""
        if log_type not in ALL_TYPES:
            return False
        await self._queues[log_type].put(data)
        # DAU 立即刷写
        if log_type == 'dau':
            await self._flush_type('dau')
        return True

    def add_sync(self, log_type, data):
        """同步添加(从非异步上下文中调用)"""
        if log_type not in ALL_TYPES:
            return False
        try:
            self._queues[log_type].put_nowait(data)
            return True
        except asyncio.QueueFull:
            return False

    def query(self, log_type, sql, params=(), date=None):
        """同步查询, 返回 [dict]"""
        db_path = self._resolve_db_path(log_type, date)
        if not os.path.isfile(db_path):
            return []
        conn = self._get_conn(db_path, log_type)
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            log.warning(f"[{self._appid}] 查询失败 [{log_type}]: {e}")
            return []

    def query_data(self, sql, params=()):
        """同步查询 data.db (users/groups/members 表)"""
        return self.query('data', sql, params)

    def _resolve_db_path(self, log_type, date=None):
        if log_type in DAILY_TYPES:
            date = date or datetime.now().strftime('%Y-%m-%d')
            return os.path.join(self._base_dir, date, f"{log_type}.db")
        return os.path.join(self._base_dir, f"{log_type}.db")

    def _get_conn(self, db_path, log_type):
        if db_path in self._conns:
            return self._conns[db_path]

        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        conn = sqlite3.connect(db_path, check_same_thread=False)
        if self._wal:
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")

        # 建表 (data 类型含多条 SQL, 用 executescript)
        if db_path not in self._initialized:
            schema = _SCHEMAS.get(log_type)
            if schema:
                if log_type == 'data':
                    conn.executescript(schema)
                else:
                    conn.execute(schema)
                    conn.commit()
                # 自动迁移: 为旧表补齐缺失列
                self._migrate_missing_columns(conn, log_type)
            self._initialized.add(db_path)

        self._conns[db_path] = conn
        return conn

    @staticmethod
    def _migrate_missing_columns(conn, log_type):
        """为旧表补齐缺失列"""
        schema = _SCHEMAS.get(log_type)
        if not schema or log_type == 'data':
            return
        try:
            existing = {row[1] for row in conn.execute("PRAGMA table_info(log)").fetchall()}
        except Exception:
            return
        col_pattern = re.compile(r'^\s+(\w+)\s+(TEXT|INTEGER|REAL)(.*)$', re.MULTILINE)
        for m in col_pattern.finditer(schema):
            col_name = m.group(1)
            if col_name in existing or col_name == 'id':
                continue
            col_def = f"{m.group(2)}{m.group(3).rstrip().rstrip(',')}"
            try:
                conn.execute(f"ALTER TABLE log ADD COLUMN {col_name} {col_def}")
                conn.commit()
                log.info(f"自动迁移: log 表新增列 {col_name} ({log_type})")
            except Exception as e:
                log.warning(f"自动迁移列 {col_name} 失败: {e}")

    def _extract_row(self, log_type, data):
        """dict → INSERT 参数元组"""
        ts = data.get('timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        if log_type == 'message':
            return (ts, data.get('type', ''), data.get('message_id', ''),
                    data.get('user_id', ''), data.get('group_id', ''),
                    data.get('content', ''), data.get('raw_message', ''),
                    data.get('plugin_name', ''))
        if log_type == 'framework':
            return (ts, data.get('content', ''), data.get('level', 'INFO'))
        if log_type == 'error':
            return (ts, data.get('appid', '0000'),
                    data.get('module_type', ''), data.get('module_name', ''),
                    data.get('content', ''), data.get('traceback', ''),
                    _json_field(data, 'context', {}))
        if log_type == 'dau':
            return (data.get('date', datetime.now().strftime('%Y-%m-%d')),
                    data.get('active_users', 0), data.get('active_groups', 0),
                    data.get('total_messages', 0), data.get('private_messages', 0),
                    data.get('group_join_count', 0), data.get('group_leave_count', 0),
                    data.get('friend_add_count', 0), data.get('friend_remove_count', 0),
                    _json_field(data, 'message_stats_detail'),
                    _json_field(data, 'user_stats_detail'),
                    _json_field(data, 'command_stats_detail'))
        if log_type == 'lifecycle':
            extra = {k: v for k, v in data.items()
                     if k not in ('timestamp', 'type', 'user_id', 'group_id')}
            return (ts, data.get('type', ''), data.get('user_id', ''),
                    data.get('group_id', ''),
                    json.dumps(extra, ensure_ascii=False) if extra else '')
        return None

    async def _flush_type(self, log_type):
        q = self._queues[log_type]
        if q.empty():
            return
        batch = []
        limit = self._batch_size if self._batch_size > 0 else 10000
        while not q.empty() and len(batch) < limit:
            try:
                batch.append(q.get_nowait())
            except asyncio.QueueEmpty:
                break

        if not batch:
            return

        sql = _INSERTS.get(log_type)
        if not sql:
            return

        # 按日期分组 (DAILY_TYPES)
        if log_type in DAILY_TYPES:
            groups = defaultdict(list)
            for item in batch:
                ts = item.get('timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                date = ts[:10]  # YYYY-MM-DD
                row = self._extract_row(log_type, item)
                if row:
                    groups[date].append(row)
            for date, rows in groups.items():
                db_path = self._resolve_db_path(log_type, date)
                await self._write_batch(db_path, log_type, sql, rows)
        else:
            rows = [r for item in batch if (r := self._extract_row(log_type, item))]
            if rows:
                db_path = self._resolve_db_path(log_type)
                await self._write_batch(db_path, log_type, sql, rows)

    async def _write_batch(self, db_path, log_type, sql, rows):
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self._write_batch_sync, db_path, log_type, sql, rows)
        except Exception as e:
            log.error(f"[{self._appid}] 写入失败 [{log_type}]: {e}")

    def _write_batch_sync(self, db_path, log_type, sql, rows):
        conn = self._get_conn(db_path, log_type)
        try:
            conn.executemany(sql, rows)
            conn.commit()
        except sqlite3.Error as e:
            log.error(f"[{self._appid}] SQLite 错误 [{log_type}]: {e}")
            try:
                conn.rollback()
            except Exception:
                pass

    async def _flush_all(self):
        for t in ALL_TYPES:
            try:
                await self._flush_type(t)
            except Exception as e:
                log.warning(f"[{self._appid}] 刷写失败 [{t}]: {e}")
        # 刷写 data.db 通用写入队列
        await self._flush_data_queue()

    async def _periodic_flush(self):
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=max(self._interval, 1))
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                break
            await self._flush_all()

    async def _periodic_cleanup(self):
        while not self._stop.is_set():
            try:
                # 每天凌晨1点清理
                now = datetime.now()
                target = now.replace(hour=1, minute=0, second=0, microsecond=0)
                if now >= target:
                    target += timedelta(days=1)
                wait_secs = (target - now).total_seconds()
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=wait_secs)
                    break
                except asyncio.TimeoutError:
                    pass
                await self._cleanup_expired()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning(f"[{self._appid}] 清理异常: {e}")
                await asyncio.sleep(3600)

    async def _cleanup_expired(self):
        if self._retention_days <= 0:
            return
        cutoff = (datetime.now() - timedelta(days=self._retention_days)).strftime('%Y-%m-%d')
        loop = asyncio.get_running_loop()
        removed = 0
        try:
            for name in os.listdir(self._base_dir):
                path = os.path.join(self._base_dir, name)
                if not os.path.isdir(path):
                    continue
                # 日期目录格式: YYYY-MM-DD
                if len(name) == 10 and name < cutoff:
                    # 关闭该目录下的连接
                    for db_file in os.listdir(path):
                        db_path = os.path.join(path, db_file)
                        conn = self._conns.pop(db_path, None)
                        if conn:
                            try:
                                conn.close()
                            except Exception:
                                pass
                        self._initialized.discard(db_path)
                    await loop.run_in_executor(None, shutil.rmtree, path, True)
                    removed += 1
        except Exception as e:
            log.warning(f"[{self._appid}] 清理目录异常: {e}")
        if removed:
            log.info(f"[{self._appid}] 已清理 {removed} 个过期日志目录")

    def db_queue(self, sql, params=()):
        """写操作放入队列, 随下次 flush 批量执行"""
        try:
            self._data_write_queue.put_nowait((sql, params))
        except asyncio.QueueFull:
            pass

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
        conn = self._get_conn(self._resolve_db_path('data'), 'data')
        try:
            for sql, params in ops:
                conn.execute(sql, params)
            conn.commit()
        except Exception as e:
            log.error(f"[{self._appid}] data.db 批量写入失败: {e}")
            try:
                conn.rollback()
            except Exception:
                pass

    async def db_execute(self, sql, params=()):
        """执行写操作, 返回 lastrowid"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._db_execute_sync, sql, params)

    def _db_execute_sync(self, sql, params):
        conn = self._get_conn(self._resolve_db_path('data'), 'data')
        cursor = conn.execute(sql, params)
        conn.commit()
        return cursor.lastrowid

    async def db_execute_many(self, sql, params_list):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._db_execute_many_sync, sql, params_list)

    def _db_execute_many_sync(self, sql, params_list):
        conn = self._get_conn(self._resolve_db_path('data'), 'data')
        conn.executemany(sql, params_list)
        conn.commit()

    async def db_execute_script(self, script):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._db_execute_script_sync, script)

    def _db_execute_script_sync(self, script):
        conn = self._get_conn(self._resolve_db_path('data'), 'data')
        conn.executescript(script)

    async def db_fetch_one(self, sql, params=()):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._db_fetch_one_sync, sql, params)

    def _db_fetch_one_sync(self, sql, params):
        conn = self._get_conn(self._resolve_db_path('data'), 'data')
        conn.row_factory = sqlite3.Row
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    async def db_fetch_all(self, sql, params=()):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._db_fetch_all_sync, sql, params)

    def _db_fetch_all_sync(self, sql, params):
        conn = self._get_conn(self._resolve_db_path('data'), 'data')
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, params).fetchall()]

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
        sql = f"INSERT INTO {table} ({col_str}) VALUES ({placeholders})"
        if update_cols:
            update_str = ','.join(f"{c}=excluded.{c}" for c in update_cols)
            sql += f" ON CONFLICT({conflict_str}) DO UPDATE SET {update_str}"
        else:
            sql += f" ON CONFLICT({conflict_str}) DO NOTHING"
        return await self.db_execute(sql, values)

    async def db_table_exists(self, table_name):
        row = await self.db_fetch_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
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

class SharedLogService:
    """通用日志服务 — framework.db / error.db, 不分机器人"""

    _instance = None  # 单例, 供 LogService 回调桥接使用

    def __init__(self, base_dir, wal_mode=True, insert_interval=2, retention_days=5):
        self._base_dir = base_dir  # data/log
        self._wal = wal_mode
        self._interval = insert_interval
        self._retention_days = retention_days
        self._queues = {
            'framework': asyncio.Queue(),
            'error': asyncio.Queue(),
        }
        self._conns = {}
        self._initialized = set()
        self._stop = asyncio.Event()
        self._tasks = []

    async def start(self):
        os.makedirs(self._base_dir, exist_ok=True)
        SharedLogService._instance = self
        self._tasks.append(asyncio.create_task(self._periodic_flush()))
        if self._retention_days > 0:
            self._tasks.append(asyncio.create_task(self._periodic_cleanup()))
        log.info(f"[通用日志] 已启动 (目录: {self._base_dir})")

    async def shutdown(self):
        SharedLogService._instance = None
        await _shutdown_tasks(self._tasks, self._stop)
        await self._flush_all()
        _close_all_conns(self._conns)
        log.info("[通用日志] 已关闭")

    def add_sync(self, log_type, data):
        if log_type not in self._queues:
            return False
        try:
            self._queues[log_type].put_nowait(data)
            return True
        except asyncio.QueueFull:
            return False

    def query(self, log_type, sql, params=(), date=None):
        """同步查询"""
        db_path = self._resolve_db_path(log_type, date)
        if not os.path.isfile(db_path):
            return []
        conn = self._get_conn(db_path, log_type)
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            log.warning(f"[通用日志] 查询失败 [{log_type}]: {e}")
            return []

    def _resolve_db_path(self, log_type, date=None):
        date = date or datetime.now().strftime('%Y-%m-%d')
        return os.path.join(self._base_dir, date, f"{log_type}.db")

    def _get_conn(self, db_path, log_type):
        if db_path in self._conns:
            return self._conns[db_path]
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        conn = sqlite3.connect(db_path, check_same_thread=False)
        if self._wal:
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        if db_path not in self._initialized:
            schema = _SCHEMAS.get(log_type)
            if schema:
                conn.execute(schema)
                conn.commit()
                LogService._migrate_missing_columns(conn, log_type)
            self._initialized.add(db_path)
        self._conns[db_path] = conn
        return conn

    def _extract_row(self, log_type, data):
        ts = data.get('timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        if log_type == 'framework':
            return (ts, data.get('content', ''), data.get('level', 'INFO'))
        if log_type == 'error':
            return (ts, data.get('appid', '0000'),
                    data.get('module_type', ''), data.get('module_name', ''),
                    data.get('content', ''), data.get('traceback', ''),
                    _json_field(data, 'context', {}))
        return None

    async def _flush_type(self, log_type):
        q = self._queues[log_type]
        if q.empty():
            return
        batch = []
        while not q.empty() and len(batch) < 10000:
            try:
                batch.append(q.get_nowait())
            except asyncio.QueueEmpty:
                break
        if not batch:
            return
        sql = _INSERTS.get(log_type)
        if not sql:
            return
        # 按日期分组
        groups = defaultdict(list)
        for item in batch:
            ts = item.get('timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
            date = ts[:10]
            row = self._extract_row(log_type, item)
            if row:
                groups[date].append(row)
        for date, rows in groups.items():
            db_path = self._resolve_db_path(log_type, date)
            loop = asyncio.get_running_loop()
            try:
                await loop.run_in_executor(None, self._write_batch_sync, db_path, log_type, sql, rows)
            except Exception as e:
                log.error(f"[通用日志] 写入失败 [{log_type}]: {e}")

    def _write_batch_sync(self, db_path, log_type, sql, rows):
        conn = self._get_conn(db_path, log_type)
        try:
            conn.executemany(sql, rows)
            conn.commit()
        except sqlite3.Error as e:
            log.error(f"[通用日志] SQLite 错误 [{log_type}]: {e}")
            try:
                conn.rollback()
            except Exception:
                pass

    async def _flush_all(self):
        for t in self._queues:
            try:
                await self._flush_type(t)
            except Exception as e:
                log.warning(f"[通用日志] 刷写失败 [{t}]: {e}")

    async def _periodic_flush(self):
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=max(self._interval, 1))
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                break
            await self._flush_all()

    async def _periodic_cleanup(self):
        while not self._stop.is_set():
            try:
                now = datetime.now()
                target = now.replace(hour=1, minute=0, second=0, microsecond=0)
                if now >= target:
                    target += timedelta(days=1)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=(target - now).total_seconds())
                    break
                except asyncio.TimeoutError:
                    pass
                if self._retention_days <= 0:
                    continue
                cutoff = (datetime.now() - timedelta(days=self._retention_days)).strftime('%Y-%m-%d')
                removed = 0
                for name in os.listdir(self._base_dir):
                    path = os.path.join(self._base_dir, name)
                    if not (os.path.isdir(path) and len(name) == 10 and name < cutoff):
                        continue
                    for db_file in os.listdir(path):
                        db_path = os.path.join(path, db_file)
                        conn = self._conns.pop(db_path, None)
                        if conn:
                            try:
                                conn.close()
                            except Exception:
                                pass
                        self._initialized.discard(db_path)
                    shutil.rmtree(path, ignore_errors=True)
                    removed += 1
                if removed:
                    log.info(f"[通用日志] 已清理 {removed} 个过期目录")
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning(f"[通用日志] 清理异常: {e}")
                await asyncio.sleep(3600)
