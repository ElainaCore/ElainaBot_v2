#!/usr/bin/env python
"""MySQL 异步连接池组件

基于 aiomysql, 提供 execute/fetch/upsert 等便捷方法。
由 datastore 主模块统一管理生命周期, 不单独作为模块使用。
"""

import asyncio
import contextlib
import time

_DEFAULTS = {
    'host': '127.0.0.1',
    'port': 3306,
    'user': 'root',
    'password': '',
    'database': '',
    'charset': 'utf8mb4',
    'minsize': 2,
    'connect_timeout': 10,
    'acquire_timeout': 10,
    'pool_recycle': 300,
    'connect_concurrency': 8,
    'autocommit': True,
    'slow_query_seconds': 3,
}

_COMMENTS = {
    'host': 'MySQL 服务器地址',
    'port': 'MySQL 端口号',
    'user': '数据库用户名',
    'password': '数据库密码, 无密码留空',
    'database': '数据库名称 (必填)',
    'charset': '字符集编码',
    'minsize': '连接池最小连接数',
    'connect_timeout': '连接超时 (秒)',
    'acquire_timeout': '获取连接超时 (秒), 建连或等待连接时快速报错',
    'pool_recycle': '空闲连接最长保留时间 (秒), 避免长时间占用 MySQL 服务端连接数',
    'connect_concurrency': '并发新建连接数上限, 防止流量洪峰时向 MySQL 发起建连风暴',
    'autocommit': '是否自动提交事务',
    'slow_query_seconds': '慢查询告警阈值 (秒), 超过该耗时的 SQL 会记录告警日志, 0 为关闭',
}


def _conn_broken(conn):
    reader = conn._reader
    return conn.closed or reader.at_eof() or reader.exception() or reader.eof_received


async def _safe_rollback(conn):
    with contextlib.suppress(BaseException):
        await conn.rollback()


async def _create_pool(connect_concurrency, minsize, pool_recycle, **conn_kwargs):
    """创建无限连接池, 锁内取空闲连接, 锁外限流建连"""
    import aiomysql

    class _Pool(aiomysql.Pool):
        def _pop_reusable_free(self):
            """锁内调用: 剔除失效/超期空闲连接, 返回一个可复用连接或 None"""
            while self._free:
                conn = self._free.popleft()
                expired = self._recycle > -1 and self._loop.time() - conn.last_usage > self._recycle
                if _conn_broken(conn) or expired:
                    conn.close()
                else:
                    return conn
            return None

        def release(self, conn):
            """父类丢弃连接时不唤醒, 会让 wait_closed 挂住, 这里补一次。"""
            discarded = conn in self._used and (conn.closed or conn.get_transaction_status())
            result = super().release(conn)
            if discarded:
                wakeup = self._loop.create_task(self._wakeup())
                wakeup.add_done_callback(lambda task: None if task.cancelled() else task.exception())
            return result

        async def _acquire(self):
            if self._closing:
                raise RuntimeError('Cannot acquire connection after closing pool')
            async with self._cond:
                conn = self._pop_reusable_free()
                if conn is not None:
                    self._used.add(conn)
                    return conn
                self._acquiring += 1  # 锁内占名额, 锁外建连
            try:
                async with self._connect_sem:
                    conn = await aiomysql.connect(echo=self._echo, loop=self._loop, **self._conn_kwargs)
            except BaseException:
                async with self._cond:
                    self._acquiring -= 1
                    self._cond.notify()
                raise
            async with self._cond:
                self._acquiring -= 1
                self._used.add(conn)
                return conn

    pool = _Pool(minsize, 0, False, pool_recycle, asyncio.get_running_loop(), **conn_kwargs)
    pool._connect_sem = asyncio.Semaphore(max(1, connect_concurrency))
    if minsize > 0:
        async with pool._cond:
            await pool._fill_free_pool(False)
    return pool


class _AcquireContext:
    """带超时的连接获取上下文"""

    __slots__ = ('_pool', '_timeout', '_log', '_conn')

    def __init__(self, pool, timeout, log):
        self._pool = pool
        self._timeout = timeout
        self._log = log
        self._conn = None

    async def __aenter__(self):
        # 用 shield 让 acquire 继续完成, 超时或取消后将连接归还池中。
        acquire_task = asyncio.ensure_future(self._pool.acquire())
        try:
            self._conn = await asyncio.wait_for(asyncio.shield(acquire_task), self._timeout)
        except BaseException as exc:
            acquire_task.add_done_callback(self._release_late)
            if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
                pool = self._pool
                self._log.error(
                    f'获取 MySQL 连接超时 ({self._timeout}s), 连接池占用: '
                    f'used={len(pool._used)} free={pool.freesize} connecting={pool._acquiring}'
                )
                raise RuntimeError(f'MySQL 连接池获取超时 ({self._timeout}s)') from None
            raise
        return self._conn

    def _release_late(self, task):
        if not task.cancelled() and task.exception() is None:
            with contextlib.suppress(Exception):
                self._pool.release(task.result())

    async def __aexit__(self, *exc):
        if self._conn is not None:
            if exc[0] is not None and _conn_broken(self._conn):
                with contextlib.suppress(Exception):
                    self._conn.close()
            self._pool.release(self._conn)
            self._conn = None
        return False


