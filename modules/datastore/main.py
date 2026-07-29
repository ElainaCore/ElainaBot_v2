#!/usr/bin/env python
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
    'version': '1.1.0',
    'author': 'ElainaBot',
}

from core.base.logger import EXTENSION, get_logger

log = get_logger(EXTENSION, '数据存储引擎')

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

    from modules.datastore.mysql import main as mysql_mod
    from modules.datastore.redis import main as redis_mod

    mysql_inst = None
    redis_inst = None

    mysql_cfg = ctx.ensure_config(mysql_mod._DEFAULTS, filename='mysql.yaml', comments=mysql_mod._COMMENTS)
    redis_cfg = ctx.ensure_config(redis_mod._DEFAULTS, filename='redis.yaml', comments=redis_mod._COMMENTS)

    # 复用跨重载存活的连接池: 配置未变则零重连, 变了才平滑切换, 重载期间服务不中断
    if cfg.get('mysql_enabled', True):
        mysql_inst = await mysql_mod.get_pool(mysql_cfg, log)
    else:
        mysql_mod.schedule_close()

    if cfg.get('redis_enabled', False):
        redis_inst = await redis_mod.get_pool(redis_cfg, log)
    else:
        redis_mod.schedule_close()

    _instance = DataStore(mysql_inst, redis_inst)

    parts = []
    if mysql_inst and mysql_inst.is_available():
        parts.append(f'MySQL ✅ [{mysql_cfg["host"]}:{mysql_cfg["port"]}/{mysql_cfg["database"]}]')
    elif cfg.get('mysql_enabled'):
        parts.append('MySQL ❌')
    else:
        parts.append('MySQL 关闭')

    if redis_inst and redis_inst.is_available():
        parts.append(f'Redis ✅ [{redis_cfg["host"]}:{redis_cfg["port"]}/{redis_cfg["db"]}]')
    elif cfg.get('redis_enabled'):
        parts.append('Redis ❌')
    else:
        parts.append('Redis 关闭')

    log.info(f'{" | ".join(parts)}')
    return _instance


async def teardown():
    """延迟关闭连接池: 若是配置重载, setup 会在延迟内接管现有池实现平滑热更;
    若是真正 disable, 延迟到期后连接池自动关闭。
    """
    global _instance
    from modules.datastore.mysql import main as mysql_mod
    from modules.datastore.redis import main as redis_mod
    mysql_mod.schedule_close()
    redis_mod.schedule_close()
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
