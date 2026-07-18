"""日志服务公共基类 — 连接管理、查询、批量写入、定时刷写/清理"""

import asyncio
import contextlib
import os
import re
import shutil
import sqlite3
import threading
import time
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
    _missing_index_sqls,
)


def _dict_factory(cursor, row):
    """直接返回 dict, 跳过 sqlite3.Row 中间对象"""
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}

log = get_logger(SERVICE, '日志')
_DATE_DIR_RE = re.compile(r'\d{4}-\d{2}-\d{2}$')


async def _shutdown_tasks(tasks, stop_event):
    """stop + cancel 所有后台任务"""
    stop_event.set()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def _close_all_conns(conns, conn_locks=None):
    """WAL checkpoint + 关闭并清空所有 SQLite 连接 (加锁防止与 executor 写线程竞态)"""
    for db_path, conn in conns.items():
        lock = (conn_locks or {}).get(db_path)
        if lock:
            lock.acquire()
        try:
            with contextlib.suppress(Exception):
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            with contextlib.suppress(Exception):
                conn.close()
        finally:
            if lock:
                with contextlib.suppress(Exception):
                    lock.release()
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
        self._read_conns = {}  # 独立读连接, 避免读写互锁
        self._read_locks = {}
        self._initialized = set()
        self._init_lock = threading.Lock()  # 保护连接池初始化, 避免多线程竞态
        self._stop = asyncio.Event()
        self._tasks = []
        self._log_tag = ''  # 子类设置
        self._last_checkpoint = 0.0

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
            preexisting = os.path.isfile(db_path) and os.path.getsize(db_path) > 0
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
                    if log_type != 'data':
                        _migrate_missing_columns(conn, log_type)
                    if preexisting:
                        # 已有大库现场建索引可能需要数十秒, 改为后台线程补建
                        self._build_missing_indexes_bg(db_path, conn, log_type)
                    else:
                        _ensure_indexes(conn, log_type)
                self._initialized.add(db_path)
            self._conn_locks.setdefault(db_path, threading.Lock())
            self._conns[db_path] = conn  # 最后赋值, 确保读端看到的连接已完全初始化
            return conn

    def _build_missing_indexes_bg(self, db_path, conn, log_type):
        """检查缺失索引并在后台线程用独立连接补建 (不阻塞查询/写入线程)"""
        missing = _missing_index_sqls(conn, log_type)
        if not missing:
            return

        def _build():
            try:
                bg = sqlite3.connect(db_path)
                bg.execute('PRAGMA busy_timeout=30000')
                for sql in missing:
                    bg.execute(sql)
                    bg.commit()
                bg.close()
                log.info(f'[{self._log_tag}] 后台补建索引完成 ({log_type}): {len(missing)}个 {os.path.dirname(db_path)}')
            except Exception as e:
                log.warning(f'[{self._log_tag}] 后台补建索引失败 ({log_type}): {e}')

        threading.Thread(target=_build, name=f'idx-{log_type}', daemon=True).start()

    def _get_read_conn(self, db_path, log_type):
        """获取独立的读连接 (WAL 模式下读写不互锁)"""
        conn = self._read_conns.get(db_path)
        if conn is not None:
            return conn
        # 先确保写连接已初始化 (建表/索引), 在 _init_lock 外调用避免死锁
        self._get_conn(db_path, log_type)
        with self._init_lock:
            conn = self._read_conns.get(db_path)
            if conn is not None:
                return conn
            conn = sqlite3.connect(db_path, check_same_thread=False)
            conn.row_factory = _dict_factory
            if self._wal:
                conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA synchronous=NORMAL')
            conn.execute('PRAGMA query_only=ON')
            self._read_locks.setdefault(db_path, threading.Lock())
            self._read_conns[db_path] = conn
            return conn

    def query(self, log_type, sql, params=(), date=None):
        """同步查询, 返回 [dict] — 使用独立读连接, 不阻塞写入"""
        db_path = self._resolve_db_path(log_type, date)
        if not os.path.isfile(db_path):
            return []
        conn = self._get_read_conn(db_path, log_type)
        lock = self._read_locks.get(db_path)
        try:
            with lock:
                return conn.execute(sql, params).fetchall()
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
        try:
            conn = self._get_conn(db_path, log_type)
        except Exception as e:
            log.error(f'[{self._log_tag}] 打开数据库失败 [{log_type}] {db_path}: {e}')
            raise
        lock = self._conn_locks.get(db_path)
        try:
            with lock:
                conn.executemany(sql, rows)
                conn.commit()
        except Exception as e:
            log.error(f'[{self._log_tag}] 写入失败 [{log_type}] ({len(rows)}条): {e}')
            with contextlib.suppress(Exception):
                conn.rollback()
            raise

    async def _write_batch(self, db_path, log_type, sql, rows):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._write_batch_sync, db_path, log_type, sql, rows)

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
        try:
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
        except Exception as e:
            log.error(f'[{self._log_tag}] 刷写失败 [{log_type}] {len(batch)}条消息丢失: {e}')
            # 将未写入的数据放回队列重试
            for item in batch:
                with contextlib.suppress(asyncio.QueueFull):
                    q.put_nowait(item)

    async def _flush_all(self):
        for t in self._queues:
            try:
                await self._flush_type(t)
            except Exception as e:
                log.warning(f'[{self._log_tag}] 刷写失败 [{t}]: {e}')

    def _checkpoint_wal_sync(self):
        """定期把 WAL 合并回主库并截断, 避免 WAL 膨胀拖慢查询"""
        for db_path, conn in list(self._conns.items()):
            lock = self._conn_locks.get(db_path)
            if lock is None:
                continue
            with lock, contextlib.suppress(Exception):
                conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')

    async def _periodic_flush(self):
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=max(self._interval, 1))
            except TimeoutError:
                pass
            except asyncio.CancelledError:
                break
            await self._flush_all()
            if self._wal and time.monotonic() - self._last_checkpoint >= 300:
                self._last_checkpoint = time.monotonic()
                await asyncio.get_running_loop().run_in_executor(None, self._checkpoint_wal_sync)

    async def _cleanup_expired(self):
        if self._retention_days <= 0:
            return
        cutoff = (datetime.now() - timedelta(days=self._retention_days)).strftime('%Y-%m-%d')
        loop = asyncio.get_running_loop()
        removed = 0
        try:
            for name in os.listdir(self._base_dir):
                path = os.path.join(self._base_dir, name)
                if not (os.path.isdir(path) and _DATE_DIR_RE.fullmatch(name) and name < cutoff):
                    continue
                for db_file in os.listdir(path):
                    db_path = os.path.join(path, db_file)
                    conn = self._conns.pop(db_path, None)
                    self._conn_locks.pop(db_path, None)
                    if conn:
                        with contextlib.suppress(Exception):
                            conn.close()
                    rconn = self._read_conns.pop(db_path, None)
                    self._read_locks.pop(db_path, None)
                    if rconn:
                        with contextlib.suppress(Exception):
                            rconn.close()
                    self._initialized.discard(db_path)
                await loop.run_in_executor(None, shutil.rmtree, path, True)
                removed += 1
        except Exception as e:
            log.warning(f'[{self._log_tag}] 清理目录异常: {e}')
        if removed:
            log.info(f'[{self._log_tag}] 已清理 {removed} 个过期日志目录')

    def _close_stale_conns(self):
        """关闭非当天 daily 日志的数据库连接 (写+读), 释放资源"""
        today = datetime.now().strftime('%Y-%m-%d')
        with self._init_lock:
            to_close = []
            for db_path in list(self._conns):
                parent = os.path.basename(os.path.dirname(db_path))
                if parent != today and _DATE_DIR_RE.fullmatch(parent):
                    to_close.append(db_path)
            for db_path in to_close:
                conn = self._conns.pop(db_path, None)
                self._conn_locks.pop(db_path, None)
                self._initialized.discard(db_path)
                if conn:
                    with contextlib.suppress(Exception):
                        conn.close()
                # 同时关闭对应的读连接
                rconn = self._read_conns.pop(db_path, None)
                self._read_locks.pop(db_path, None)
                if rconn:
                    with contextlib.suppress(Exception):
                        rconn.close()
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
        log.debug(f'[{self._log_tag}] 日志刷写任务已启动 (间隔={self._interval}s, 保留={self._retention_days}天, 目录={self._base_dir})')

    async def _shutdown_base(self):
        await _shutdown_tasks(self._tasks, self._stop)
        await self._flush_all()
        _close_all_conns(self._conns, self._conn_locks)
        _close_all_conns(self._read_conns, self._read_locks)
