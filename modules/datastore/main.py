#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""数据存储引擎 — MySQL + Redis 统一管理模块

通过配置文件独立开关两个数据库。

插件中获取:
    ds = bot.module_manager.get("datastore")

    # MySQL
    if ds.mysql_available():
        await ds.mysql.execute("INSERT INTO t VALUES (%s)", (1,))
        row = await ds.mysql.fetch_one("SELECT * FROM t WHERE id=%s", (1,))

    # Redis
    if ds.redis_available():
        await ds.redis.set("key", "value", ex=60)
        v = await ds.redis.get("key")

配置文件 (data/ 下自动生成):
    config.yaml  → mysql_enabled / redis_enabled 开关
    mysql.yaml   → MySQL 连接参数
    redis.yaml   → Redis 连接参数
"""

__module_meta__ = {
    'name': '数据存储引擎',
    'description': 'MySQL + Redis 统一数据存储, 异步连接池与完整 CRUD / 缓存操作',
    'version': '1.0.0',
    'author': 'ElainaBot',
}

from core.base.logger import get_logger, EXTENSION

log = get_logger(EXTENSION, "数据存储引擎")

_instance = None

_DEFAULTS = {
    'mysql_enabled': True,
    'redis_enabled': False,
}

_COMMENTS = {
    'mysql_enabled': '是否启用 MySQL 连接池',
    'redis_enabled': '是否启用 Redis 连接池',
}


# ==================== 模块入口 ====================

async def setup(ctx):
    global _instance
    cfg = ctx.ensure_config(_DEFAULTS, comments=_COMMENTS)

    from modules.datastore.mysql.main import MySQLPool, _DEFAULTS as MYSQL_DEFAULTS, _COMMENTS as MYSQL_COMMENTS
    from modules.datastore.redis.main import RedisPool, _DEFAULTS as REDIS_DEFAULTS, _COMMENTS as REDIS_COMMENTS

    mysql_inst = None
    redis_inst = None

    mysql_cfg = ctx.ensure_config(MYSQL_DEFAULTS, filename='mysql.yaml', comments=MYSQL_COMMENTS)
    redis_cfg = ctx.ensure_config(REDIS_DEFAULTS, filename='redis.yaml', comments=REDIS_COMMENTS)

    if cfg.get('mysql_enabled', True):
        mysql_inst = MySQLPool(mysql_cfg, log)
        await mysql_inst.initialize()

    if cfg.get('redis_enabled', False):
        redis_inst = RedisPool(redis_cfg, log)
        await redis_inst.initialize()

    _instance = DataStore(mysql_inst, redis_inst)

    parts = []
    if mysql_inst and mysql_inst.is_available():
        parts.append("MySQL ✅")
    elif cfg.get('mysql_enabled'):
        parts.append("MySQL ❌")
    else:
        parts.append("MySQL 关闭")

    if redis_inst and redis_inst.is_available():
        parts.append("Redis ✅")
    elif cfg.get('redis_enabled'):
        parts.append("Redis ❌")
    else:
        parts.append("Redis 关闭")

    log.info(f"数据存储引擎: {' | '.join(parts)}")
    return _instance


async def teardown():
    global _instance
    if _instance:
        await _instance.close()
        _instance = None


# ==================== DataStore ====================

class DataStore:
    """统一数据存储 — 通过 .mysql / .redis 属性访问子组件"""

    __slots__ = ('_mysql', '_redis')

    def __init__(self, mysql_pool, redis_pool):
        self._mysql = mysql_pool
        self._redis = redis_pool

    @property
    def mysql(self):
        """MySQLPool 实例, 不可用时返回 None"""
        return self._mysql if self._mysql and self._mysql.is_available() else None

    @property
    def redis(self):
        """RedisPool 实例, 不可用时返回 None"""
        return self._redis if self._redis and self._redis.is_available() else None

    def mysql_available(self):
        return self._mysql is not None and self._mysql.is_available()

    def redis_available(self):
        return self._redis is not None and self._redis.is_available()

    async def close(self):
        if self._mysql:
            await self._mysql.close()
        if self._redis:
            await self._redis.close()
