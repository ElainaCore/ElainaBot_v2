"""日志 Schema 定义 — 表结构、INSERT SQL、索引、迁移"""

import contextlib
import json
import re

from core.base.logger import SERVICE, get_logger
from core.message.response import raw_response_text

log = get_logger(SERVICE, '日志')

_QUEUE_MAXSIZE = 50000


def _json_field(data, key, default=''):
    """将 dict/list 字段序列化为 JSON, 其它直接转 str"""
    v = data.get(key, default)
    raw = raw_response_text(v)
    if raw is not None:
        return raw
    return json.dumps(v, ensure_ascii=False) if isinstance(v, dict | list) else str(v)


# ==================== 日志类型定义 ====================

# 按日期分目录的类型
DAILY_TYPES = frozenset({'message', 'framework', 'error', 'lifecycle'})
# 不分日期的类型
STATIC_TYPES = frozenset({'data', 'dau', 'share', 'wakeup', 'subscribe'})
ALL_TYPES = DAILY_TYPES | STATIC_TYPES

# DAU 表结构 (公开常量, dau.py 复用)
DAU_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT UNIQUE NOT NULL,
        active_users INTEGER DEFAULT 0,
        active_groups INTEGER DEFAULT 0,
        total_messages INTEGER DEFAULT 0,
        private_messages INTEGER DEFAULT 0,
        received_messages INTEGER DEFAULT 0,
        sent_messages INTEGER DEFAULT 0,
        group_join_count INTEGER DEFAULT 0,
        group_leave_count INTEGER DEFAULT 0,
        friend_add_count INTEGER DEFAULT 0,
        friend_remove_count INTEGER DEFAULT 0,
        message_stats_detail TEXT DEFAULT '',
        user_stats_detail TEXT DEFAULT '',
        command_stats_detail TEXT DEFAULT ''
    )
