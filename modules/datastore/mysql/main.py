#!/usr/bin/env python
"""MySQL 异步连接池组件

基于 aiomysql, 提供 execute/fetch/upsert 等便捷方法。
由 datastore 主模块统一管理生命周期, 不单独作为模块使用。
"""

import asyncio

_DEFAULTS = {
    'host': '127.0.0.1',
    'port': 3306,
    'user': 'root',
    'password': '',
    'database': '',
    'charset': 'utf8mb4',
    'minsize': 2,
    'maxsize': 20,
    'connect_timeout': 10,
    'acquire_timeout': 10,
    'autocommit': True,
}

_COMMENTS = {
    'host': 'MySQL 服务器地址',
    'port': 'MySQL 端口号',
    'user': '数据库用户名',
    'password': '数据库密码, 无密码留空',
    'database': '数据库名称 (必填)',
    'charset': '字符集编码',
    'minsize': '连接池最小连接数',
    'maxsize': '连接池最大连接数',
    'connect_timeout': '连接超时 (秒)',
    'acquire_timeout': '获取连接超时 (秒), 连接池占满时快速报错而非无限等待',
    'autocommit': '是否自动提交事务',
}


class _AcquireContext:
    """带超时的连接获取上下文: 池占满时快速失败并报告池占用情况"""

    __slots__ = ('_pool', '_timeout', '_log', '_conn')

    def __init__(self, pool, timeout, log):
        self._pool = pool
        self._timeout = timeout
        self._log = log
        self._conn = None

    async def __aenter__(self):
        try:
            self._conn = await asyncio.wait_for(self._pool.acquire(), self._timeout)
        except TimeoutError:
            pool = self._pool
            self._log.error(
                f'获取 MySQL 连接超时 ({self._timeout}s), 连接池占用: '
                f'used={pool.size - pool.freesize}/{pool.maxsize} free={pool.freesize} '
                f'— 可能存在慢查询/锁等待占住连接'
            )
            raise RuntimeError(f'MySQL 连接池获取超时 ({self._timeout}s)') from None
        return self._conn

    async def __aexit__(self, *exc):
        if self._conn is not None:
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
            import aiomysql
        except ImportError:
            self._log.error('aiomysql 未安装, MySQL 连接池禁用 (pip install aiomysql)')
            return
        if not self._cfg.get('database'):
            self._log.warning('未配置 database, 跳过 MySQL 初始化')
            return
        try:
            self._pool = await aiomysql.create_pool(
                host=self._cfg.get('host', '127.0.0.1'),
                port=int(self._cfg.get('port', 3306)),
                user=self._cfg.get('user', 'root'),
                password=str(self._cfg.get('password', '')),
                db=self._cfg.get('database'),
                charset=self._cfg.get('charset', 'utf8mb4'),
                minsize=int(self._cfg.get('minsize', 2)),
                maxsize=int(self._cfg.get('maxsize', 20)),
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

    async def execute(self, sql, params=None):
        """执行写操作, 返回受影响行数"""
        if not self.is_available():
            return 0
        async with self.acquire() as conn, conn.cursor() as cur:
            is_ddl = sql.lstrip()[:6].upper() in ('CREATE', 'ALTER ', 'DROP T')
            if is_ddl:
                await cur.execute('SET sql_notes=0')
            rows = await cur.execute(sql, params)
            if is_ddl:
                await cur.execute('SET sql_notes=1')
            if not conn.get_autocommit():
                await conn.commit()
            return rows

    async def execute_many(self, sql, params_list):
        """批量执行"""
        if not self.is_available() or not params_list:
            return 0
        async with self.acquire() as conn, conn.cursor() as cur:
            rows = await cur.executemany(sql, params_list)
            if not conn.get_autocommit():
                await conn.commit()
            return rows

    async def fetch_one(self, sql, params=None):
        """查询单行, 返回 dict 或 None"""
        if not self.is_available():
            return None
        import aiomysql

        async with (
            self.acquire() as conn,
            conn.cursor(aiomysql.DictCursor) as cur,
        ):
            await cur.execute(sql, params)
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
            await cur.execute(sql, params)
            return list(await cur.fetchall())

    async def fetch_value(self, sql, params=None, default=None):
        """查询单个值"""
        if not self.is_available():
            return default
        async with self.acquire() as conn, conn.cursor() as cur:
            await cur.execute(sql, params)
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
            await conn.begin()
            try:
                async with conn.cursor() as cur:
                    for item in sql_list:
                        sql = item.get('sql')
                        if sql:
                            await cur.execute(sql, item.get('params'))
                await conn.commit()
                return True
            except Exception:
                await conn.rollback()
                return False

    async def ping(self):
        """连通性测试"""
        try:
            async with self.acquire() as conn:
                await conn.ping()
            return True
        except Exception:
            return False
