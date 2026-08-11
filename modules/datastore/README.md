# Datastore 模块接入文档

Datastore 为插件提供统一的异步 MySQL 连接池和 Redis 客户端。模块由框架统一创建、复用和关闭，插件不应自行创建连接池。

## 获取模块

```python
from core.application import get_app

app = get_app()
ds = app.module_manager.get("datastore") if app else None

if ds and ds.mysql_available():
    row = await ds.mysql.fetch_one(
        "SELECT id, name FROM users WHERE id=%s",
        (user_id,),
    )
```

`module_manager.get("datastore")` 在模块未启用或初始化失败时返回 `None`。`ds.mysql` 和 `ds.redis` 在对应连接不可用时也返回 `None`，建议同时使用 `*_available()` 判断。

配置文件由模块自动生成在 `modules/datastore/data/`：

| 文件 | 作用 |
| --- | --- |
| `config.yaml` | `mysql_enabled`、`redis_enabled` 总开关 |
| `mysql.yaml` | MySQL 连接池参数 |
| `redis.yaml` | Redis 连接参数 |

启用 MySQL 需要 `aiomysql` 和有效的 `database`；启用 Redis 需要 `redis>=5.0`。依赖会在启用模块时按 `requirements.txt` 安装。

## MySQL API

通过 `ds.mysql` 获取 `MySQLPool`。所有数据库方法都是异步方法。

| 调用 | 返回值 | 说明 |
| --- | --- | --- |
| `await mysql.execute(sql, params=None)` | `int` | 执行写操作，返回受影响行数；不可用时返回 `0` |
| `await mysql.execute_many(sql, params_list)` | `int` | 批量执行 |
| `await mysql.fetch_one(sql, params=None)` | `dict \| None` | 查询一行，键名为列名 |
| `await mysql.fetch_all(sql, params=None)` | `list[dict]` | 查询多行 |
| `await mysql.fetch_value(sql, params=None, default=None)` | `Any` | 查询第一行第一列 |
| `await mysql.upsert(table, data, conflict_columns)` | `int` | `INSERT ... ON DUPLICATE KEY UPDATE`；`table` 和列名必须来自可信代码 |
| `await mysql.table_exists(table_name)` | `bool` | 检查当前数据库中的表 |
| `await mysql.execute_transaction(sql_list)` | `bool` | 原子执行 `[{'sql': ..., 'params': ...}, ...]` |
| `await mysql.ping()` | `bool` | 连通性测试 |
| `mysql.acquire()` | async context manager | 获取连接，必须配合 `async with` 使用 |

```python
mysql = ds.mysql
if mysql:
    await mysql.execute(
        "CREATE TABLE IF NOT EXISTS plugin_scores (user_id VARCHAR(64) PRIMARY KEY, score INT NOT NULL)",
    )
    await mysql.upsert(
        "plugin_scores",
        {"user_id": str(event.user_id), "score": 1},
        ["user_id"],
    )
    score = await mysql.fetch_value(
        "SELECT score FROM plugin_scores WHERE user_id=%s",
        (str(event.user_id),),
        default=0,
    )
```

参数使用数据库驱动的占位符（MySQL 为 `%s`），不要把用户输入拼接进 SQL。`autocommit` 默认开启；需要多个操作一起提交时使用 `execute_transaction()`。

## Redis API

通过 `ds.redis` 获取 `RedisPool`。Redis 方法对连接异常做了降级处理，失败时返回各方法对应的安全默认值；需要严格感知错误时使用 `get_client()` 获取底层 `redis.asyncio.Redis` 并自行处理异常。

### 基础 Key

`get(key, default=None)`、`set(key, value, ex=None, px=None, nx=False, xx=False)`、`delete(*keys)`、`exists(*keys)`、`expire(key, seconds)`、`expireat(key, when)`、`ttl(key)`、`incr(key, amount=1)`、`decr(key, amount=1)`、`keys(pattern='*')`。

```python
redis = ds.redis
if redis:
    await redis.set(f"my_plugin:{event.user_id}:seen", "1", ex=3600)
    seen = await redis.get(f"my_plugin:{event.user_id}:seen", default="0")
```

### Hash、List、Set、Sorted Set

- Hash：`hget`、`hset`、`hdel`、`hgetall`、`hexists`、`hincrby`、`hkeys`、`hlen`
- List：`lpush`、`rpush`、`lpop`、`rpop`、`lrange`、`llen`
- Set：`sadd`、`srem`、`smembers`、`sismember`、`scard`
- Sorted Set：`zadd`、`zrem`、`zrange`、`zrevrange`、`zscore`、`zincrby`、`zcard`
- 扫描：`scan_iter(match=None, count=None)` 返回异步迭代器

### Lua、Pipeline、管理

`eval(script, numkeys, *keys_and_args)`、`evalsha(sha, numkeys, *keys_and_args)`、`script_load(script)`、`pipeline(transaction=True)`、`flushdb(asynchronous=False)`、`info(section=None)`、`dbsize()`、`ping()`。

```python
redis = ds.redis
if redis:
    async with redis.pipeline(transaction=True) as pipe:
        pipe.incrby("my_plugin:total", 1)
        pipe.expire("my_plugin:total", 86400)
        await pipe.execute()
```

`flushdb()` 会清空当前 Redis 数据库，插件应避免调用，除非这是明确的管理操作。建议为插件 key 统一加前缀，避免与其他插件冲突。

## 生命周期注意事项

- 不要在插件 `on_load` 中缓存底层连接对象；模块 reload 后连接池可能被替换。
- 每次使用前检查模块和对应后端是否可用。
- 插件卸载时不需要关闭 `ds.mysql` 或 `ds.redis`，由 Datastore 模块负责。