"""

# 表结构 (类型 -> CREATE TABLE SQL)
_SCHEMAS = {
    'message': """
        CREATE TABLE IF NOT EXISTS log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            message_id TEXT DEFAULT '',
            reference_id TEXT DEFAULT '',
            user_id TEXT DEFAULT '',
            group_id TEXT DEFAULT '',
            content TEXT DEFAULT '',
            raw_message TEXT DEFAULT '',
            plugin_name TEXT DEFAULT '',
            direction TEXT DEFAULT '',
            at_bot INTEGER DEFAULT 1,
            context TEXT DEFAULT ''
        )
    """,
    'framework': """
        CREATE TABLE IF NOT EXISTS log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            content TEXT DEFAULT '',
            level TEXT DEFAULT 'INFO'
        )
    """,
    'error': """
        CREATE TABLE IF NOT EXISTS log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            appid TEXT DEFAULT '0000',
            module_type TEXT DEFAULT '',
            module_name TEXT DEFAULT '',
            content TEXT DEFAULT '',
            traceback TEXT DEFAULT '',
            context TEXT DEFAULT ''
        )
    """,
    'dau': DAU_TABLE_SQL,
    'share': """
        CREATE TABLE IF NOT EXISTS log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            openid TEXT UNIQUE NOT NULL,
            referrals TEXT DEFAULT '{}',
            created_at TEXT,
            updated_at TEXT
        )
    """,
    'wakeup': """
        CREATE TABLE IF NOT EXISTS log (
            openid TEXT PRIMARY KEY,
            last_msg_date TEXT NOT NULL,
            wakeup_stage INTEGER DEFAULT 0,
            last_wakeup_date TEXT,
            updated_at TEXT
        )
    """,
    'subscribe': """
        CREATE TABLE IF NOT EXISTS log (
            template_id TEXT NOT NULL,
            target_id TEXT NOT NULL,
            target_type TEXT DEFAULT 'group',
            sub_type TEXT DEFAULT 'permanent',
            subscribe_id TEXT DEFAULT '',
            status INTEGER DEFAULT 1,
            subscribe_ts INTEGER DEFAULT 0,
            update_ts INTEGER DEFAULT 0,
            updated_at TEXT DEFAULT '',
            PRIMARY KEY (template_id, target_id)
        )
    """,
    'lifecycle': """
        CREATE TABLE IF NOT EXISTS log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            type TEXT DEFAULT '',
            user_id TEXT DEFAULT '',
            group_id TEXT DEFAULT '',
            extra TEXT DEFAULT ''
        )
    """,
    'data': """
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            name TEXT DEFAULT '',
            state INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS members (
            user_id TEXT PRIMARY KEY
        );
        CREATE TABLE IF NOT EXISTS groups_users (
            group_id TEXT PRIMARY KEY,
            users TEXT DEFAULT '[]',
            in_group INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS full_access_groups (
            group_id TEXT PRIMARY KEY,
            first_seen TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS group_bot_admin (
            group_id TEXT PRIMARY KEY,
            updated_at TEXT DEFAULT ''
        );
    """,
}

# INSERT SQL
_INSERTS = {
    'message': 'INSERT INTO log (timestamp, message_id, reference_id, user_id, group_id, content, raw_message, plugin_name, direction, at_bot, context) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
    'framework': 'INSERT INTO log (timestamp, content, level) VALUES (?,?,?)',
    'error': 'INSERT INTO log (timestamp, appid, module_type, module_name, content, traceback, context) VALUES (?,?,?,?,?,?,?)',
    'lifecycle': 'INSERT INTO log (timestamp, type, user_id, group_id, extra) VALUES (?,?,?,?,?)',
    'dau': """INSERT INTO log (date, active_users, active_groups, total_messages, private_messages,
              received_messages, sent_messages,
              group_join_count, group_leave_count, friend_add_count, friend_remove_count,
              message_stats_detail, user_stats_detail, command_stats_detail)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(date) DO UPDATE SET
              active_users=MAX(active_users, excluded.active_users),
              active_groups=MAX(active_groups, excluded.active_groups),
              total_messages=MAX(total_messages, excluded.total_messages),
              private_messages=MAX(private_messages, excluded.private_messages),
              received_messages=MAX(received_messages, excluded.received_messages),
              sent_messages=MAX(sent_messages, excluded.sent_messages),
              group_join_count=group_join_count+excluded.group_join_count,
              group_leave_count=group_leave_count+excluded.group_leave_count,
              friend_add_count=friend_add_count+excluded.friend_add_count,
              friend_remove_count=friend_remove_count+excluded.friend_remove_count""",
}

# 表索引
_INDEXES = {
    'message': [
        'CREATE INDEX IF NOT EXISTS idx_msg_group_id ON log(group_id)',
        'CREATE INDEX IF NOT EXISTS idx_msg_user_id ON log(user_id)',
        'CREATE INDEX IF NOT EXISTS idx_msg_group_agg ON log(group_id, id, timestamp)',
        'CREATE INDEX IF NOT EXISTS idx_msg_user_agg ON log(user_id, id, timestamp)',
        'CREATE INDEX IF NOT EXISTS idx_msg_message_id ON log(message_id)',
        'CREATE INDEX IF NOT EXISTS idx_msg_timestamp ON log(timestamp)',
        'CREATE INDEX IF NOT EXISTS idx_msg_reference_id ON log(reference_id)',
        # 覆盖索引
        'CREATE INDEX IF NOT EXISTS idx_msg_group_cover ON log(group_id, timestamp, id, content)',
        'CREATE INDEX IF NOT EXISTS idx_msg_user_cover ON log(user_id, group_id, timestamp, id, content)',

        'CREATE INDEX IF NOT EXISTS idx_msg_direction ON log(direction, user_id, group_id, content)',
        'CREATE INDEX IF NOT EXISTS idx_msg_plugin_name ON log(plugin_name)',
        # 统计覆盖索引: 计数/峰值/排行只扫索引, 避免读取 raw_message 大字段
        'CREATE INDEX IF NOT EXISTS idx_msg_stats_cover ON log(direction, at_bot, group_id, user_id, timestamp)',
        'CREATE INDEX IF NOT EXISTS idx_msg_stats_user ON log(user_id, direction, at_bot)',
        'CREATE INDEX IF NOT EXISTS idx_msg_stats_group ON log(group_id, direction, at_bot)',
    ],
    'lifecycle': [
        'CREATE INDEX IF NOT EXISTS idx_lc_user_id ON log(user_id)',
        'CREATE INDEX IF NOT EXISTS idx_lc_group_id ON log(group_id)',
        'CREATE INDEX IF NOT EXISTS idx_lc_type ON log(type)',
    ],
}


# ==================== 迁移 ====================

_DATA_MIGRATIONS = [
    ('users', 'state', 'INTEGER DEFAULT 0'),
    ('groups_users', 'in_group', 'INTEGER DEFAULT 1'),
]

# data 库 schema 版本 (PRAGMA user_version); 新增迁移时 +1
_DATA_SCHEMA_VERSION = 2


def _migrate_data_tables(conn):
    """为 data 库的旧表补齐缺失列 (按 user_version 版本号跳过已迁移库)"""
    try:
        if conn.execute('PRAGMA user_version').fetchone()[0] >= _DATA_SCHEMA_VERSION:
            return
    except Exception:
        pass
    for table, col, col_def in _DATA_MIGRATIONS:
        try:
            existing = {row[1] for row in conn.execute(f'PRAGMA table_info({table})').fetchall()}
            if col in existing:
                continue
            conn.execute(f'ALTER TABLE {table} ADD COLUMN {col} {col_def}')
            conn.commit()
            log.info(f'自动迁移: {table} 表新增列 {col}')
        except Exception as e:
            log.warning(f'迁移列 {table}.{col} 失败: {e}')
    with contextlib.suppress(Exception):
        conn.execute('ALTER TABLE full_access_groups DROP COLUMN last_seen')
        conn.commit()
    with contextlib.suppress(Exception):
        conn.execute(f'PRAGMA user_version = {_DATA_SCHEMA_VERSION}')
        conn.commit()
    with contextlib.suppress(Exception):
        conn.execute(f'PRAGMA user_version = {_DATA_SCHEMA_VERSION}')
        conn.commit()


def _migrate_missing_columns(conn, log_type):
    """为旧表补齐缺失列"""
    schema = _SCHEMAS.get(log_type)
    if not schema or log_type == 'data':
        return
    try:
        existing = {row[1] for row in conn.execute('PRAGMA table_info(log)').fetchall()}
    except Exception:
        return
    col_pattern = re.compile(r'^\s+(\w+)\s+(TEXT|INTEGER|REAL)(.*)$', re.MULTILINE)
    for m in col_pattern.finditer(schema):
        col_name = m.group(1)
        if col_name in existing or col_name == 'id':
            continue
        col_def = f'{m.group(2)}{m.group(3).rstrip().rstrip(",")}'
        try:
            conn.execute(f'ALTER TABLE log ADD COLUMN {col_name} {col_def}')
            conn.commit()
            log.info(f'自动迁移: log 表新增列 {col_name} ({log_type})')
        except Exception as e:
            log.warning(f'自动迁移列 {col_name} 失败: {e}')


def _ensure_indexes(conn, log_type):
    """为日志表创建必要索引 (幂等)"""
    for sql in _INDEXES.get(log_type, ()):
        try:
            conn.execute(sql)
        except Exception as e:
            log.warning(f'创建索引失败 ({log_type}): {e}')
    with contextlib.suppress(Exception):
        conn.commit()


_INDEX_NAME_RE = re.compile(r'CREATE INDEX IF NOT EXISTS (\w+)', re.IGNORECASE)


def _missing_index_sqls(conn, log_type):
    """返回该库缺失的索引建表语句列表"""
    try:
        existing = {row[1] for row in conn.execute("PRAGMA index_list('log')").fetchall()}
    except Exception:
        return []
    missing = []
    for sql in _INDEXES.get(log_type, ()):
        m = _INDEX_NAME_RE.search(sql)
        if m and m.group(1) not in existing:
            missing.append(sql)
    return missing
