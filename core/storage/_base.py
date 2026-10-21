"""日志服务公共基类 — 连接管理、查询、批量写入、定时刷写/清理"""

import asyncio
import contextlib
import os
import re
import shutil
import sqlite3
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta
from queue import Full, Queue

from core.base.logger import SERVICE, get_logger, now_str
from core.base.metrics import counter
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
from core.storage._sqlite import SQLiteConnectionPool
from core.storage._workers import SQLITE_EXECUTOR
from core.storage.message_stats import add_message_stats, init_message_stats


def _dict_factory(cursor, row):
    """直接返回 dict, 跳过 sqlite3.Row 中间对象"""
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}

log = get_logger(SERVICE, '日志')
_DATE_DIR_RE = re.compile(r'\d{4}-\d{2}-\d{2}$')


class _ExpiredLogError(FileNotFoundError):
    """保留策略已阻止重建该日期的数据库。"""


class _BatchQueue(Queue):
    def take(self, limit):
        """批量出队只获取一次队列锁；锁内不解析日志或执行 SQL。"""
        with self.mutex:
            items = [self._get() for _ in range(min(limit, self._qsize()))]
            self.not_full.notify_all()
            return items


async def _shutdown_tasks(tasks, stop_event):
    """stop + cancel 所有后台任务"""
    stop_event.set()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


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
        self._batch_size = max(int(batch_size or 0), 0)
        self._retention_days = retention_days
        self._queues = {t: _BatchQueue(maxsize=_QUEUE_MAXSIZE) for t in queue_types}
        self._queue_lock = threading.Lock()  # 仅保护准入，不包含数据库/文件操作
        self._accepting = True
        self._expired_before = ''
        self._pool = SQLiteConnectionPool(self._open_writer, self._open_reader, self._check_db_path)
        self._pending_batches = {t: deque() for t in queue_types}
        self._unprepared_batches = {t: [] for t in queue_types}
        self._flush_jobs = {}
        self._io_futures = set()
        self._db_queues = {}
        self._shutdown_job = None
        self._stop = asyncio.Event()
        self._tasks = []
        self._log_tag = ''  # 子类设置
        self._last_checkpoint = 0.0

    def _resolve_db_path(self, log_type, date=None):
        if log_type in DAILY_TYPES:
            date = date or datetime.now().strftime('%Y-%m-%d')
            return os.path.join(self._base_dir, date, f'{log_type}.db')
        return os.path.join(self._base_dir, f'{log_type}.db')

    def _check_db_path(self, db_path, log_type):
        if self._expired_before and log_type in DAILY_TYPES:
            date = os.path.basename(os.path.dirname(db_path))
            if _DATE_DIR_RE.fullmatch(date) and date < self._expired_before:
                raise _ExpiredLogError(f'日志日期 {date} 已超过保留期限')

    def _locked_conn(self, db_path, log_type, *, read_only=False):
        return self._pool.connection(db_path, log_type, read_only=read_only)

    @contextlib.contextmanager
    def _transaction(self, db_path, log_type):
        # commit 及失败 rollback 都处在同一个借用和使用锁内。
        with self._locked_conn(db_path, log_type) as conn, conn:
            yield conn

    def _ensure_schema(self, db_path, log_type):
        with self._locked_conn(db_path, log_type):
            pass

    def _open_writer(self, db_path, log_type):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        preexisting = os.path.isfile(db_path) and os.path.getsize(db_path) > 0
        conn = sqlite3.connect(db_path, check_same_thread=False)
        try:
            if self._wal:
                conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA synchronous=NORMAL')
            schema = _SCHEMAS.get(log_type)
            if schema:
                if log_type == 'data':
                    conn.executescript(schema)
                    _migrate_data_tables(conn)
                else:
                    conn.execute(schema)
                    conn.commit()
                    _migrate_missing_columns(conn, log_type)
                if preexisting:
                    self._build_missing_indexes_bg(db_path, conn, log_type)
                else:
                    _ensure_indexes(conn, log_type)
                if log_type == 'message':
                    init_message_stats(conn)
                    conn.commit()
            return conn
        except BaseException:
            conn.close()
            raise

    def _build_missing_indexes_bg(self, db_path, conn, log_type):
        """后台补索引沿用文件写锁，并参与连接退役协议。"""
        missing = _missing_index_sqls(conn, log_type)
        if not missing:
            return

        def _build():
            try:
                for sql in missing:
                    with self._transaction(db_path, log_type) as bg:
                        bg.execute(sql)
                log.info(f'[{self._log_tag}] 后台补建索引完成 ({log_type}): {len(missing)}个 {os.path.dirname(db_path)}')
            except Exception as e:
                log.warning(f'[{self._log_tag}] 后台补建索引失败 ({log_type}): {e}')

        threading.Thread(target=_build, name=f'idx-{log_type}', daemon=True).start()

    def _open_reader(self, db_path, log_type):
        conn = sqlite3.connect(db_path, check_same_thread=False)
        try:
            conn.row_factory = _dict_factory
            if self._wal:
                conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA synchronous=NORMAL')
            conn.execute('PRAGMA query_only=ON')
            return conn
        except BaseException:
            conn.close()
            raise

    def query(self, log_type, sql, params=(), date=None):
        """同步查询, 返回 [dict] — 使用独立读连接, 不阻塞写入"""
        db_path = self._resolve_db_path(log_type, date)
        if not os.path.isfile(db_path):
            return []
        try:
            with self._locked_conn(db_path, log_type, read_only=True) as conn:
                return conn.execute(sql, params).fetchall()
        except FileNotFoundError:
            return []
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
        with self._queue_lock:
            if not self._accepting:
                return False
            try:
                self._queues[log_type].put_nowait(data)
                return True
            except Full:
                counter('log_queue_rejected')
                return False

    def _extract_row(self, log_type, data):
        """dict → INSERT 参数元组 (子类实现)"""
        return None

    def _write_batch_sync(self, db_path, log_type, sql, rows):
        try:
            with self._transaction(db_path, log_type) as conn:
                conn.executemany(sql, rows)
                if log_type == 'message':
                    add_message_stats(conn, ((r[0], r[3], r[4], r[7], r[8], r[9]) for r in rows))
        except _ExpiredLogError:
            raise
        except Exception as e:
            log.error(f'[{self._log_tag}] 写入失败 [{log_type}] ({len(rows)}条): {e}')
            raise

    def _track_io(self, future):
        self._io_futures.add(future)
        future.add_done_callback(self._io_finished)
        return future

    async def _run_io(self, func, *args):
        """取消调用方不取消线程工作；停机等待全部在途工作。"""
        future = asyncio.get_running_loop().run_in_executor(SQLITE_EXECUTOR, func, *args)
        return await asyncio.shield(self._track_io(future))

    def _io_finished(self, future):
        self._io_futures.discard(future)
        if not future.cancelled():
            future.exception()  # 同时处理调用方已取消的情况，避免未收集异常。

    async def _run_write(self, db_path, func, *args, draining=False):
        return await self._run_db((db_path, False), func, *args, draining=draining)

    async def _run_read(self, log_type, func, *args):
        return await self._run_db((self._resolve_db_path(log_type), True), func, *args)

    async def _run_db(self, key, func, *args, draining=False):
        if not draining and not self._accepting:
            raise RuntimeError('SQLite 日志服务正在关闭')
        # 同库的读、写各自排队；连接锁的等待者不占住 executor 线程。
        loop = asyncio.get_running_loop()
        with self._queue_lock:
            start = key not in self._db_queues
            queue = self._db_queues.setdefault(key, deque())
        if start:
            try:
                future = loop.run_in_executor(SQLITE_EXECUTOR, func, *args)
            except BaseException:
                with self._queue_lock:
                    self._db_queues.pop(key)
                raise
            future.add_done_callback(lambda _: self._continue_db_worker(key))
            return await asyncio.shield(self._track_io(future))
        result = loop.create_future()
        result.add_done_callback(self._io_finished)
        with self._queue_lock:
            queue.append((func, args, result))
        # 取消结果等待不会撤销入队操作；工作线程的 Future 跟踪整个队列。
        return await result

    def _start_db_worker(self, loop, key):
        try:
            future = loop.run_in_executor(SQLITE_EXECUTOR, self._drain_db_ops, loop, key)
        except BaseException as exc:
            with self._queue_lock:
                pending = self._db_queues.pop(key)
            for _, _, result in pending:
                self._write_finished(result, None, exc)
        else:
            # 先延续未执行的队列，再解除线程 Future 的停机跟踪。
            future.add_done_callback(lambda done: self._db_ops_drained(key, done))
            self._track_io(future)

    def _drain_db_ops(self, loop, key):
        """同一连接的操作由一个线程连续执行，避免反复切换线程。"""
        with self._queue_lock:
            queue = self._db_queues[key]
            func, args, result = queue.popleft()
        while True:
            try:
                value, error = func(*args), None
            except BaseException as exc:
                value, error = None, exc
            with self._queue_lock:
                if not queue:
                    return result, value, error
                next_job = queue.popleft()
            loop.call_soon_threadsafe(self._write_finished, result, value, error)
            func, args, result = next_job

    def _db_ops_drained(self, key, future):
        if future.cancelled():
            return
        self._write_finished(*future.result())
        self._continue_db_worker(key)

    def _continue_db_worker(self, key):
        with self._queue_lock:
            pending = bool(self._db_queues[key])
            if not pending:
                self._db_queues.pop(key)
        if pending:
            self._start_db_worker(asyncio.get_running_loop(), key)

    @staticmethod
    def _write_finished(result, value, error):
        if not result.done():
            if error is None:
                result.set_result(value)
            else:
                result.set_exception(error)

    async def _write_batch(self, db_path, log_type, sql, rows):
        await self._run_write(db_path, self._write_batch_sync, db_path, log_type, sql, rows, draining=True)

    def _flush_job(self, log_type):
        job = self._flush_jobs.get(log_type)
        if job is None:
            job = asyncio.create_task(self._flush_type_inner(log_type))
            self._flush_jobs[log_type] = job
            job.add_done_callback(lambda done: self._flush_finished(log_type, done))
        return job

    async def _flush_type(self, log_type):
        return await asyncio.shield(self._flush_job(log_type))

    def _flush_finished(self, log_type, job):
        if self._flush_jobs.get(log_type) is job:
            self._flush_jobs.pop(log_type, None)
        if not job.cancelled():
            job.exception()

    async def _flush_type_inner(self, log_type):
        q = self._queues[log_type]
        pending = self._pending_batches[log_type]
        sql = _INSERTS.get(log_type)
        if not sql:
            return True
        batch = self._unprepared_batches[log_type]
        if not pending and not batch and q.empty():
            return True
        try:
            if not pending:
                await self._run_io(self._prepare_batch, log_type)
            while pending:
                path, rows = pending[0]
                try:
                    await self._write_batch(path, log_type, sql, rows)
                except _ExpiredLogError:
                    # 已过期日志不重建被保留策略删除的目录。
                    counter('log_expired_rows', len(rows))
                pending.popleft()  # 仅成功提交或按保留规则过期的日期退出重试。
            return True
        except Exception as e:
            counter('log_flush_failures')
            log.error(f'[{self._log_tag}] 刷写失败 [{log_type}], 保留未提交批次重试: {e}')
            return False

    def _prepare_batch(self, log_type):
        batch = self._unprepared_batches[log_type]
        batch.extend(self._queues[log_type].take((self._batch_size or 10000) - len(batch)))
        groups = defaultdict(list)
        for item in batch:
            ts = item['timestamp'] if 'timestamp' in item else now_str()
            row = self._extract_row(log_type, dict(item, timestamp=ts))
            if row:
                groups[ts[:10] if log_type in DAILY_TYPES else None].append(row)
        self._pending_batches[log_type].extend(
            [(self._resolve_db_path(log_type, date), rows) for date, rows in groups.items()])
        batch.clear()

    async def _flush_all(self):
        # 各文件独立刷写，单个库等待写锁不推迟其他队列。
        results = await asyncio.gather(*(self._flush_type(t) for t in self._queues), return_exceptions=True)
        for log_type, result in zip(self._queues, results, strict=True):
            if isinstance(result, BaseException):
                log.warning(f'[{self._log_tag}] 刷写失败 [{log_type}]: {result}')
        return all(result is True for result in results)

    def _checkpoint_wal_sync(self):
        """在线 PASSIVE 检查点不等待读者；关闭连接时由 SQLite 完成回收。"""
        for db_path in self._pool.paths():
            log_type = os.path.splitext(os.path.basename(db_path))[0]
            with contextlib.suppress(Exception), self._locked_conn(db_path, log_type) as conn:
                conn.execute('PRAGMA wal_checkpoint(PASSIVE)')

    async def _periodic_flush(self):
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=max(self._interval, 1))
            except TimeoutError:
                pass
            except asyncio.CancelledError:
                raise
            await self._flush_all()
            if self._wal and time.monotonic() - self._last_checkpoint >= 300:
                self._last_checkpoint = time.monotonic()
                await self._run_io(self._checkpoint_wal_sync)

    def _drop_conns(self, db_path):
        """仅在所有借用者释放后关闭指定库；调用方须在工作线程执行。"""
        self._pool.retire(db_path)

    async def _cleanup_expired(self):
        if self._retention_days <= 0:
            return
        cutoff = (datetime.now() - timedelta(days=self._retention_days)).strftime('%Y-%m-%d')
        await self._run_io(self._cleanup_expired_sync, cutoff)

    def _cleanup_expired_sync(self, cutoff):
        # 先阻止新借用，再退役已借用的连接，避免重新创建过期库。
        self._expired_before = max(self._expired_before, cutoff)
        removed = 0
        try:
            # 也等待已借用但尚未创建文件的初始化工作。
            for db_path in self._pool.paths():
                parent = os.path.basename(os.path.dirname(db_path))
                if _DATE_DIR_RE.fullmatch(parent) and parent < cutoff:
                    self._drop_conns(db_path)
            for name in os.listdir(self._base_dir):
                path = os.path.join(self._base_dir, name)
                if not (os.path.isdir(path) and _DATE_DIR_RE.fullmatch(name) and name < cutoff):
                    continue
                shutil.rmtree(path)
                removed += 1
        except Exception as e:
            log.warning(f'[{self._log_tag}] 清理目录异常: {e}')
        if removed:
            log.info(f'[{self._log_tag}] 已清理 {removed} 个过期日志目录')

    def _close_stale_conns(self):
        """关闭非当天 daily 日志的数据库连接 (写+读), 释放资源"""
        today = datetime.now().strftime('%Y-%m-%d')
        closed = 0
        for db_path in self._pool.paths():
            parent = os.path.basename(os.path.dirname(db_path))
            if parent != today and _DATE_DIR_RE.fullmatch(parent):
                self._drop_conns(db_path)
                closed += 1
        if closed:
            log.debug(f'[{self._log_tag}] 已关闭 {closed} 个过期 daily 连接')

    async def _periodic_cleanup(self):
        while not self._stop.is_set():
            try:
                now = datetime.now()
                target = now.replace(hour=4, minute=10, second=0, microsecond=0)
                if now >= target:
                    target += timedelta(days=1)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=(target - now).total_seconds())
                    break
                except TimeoutError:
                    pass
                await self._cleanup_expired()
                await self._run_io(self._close_stale_conns)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning(f'[{self._log_tag}] 清理异常: {e}')
                await asyncio.sleep(3600)

    async def _start_tasks(self):
        await self._run_io(os.makedirs, self._base_dir, 0o777, True)
        self._tasks.append(asyncio.create_task(self._periodic_flush()))
        if self._retention_days > 0:
            self._tasks.append(asyncio.create_task(self._periodic_cleanup()))
        log.debug(f'[{self._log_tag}] 日志刷写任务已启动 (间隔={self._interval}s, 保留={self._retention_days}天, 目录={self._base_dir})')

    async def _shutdown_base(self):
        if self._shutdown_job is None:
            self._shutdown_job = asyncio.create_task(self._shutdown_inner())
            self._shutdown_job.add_done_callback(self._shutdown_finished)
        return await asyncio.shield(self._shutdown_job)

    def _shutdown_finished(self, job):
        if job.cancelled() or job.exception() is not None:
            self._shutdown_job = None

    def _has_backlog(self):
        return (any(not q.empty() for t, q in self._queues.items() if t in _INSERTS)
                or any(self._pending_batches.values()) or any(self._unprepared_batches.values()))

    async def _shutdown_inner(self):
        with self._queue_lock:
            self._accepting = False
        await _shutdown_tasks(self._tasks, self._stop)
        if self._flush_jobs:
            await asyncio.gather(*tuple(self._flush_jobs.values()))
        while self._has_backlog():
            if not await self._flush_all():
                # 不把持久化失败报告成停机成功，也不无期限重试阻塞退出。
                raise RuntimeError(f'[{self._log_tag}] 停机刷写失败，未提交批次仍保留')
        while self._io_futures:
            await asyncio.gather(*tuple(self._io_futures), return_exceptions=True)
            # gather 已完成的 Future 可能不让出执行权，完成回调仍需运行。
            await asyncio.sleep(0)
        await self._run_io(self._pool.close)