class MySQLPool:
    """MySQL 异步连接池封装"""

    __slots__ = ('_cfg', '_pool', '_available', '_log')

    def __init__(self, cfg, log):
        self._cfg = cfg
        self._pool = None
        self._available = False
        self._log = log

    async def initialize(self):
        try:
            import aiomysql  # noqa: F401
        except ImportError:
            self._log.error('aiomysql 未安装, MySQL 连接池禁用 (pip install aiomysql)')
            return
        if not self._cfg.get('database'):
            self._log.warning('未配置 database, 跳过 MySQL 初始化')
            return
        try:
            self._pool = await _create_pool(
                connect_concurrency=int(self._cfg.get('connect_concurrency', 8)),
                minsize=int(self._cfg.get('minsize', 2)),
                pool_recycle=int(self._cfg.get('pool_recycle', 300)),
                host=self._cfg.get('host', '127.0.0.1'),
                port=int(self._cfg.get('port', 3306)),
                user=self._cfg.get('user', 'root'),
                password=str(self._cfg.get('password', '')),
                db=self._cfg.get('database'),
                charset=self._cfg.get('charset', 'utf8mb4'),
                connect_timeout=int(self._cfg.get('connect_timeout', 10)),
                autocommit=bool(self._cfg.get('autocommit', True)),
            )
            self._available = True
        except Exception as e:
            self._log.error(f'MySQL 初始化失败: {e}')
            self._available = False

    def is_available(self):
        return self._available and self._pool is not None

    async def close(self):
        self._available = False
        if self._pool:
            pool, self._pool = self._pool, None
            pool.close()
            await pool.wait_closed()

    # ---------- 连接 ----------

    def acquire(self):
        """获取连接 (用作 async with pool.acquire() as conn), 带超时快速失败"""
        if not self.is_available():
            raise RuntimeError('MySQL 连接池不可用')
        return _AcquireContext(self._pool, int(self._cfg.get('acquire_timeout', 10)), self._log)

    # ---------- 便捷方法 ----------

    async def _timed_execute(self, cur, sql, params=None):
        """执行 SQL 并记录慢查询 (长时间占用连接会拖垄整个连接池)"""
        threshold = float(self._cfg.get('slow_query_seconds', 3) or 0)
        start = time.monotonic()
        try:
            return await cur.execute(sql, params)
        finally:
            elapsed = time.monotonic() - start
            if threshold and elapsed >= threshold:
                self._log.warning(f'慢查询 {elapsed:.1f}s: {" ".join(str(sql).split())[:300]}')

    async def execute(self, sql, params=None):
        """执行写操作, 返回受影响行数"""
        if not self.is_available():
            return 0
        async with self.acquire() as conn, conn.cursor() as cur:
            is_ddl = sql.lstrip()[:6].upper() in ('CREATE', 'ALTER ', 'DROP T')
            try:
                if is_ddl:
                    await cur.execute('SET sql_notes=0')
                rows = await self._timed_execute(cur, sql, params)
                if not conn.get_autocommit():
                    await conn.commit()
                return rows
            except BaseException:
                if not conn.get_autocommit():
                    await _safe_rollback(conn)
                raise
            finally:
                if is_ddl:
                    with contextlib.suppress(BaseException):
                        await cur.execute('SET sql_notes=1')

    async def execute_many(self, sql, params_list):
        """批量执行"""
        if not self.is_available() or not params_list:
            return 0
        async with self.acquire() as conn, conn.cursor() as cur:
            try:
                rows = await cur.executemany(sql, params_list)
                if not conn.get_autocommit():
                    await conn.commit()
                return rows
            except BaseException:
                if not conn.get_autocommit():
                    await _safe_rollback(conn)
                raise

    async def fetch_one(self, sql, params=None):
        """查询单行, 返回 dict 或 None"""
        if not self.is_available():
            return None
        import aiomysql

        async with (
            self.acquire() as conn,
            conn.cursor(aiomysql.DictCursor) as cur,
        ):
            await self._timed_execute(cur, sql, params)
            return await cur.fetchone()

    async def fetch_all(self, sql, params=None):
        """查询多行, 返回 [dict, ...]"""
        if not self.is_available():
            return []
        import aiomysql

        async with (
            self.acquire() as conn,
            conn.cursor(aiomysql.DictCursor) as cur,
        ):
            await self._timed_execute(cur, sql, params)
            return list(await cur.fetchall())

    async def fetch_value(self, sql, params=None, default=None):
        """查询单个值"""
        if not self.is_available():
            return default
        async with self.acquire() as conn, conn.cursor() as cur:
            await self._timed_execute(cur, sql, params)
            row = await cur.fetchone()
            return row[0] if row else default

    async def upsert(self, table, data, conflict_columns):
        """INSERT ... ON DUPLICATE KEY UPDATE"""
        if not self.is_available() or not data:
            return 0
        cols = list(data.keys())
        placeholders = ', '.join(['%s'] * len(cols))
        update_cols = [c for c in cols if c not in conflict_columns]
        sql = f'INSERT INTO {table} ({", ".join(cols)}) VALUES ({placeholders}) AS new'
        if update_cols:
            update_clause = ', '.join(f'{c}=new.{c}' for c in update_cols)
            sql += f' ON DUPLICATE KEY UPDATE {update_clause}'
        return await self.execute(sql, list(data.values()))

    async def table_exists(self, table_name):
        """检查表是否存在"""
        row = await self.fetch_one(
            'SELECT COUNT(*) AS c FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name=%s',
            (table_name,),
        )
        return bool(row and row.get('c'))

    async def execute_transaction(self, sql_list):
        """执行事务 (列表中每项为 {'sql': ..., 'params': ...})"""
        if not self.is_available():
            return False
        async with self.acquire() as conn:
            try:
                await conn.begin()
                async with conn.cursor() as cur:
                    for item in sql_list:
                        sql = item.get('sql')
                        if sql:
                            await cur.execute(sql, item.get('params'))
                await conn.commit()
                return True
            except BaseException as exc:
                await _safe_rollback(conn)
                if isinstance(exc, asyncio.CancelledError):
                    raise
                self._log.error(f'MySQL 事务执行失败: {exc}')
                return False

    async def ping(self):
        """连通性测试"""
        try:
            async with self.acquire() as conn:
                await conn.ping()
            return True
        except Exception:
            return False


