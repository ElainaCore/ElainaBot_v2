#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""可选模块: MySQL 异步连接池

基于 aiomysql 的连接池, 提供 execute/query/fetch/upsert 等便捷方法。

用法 (插件中):
    pool = bot.module_manager.get("mysql_pool")
    if pool and pool.is_available():
        await pool.execute("INSERT INTO t VALUES (%s)", (1,))
        rows = await pool.fetch_all("SELECT * FROM t")
        await pool.upsert("users", {"id": 1, "name": "x"}, ["id"])

配置 (data/config.yaml):
    host: 127.0.0.1
    port: 3306
    user: root
    password: ""
    database: elaina
    minsize: 2
    maxsize: 20
    charset: utf8mb4
"""

import asyncio
from core.base.logger import get_logger, EXTENSION

log = get_logger(EXTENSION, "MySQL连接池")

_instance = None
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
    'autocommit': '是否自动提交事务',
}


# ==================== 模块入口 ====================

async def setup(ctx):
    global _instance
    cfg = ctx.ensure_config(_DEFAULTS, comments=_COMMENTS)
    _instance = MySQLPool(cfg)
    await _instance.initialize()
    return _instance


async def teardown():
    global _instance
    if _instance:
        await _instance.close()
        _instance = None


# ==================== 连接池 ====================

class MySQLPool:
    """MySQL 异步连接池封装"""

    __slots__ = ('_cfg', '_pool', '_available')

    def __init__(self, cfg):
        self._cfg = cfg
        self._pool = None
        self._available = False

    async def initialize(self):
        try:
            import aiomysql
        except ImportError:
            log.error("aiomysql 未安装, MySQL 连接池禁用 (pip install aiomysql)")
            return
        if not self._cfg.get('database'):
            log.warning("未配置 database, 跳过初始化")
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
            log.info(f"✅ 连接池就绪 [{self._cfg['host']}:{self._cfg['port']}/{self._cfg['database']}]")
        except Exception as e:
            log.error(f"初始化失败: {e}")
            self._available = False

    def is_available(self):
        return self._available and self._pool is not None

    async def close(self):
        if self._pool:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None
        self._available = False

    # ---------- 连接 ----------

    def acquire(self):
        """获取连接 (用作 async with):

            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(...)
        """
        if not self.is_available():
            raise RuntimeError("MySQL 连接池不可用")
        return self._pool.acquire()

    # ---------- 便捷方法 ----------

    async def execute(self, sql, params=None):
        """执行写操作, 返回受影响行数"""
        if not self.is_available():
            return 0
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                rows = await cur.execute(sql, params)
                if not conn.get_autocommit():
                    await conn.commit()
                return rows

    async def execute_many(self, sql, params_list):
        """批量执行"""
        if not self.is_available() or not params_list:
            return 0
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                rows = await cur.executemany(sql, params_list)
                if not conn.get_autocommit():
                    await conn.commit()
                return rows

    async def fetch_one(self, sql, params=None):
        """查询单行, 返回 dict 或 None"""
        if not self.is_available():
            return None
        import aiomysql
        async with self._pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, params)
                return await cur.fetchone()

    async def fetch_all(self, sql, params=None):
        """查询多行, 返回 [dict, ...]"""
        if not self.is_available():
            return []
        import aiomysql
        async with self._pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, params)
                return list(await cur.fetchall())

    async def fetch_value(self, sql, params=None, default=None):
        """查询单个值"""
        if not self.is_available():
            return default
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, params)
                row = await cur.fetchone()
                return row[0] if row else default

    async def upsert(self, table, data, conflict_columns):
        """INSERT ... ON DUPLICATE KEY UPDATE

        Args:
            table:            表名
            data:             {column: value}
            conflict_columns: 冲突列名列表 (用于排除 update 子句)
        """
        if not self.is_available() or not data:
            return 0
        cols = list(data.keys())
        placeholders = ', '.join(['%s'] * len(cols))
        update_cols = [c for c in cols if c not in conflict_columns]
        sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
        if update_cols:
            update_clause = ', '.join(f"{c}=VALUES({c})" for c in update_cols)
            sql += f" ON DUPLICATE KEY UPDATE {update_clause}"
        return await self.execute(sql, list(data.values()))

    async def table_exists(self, table_name):
        """检查表是否存在"""
        row = await self.fetch_one(
            "SELECT COUNT(*) AS c FROM information_schema.tables "
            "WHERE table_schema=DATABASE() AND table_name=%s",
            (table_name,))
        return bool(row and row.get('c'))

    async def ping(self):
        """连通性测试"""
        try:
            async with self._pool.acquire() as conn:
                await conn.ping()
            return True
        except Exception:
            return False
