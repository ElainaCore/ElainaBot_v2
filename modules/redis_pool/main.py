#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""可选模块: Redis 异步客户端

基于 redis.asyncio (redis-py 5.x), 提供常用键/Hash/List/Set/ZSet 操作。
所有操作失败返回默认值, 不抛异常 (调用方无需处理 None)。

用法 (插件中):
    rds = bot.module_manager.get("redis_pool")
    if rds and rds.is_available():
        await rds.set("k", "v", ex=60)
        v = await rds.get("k")

配置 (data/config.yaml):
    host: 127.0.0.1
    port: 6379
    password: ""
    db: 0
    max_connections: 50
    socket_timeout: 5
    decode_responses: true
"""

from core.base.logger import get_logger, EXTENSION

log = get_logger(EXTENSION, "Redis连接池")

_instance = None
_DEFAULTS = {
    'host': '127.0.0.1',
    'port': 6379,
    'password': '',
    'db': 0,
    'max_connections': 50,
    'socket_timeout': 5,
    'socket_connect_timeout': 5,
    'health_check_interval': 30,
    'decode_responses': True,
}

_COMMENTS = {
    'host': 'Redis 服务器地址',
    'port': 'Redis 端口号',
    'password': '连接密码, 无密码留空',
    'db': '数据库编号 (0-15)',
    'max_connections': '最大连接数',
    'socket_timeout': '读写超时 (秒)',
    'socket_connect_timeout': '连接超时 (秒)',
    'health_check_interval': '健康检查间隔 (秒)',
    'decode_responses': '是否自动解码响应为字符串',
}


# ==================== 模块入口 ====================

async def setup(ctx):
    global _instance
    cfg = ctx.ensure_config(_DEFAULTS, comments=_COMMENTS)
    _instance = RedisPool(cfg)
    await _instance.initialize()
    return _instance


async def teardown():
    global _instance
    if _instance:
        await _instance.close()
        _instance = None


# ==================== Redis 客户端 ====================

class RedisPool:
    """Redis 异步客户端封装"""

    __slots__ = ('_cfg', '_client', '_available')

    def __init__(self, cfg):
        self._cfg = cfg
        self._client = None
        self._available = False

    async def initialize(self):
        try:
            from redis.asyncio import Redis, ConnectionPool
        except ImportError:
            log.error("redis 未安装 (pip install redis>=5.0)")
            return
        try:
            password = self._cfg.get('password') or None
            pool = ConnectionPool(
                host=self._cfg.get('host', '127.0.0.1'),
                port=int(self._cfg.get('port', 6379)),
                password=password,
                db=int(self._cfg.get('db', 0)),
                max_connections=int(self._cfg.get('max_connections', 50)),
                socket_timeout=int(self._cfg.get('socket_timeout', 5)),
                socket_connect_timeout=int(self._cfg.get('socket_connect_timeout', 5)),
                health_check_interval=int(self._cfg.get('health_check_interval', 30)),
                decode_responses=bool(self._cfg.get('decode_responses', True)),
            )
            self._client = Redis(connection_pool=pool)
            await self._client.ping()
            self._available = True
            log.info(f"✅ Redis 连接成功 [{self._cfg['host']}:{self._cfg['port']}/{self._cfg['db']}]")
        except Exception as e:
            log.error(f"Redis 初始化失败: {e}")
            self._client = None
            self._available = False

    def is_available(self):
        return self._available and self._client is not None

    def get_client(self):
        """获取底层 redis.asyncio.Redis 实例 (高级用法)"""
        return self._client if self.is_available() else None

    async def close(self):
        if self._client:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None
        self._available = False

    # ==================== 内部 ====================

    async def _safe(self, op_name, coro, default=None, key=None):
        if not self.is_available():
            return default
        try:
            return await coro
        except Exception as e:
            log.warning(f"{op_name} 失败 [{key}]: {e}" if key else f"{op_name} 失败: {e}")
            return default

    # ==================== 基础操作 ====================

    async def get(self, key, default=None):
        if not self.is_available():
            return default
        v = await self._safe("GET", self._client.get(key), default=default, key=key)
        return v if v is not None else default

    async def set(self, key, value, ex=None, px=None, nx=False, xx=False):
        return bool(await self._safe(
            "SET", self._client.set(key, value, ex=ex, px=px, nx=nx, xx=xx),
            default=False, key=key))

    async def delete(self, *keys):
        if not keys:
            return 0
        return await self._safe("DELETE", self._client.delete(*keys), default=0)

    async def exists(self, *keys):
        if not keys:
            return 0
        return await self._safe("EXISTS", self._client.exists(*keys), default=0)

    async def expire(self, key, seconds):
        return bool(await self._safe("EXPIRE", self._client.expire(key, seconds),
                                     default=False, key=key))

    async def ttl(self, key):
        return await self._safe("TTL", self._client.ttl(key), default=-2, key=key)

    async def incr(self, key, amount=1):
        return await self._safe("INCR", self._client.incrby(key, amount),
                                default=None, key=key)

    async def decr(self, key, amount=1):
        return await self._safe("DECR", self._client.decrby(key, amount),
                                default=None, key=key)

    async def keys(self, pattern='*'):
        return await self._safe("KEYS", self._client.keys(pattern), default=[])

    # ==================== Hash ====================

    async def hget(self, name, key, default=None):
        if not self.is_available():
            return default
        v = await self._safe("HGET", self._client.hget(name, key), default=default, key=f"{name}.{key}")
        return v if v is not None else default

    async def hset(self, name, key=None, value=None, mapping=None):
        return await self._safe(
            "HSET", self._client.hset(name, key=key, value=value, mapping=mapping),
            default=0, key=name)

    async def hdel(self, name, *keys):
        if not keys:
            return 0
        return await self._safe("HDEL", self._client.hdel(name, *keys), default=0, key=name)

    async def hgetall(self, name):
        return await self._safe("HGETALL", self._client.hgetall(name), default={}, key=name)

    async def hexists(self, name, key):
        return bool(await self._safe("HEXISTS", self._client.hexists(name, key),
                                     default=False, key=f"{name}.{key}"))

    async def hincrby(self, name, key, amount=1):
        return await self._safe("HINCRBY", self._client.hincrby(name, key, amount),
                                default=None, key=f"{name}.{key}")

    async def hkeys(self, name):
        return await self._safe("HKEYS", self._client.hkeys(name), default=[], key=name)

    async def hlen(self, name):
        return await self._safe("HLEN", self._client.hlen(name), default=0, key=name)

    # ==================== List ====================

    async def lpush(self, name, *values):
        if not values:
            return 0
        return await self._safe("LPUSH", self._client.lpush(name, *values), default=0, key=name)

    async def rpush(self, name, *values):
        if not values:
            return 0
        return await self._safe("RPUSH", self._client.rpush(name, *values), default=0, key=name)

    async def lpop(self, name, count=None):
        return await self._safe("LPOP", self._client.lpop(name, count), default=None, key=name)

    async def rpop(self, name, count=None):
        return await self._safe("RPOP", self._client.rpop(name, count), default=None, key=name)

    async def lrange(self, name, start, end):
        return await self._safe("LRANGE", self._client.lrange(name, start, end),
                                default=[], key=name)

    async def llen(self, name):
        return await self._safe("LLEN", self._client.llen(name), default=0, key=name)

    # ==================== Set ====================

    async def sadd(self, name, *values):
        if not values:
            return 0
        return await self._safe("SADD", self._client.sadd(name, *values), default=0, key=name)

    async def srem(self, name, *values):
        if not values:
            return 0
        return await self._safe("SREM", self._client.srem(name, *values), default=0, key=name)

    async def smembers(self, name):
        return await self._safe("SMEMBERS", self._client.smembers(name),
                                default=set(), key=name)

    async def sismember(self, name, value):
        return bool(await self._safe("SISMEMBER", self._client.sismember(name, value),
                                     default=False, key=name))

    async def scard(self, name):
        return await self._safe("SCARD", self._client.scard(name), default=0, key=name)

    # ==================== Sorted Set ====================

    async def zadd(self, name, mapping):
        return await self._safe("ZADD", self._client.zadd(name, mapping), default=0, key=name)

    async def zrem(self, name, *values):
        if not values:
            return 0
        return await self._safe("ZREM", self._client.zrem(name, *values), default=0, key=name)

    async def zrange(self, name, start, end, withscores=False, desc=False):
        coro = self._client.zrevrange(name, start, end, withscores=withscores) \
            if desc else self._client.zrange(name, start, end, withscores=withscores)
        return await self._safe("ZRANGE", coro, default=[], key=name)

    async def zcard(self, name):
        return await self._safe("ZCARD", self._client.zcard(name), default=0, key=name)

    async def zscore(self, name, value):
        return await self._safe("ZSCORE", self._client.zscore(name, value),
                                default=None, key=name)