# ==================== 跨模块重载存活的连接池持有器 ====================
# reload 仅 pop `modules.datastore` 主模块, 本子模块留在 sys.modules 中,
# 因此这里的全局持有器可跨 datastore 重载存活, 实现连接池平滑热更 (无重连空窗)。

_holder = {'sig': None, 'pool': None, 'pending_close': None}

_SIG_KEYS = (
    'host', 'port', 'user', 'password', 'database', 'charset', 'minsize',
    'connect_timeout', 'acquire_timeout', 'pool_recycle', 'connect_concurrency', 'autocommit',
)


def _sig(cfg):
    return tuple(str(cfg.get(k)) for k in _SIG_KEYS)


async def get_pool(cfg, log):
    """获取 MySQL 连接池: 配置未变复用已连接池, 变则新建并关旧池 (平滑热更)"""
    pc = _holder['pending_close']
    if pc is not None and not pc.done():
        pc.cancel()  # 取消 teardown 安排的延迟关闭, 本次 setup 接管现有池
    _holder['pending_close'] = None

    sig = _sig(cfg)
    if _holder['pool'] is not None and _holder['sig'] == sig and _holder['pool'].is_available():
        return _holder['pool']

    old = _holder['pool']
    pool = MySQLPool(cfg, log)
    await pool.initialize()
    if not pool.is_available() and old is not None:
        log.error('MySQL 新连接池初始化失败, 保留现有连接池')
        return old
    _holder['sig'] = sig
    _holder['pool'] = pool
    if old is not None:
        with contextlib.suppress(Exception):
            await old.close()
    return pool


def schedule_close(delay=3):
    """teardown 调用: 延迟关闭当前池; delay 内若再次 get_pool 则被取消 (区分 reload 与真正 disable)"""
    if _holder['pool'] is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def _close():
        await asyncio.sleep(delay)
        pool = _holder['pool']
        _holder['sig'] = None
        _holder['pool'] = None
        _holder['pending_close'] = None
        if pool is not None:
            with contextlib.suppress(Exception):
                await pool.close()

    _holder['pending_close'] = loop.create_task(_close())
