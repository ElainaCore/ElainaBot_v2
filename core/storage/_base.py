"""日志服务公共基类 — 连接管理、查询、批量写入、定时刷写/清理"""

import asyncio
import contextlib
import os
import re
import shutil
import sqlite3
import threading
from collections import defaultdict
from datetime import datetime, timedelta

from core.base.logger import SERVICE, get_logger
from core.storage._schema import (
    _INSERTS,
    _QUEUE_MAXSIZE,
    _SCHEMAS,
    DAILY_TYPES,
    _ensure_indexes,
    _json_field,
    _migrate_data_tables,
    _migrate_missing_columns,
)

log = get_logger(SERVICE, '日志')


async def _shutdown_tasks(tasks, stop_event):
    """stop + cancel 所有后台任务"""
    stop_event.set()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def _close_all_conns(conns):
    """WAL checkpoint + 关闭并清空所有 SQLite 连接"""
    for conn in conns.values():
        with contextlib.suppress(Exception):
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        with contextlib.suppress(Exception):
            conn.close()
    conns.clear()


class _BaseLogService:
    """日志服务公共基类"""

    def __init__(
        self,
        base_dir,
        wal_mode,
        insert_interval,
        batch_size,
        retention_days,
        queue_types,
    ):
        self._base_dir = base_dir
        self._wal = wal_mode
        self._interval = insert_interval
        self._batch_size = batch_size
        self._retention_days = retention_days
        self._queues = {t: asyncio.Queue(maxsize=_QUEUE_MAXSIZE) for t in queue_types}
        self._conns = {}
        self._conn_locks = {}
        self._initialized = set()
        self._init_lock = threading.Lock()  # 保护连接池初始化, 避免多线程竞态
        self._stop = asyncio.Event()
        self._tasks = []
        self._log_tag = ''  # 子类设置

    def _resolve_db_path(self, log_type, date=None):
        if log_type in DAILY_TYPES:
            date = date or datetime.now().strftime('%Y-%m-%d')
            return os.path.join(self._base_dir, date, f'{log_type}.db')
        return os.path.join(self._base_dir, f'{log_type}.db')

    def _get_conn(self, db_path, log_type):
        # 快路径: 已初始化的连接, 无锁直接返回 (dict 读取在 CPython 下原子)
        conn = self._conns.get(db_path)
        if conn is not None:
            return conn
        # 慢路径: 加锁创建, 避免多个 executor 线程并发创建同一连接造成泄漏
        with self._init_lock:
            conn = self._conns.get(db_path)
            if conn is not None:
                return conn
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
            conn = sqlite3.connect(db_path, check_same_thread=False)
            if self._wal:
                conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA synchronous=NORMAL')
            if db_path not in self._initialized:
                schema = _SCHEMAS.get(log_type)
                if schema:
                    if log_type == 'data':
                        conn.executescript(schema)
                        _migrate_data_tables(conn)
                    else:
                        conn.execute(schema)
                        conn.commit()
                    _migrate_missing_columns(conn, log_type)
                    _ensure_indexes(conn, log_type)
                self._initialized.add(db_path)
            self._conn_locks.setdefault(db_path, threading.Lock())
            self._conns[db_path] = conn  # 最后赋值, 确保读端看到的连接已完全初始化
            return conn

    def query(self, log_type, sql, params=(), date=None):
        """同步查询, 返回 [dict]"""
        db_path = self._resolve_db_path(log_type, date)
        if not os.path.isfile(db_path):
            return []
        conn = self._get_conn(db_path, log_type)
        lock = self._conn_locks.get(db_path)
        try:
            with lock:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            log.warning(f'[{self._log_tag}] 查询失败 [{log_type}]: {e}')
            return []

    @staticmethod
    def _extract_common_row(log_type, data, ts):
        """framework / error 通用提取 (子类共享)"""
        if log_type == 'framework':
            return (ts, data.get('content', ''), data.get('level', 'INFO'))
        if log_type == 'error':
            return (
                ts,
                data.get('appid', '0000'),
                data.get('module_type', ''),
                data.get('module_name', ''),
                data.get('content', ''),
                data.get('traceback', ''),
                _json_field(data, 'context', {}),
            )
        return None

    def add_sync(self, log_type, data):
        """同步添加(从非异步上下文中调用)"""
        if log_type not in self._queues:
            return False
        try:
            self._queues[log_type].put_nowait(data)
            return True
        except asyncio.QueueFull:
            return False

    def _extract_row(self, log_type, data):
        """dict → INSERT 参数元组 (子类实现)"""
        return None

    def _write_batch_sync(self, db_path, log_type, sql, rows):
        conn = self._get_conn(db_path, log_type)
        lock = self._conn_locks.get(db_path)
        try:
            with lock:
                conn.executemany(sql, rows)
                conn.commit()
        except sqlite3.Error as e:
            log.error(f'[{self._log_tag}] SQLite 错误 [{log_type}]: {e}')
            with contextlib.suppress(Exception):
                conn.rollback()

    async def _write_batch(self, db_path, log_type, sql, rows):
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self._write_batch_sync, db_path, log_type, sql, rows)
        except Exception as e:
            log.error(f'[{self._log_tag}] 写入失败 [{log_type}]: {e}')

    async def _flush_type(self, log_type):
        q = self._queues[log_type]
        if q.empty():
            return
        batch, limit = [], self._batch_size or 10000
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
        if log_type in DAILY_TYPES:
            groups = defaultdict(list)
            for item in batch:
                ts = item.get('timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                row = self._extract_row(log_type, item)
                if row:
                    groups[ts[:10]].append(row)
            for date, rows in groups.items():
                await self._write_batch(self._resolve_db_path(log_type, date), log_type, sql, rows)
        else:
            rows = [r for item in batch if (r := self._extract_row(log_type, item))]
            if rows:
                await self._write_batch(self._resolve_db_path(log_type), log_type, sql, rows)

    async def _flush_all(self):
        for t in self._queues:
            try:
                await self._flush_type(t)
            except Exception as e:
                log.warning(f'[{self._log_tag}] 刷写失败 [{t}]: {e}')

    async def _periodic_flush(self):
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=max(self._interval, 1))
            except TimeoutError:
                pass
            except asyncio.CancelledError:
                break
            await self._flush_all()

    async def _cleanup_expired(self):
        if self._retention_days <= 0:
            return
        cutoff = (datetime.now() - timedelta(days=self._retention_days)).strftime('%Y-%m-%d')
        loop = asyncio.get_running_loop()
        removed = 0
        try:
            for name in os.listdir(self._base_dir):
                path = os.path.join(self._base_dir, name)
                if not (os.path.isdir(path) and len(name) == 10 and name < cutoff):
                    continue
                for db_file in os.listdir(path):
                    db_path = os.path.join(path, db_file)
                    conn = self._conns.pop(db_path, None)
                    if conn:
                        with contextlib.suppress(Exception):
                            conn.close()
                    self._initialized.discard(db_path)
                await loop.run_in_executor(None, shutil.rmtree, path, True)
                removed += 1
        except Exception as e:
            log.warning(f'[{self._log_tag}] 清理目录异常: {e}')
        if removed:
            log.info(f'[{self._log_tag}] 已清理 {removed} 个过期日志目录')

    def _close_stale_conns(self):
        """关闭非当天 daily 日志的数据库连接, 释放资源"""
        today = datetime.now().strftime('%Y-%m-%d')
        with self._init_lock:
            to_close = []
            for db_path in list(self._conns):
                parent = os.path.basename(os.path.dirname(db_path))
                if parent != today and re.fullmatch(r'\d{4}-\d{2}-\d{2}', parent):
                    to_close.append(db_path)
            for db_path in to_close:
                conn = self._conns.pop(db_path, None)
                self._conn_locks.pop(db_path, None)
                self._initialized.discard(db_path)
                if conn:
                    with contextlib.suppress(Exception):
                        conn.close()
        if to_close:
            log.debug(f'[{self._log_tag}] 已关闭 {len(to_close)} 个过期 daily 连接')

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
                except TimeoutError:
                    pass
                await self._cleanup_expired()
                self._close_stale_conns()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning(f'[{self._log_tag}] 清理异常: {e}')
                await asyncio.sleep(3600)

    async def _start_tasks(self):
        os.makedirs(self._base_dir, exist_ok=True)
        self._tasks.append(asyncio.create_task(self._periodic_flush()))
        if self._retention_days > 0:
            self._tasks.append(asyncio.create_task(self._periodic_cleanup()))

    async def _shutdown_base(self):
        await _shutdown_tasks(self._tasks, self._stop)
        await self._flush_all()
        _close_all_conns(self._conns)
