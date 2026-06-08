"""日志 Schema 定义 — 表结构、INSERT SQL、索引、迁移"""

import contextlib
import json
import re

from core.base.logger import SERVICE, get_logger

log = get_logger(SERVICE, '日志')

_QUEUE_MAXSIZE = 50000


def _json_field(data, key, default=''):
    """将 dict/list 字段序列化为 JSON, 其它直接转 str"""
    v = data.get(key, default)
    return json.dumps(v, ensure_ascii=False) if isinstance(v, dict | list) else str(v)


# ==================== 日志类型定义 ====================

# 按日期分目录的类型
DAILY_TYPES = frozenset({'message', 'framework', 'error', 'lifecycle'})
# 不分日期的类型
STATIC_TYPES = frozenset({'data', 'dau', 'share', 'wakeup'})
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
            users TEXT DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS full_access_groups (
            group_id TEXT PRIMARY KEY,
            first_seen TEXT DEFAULT ''
        );
    """,
}

# INSERT SQL
_INSERTS = {
    'message': 'INSERT INTO log (timestamp, message_id, reference_id, user_id, group_id, content, raw_message, plugin_name, direction, context) VALUES (?,?,?,?,?,?,?,?,?,?)',
    'framework': 'INSERT INTO log (timestamp, content, level) VALUES (?,?,?)',
    'error': 'INSERT INTO log (timestamp, appid, module_type, module_name, content, traceback, context) VALUES (?,?,?,?,?,?,?)',
    'lifecycle': 'INSERT INTO log (timestamp, type, user_id, group_id, extra) VALUES (?,?,?,?,?)',
    'dau': """INSERT INTO log (date, active_users, active_groups, total_messages, private_messages,
              group_join_count, group_leave_count, friend_add_count, friend_remove_count,
              message_stats_detail, user_stats_detail, command_stats_detail)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(date) DO UPDATE SET
              active_users=MAX(active_users, excluded.active_users),
              active_groups=MAX(active_groups, excluded.active_groups),
              total_messages=MAX(total_messages, excluded.total_messages),
              private_messages=MAX(private_messages, excluded.private_messages),
              group_join_count=group_join_count+excluded.group_join_count,
              group_leave_count=group_leave_count+excluded.group_leave_count,
              friend_add_count=friend_add_count+excluded.friend_add_count,
              friend_remove_count=friend_remove_count+excluded.friend_remove_count""",
}

# 表索引 (类型 -> [CREATE INDEX SQL]) — 显著加速 Web 面板的聊天列表/历史查询
_INDEXES = {
    'message': [
        'CREATE INDEX IF NOT EXISTS idx_msg_group_id ON log(group_id)',
        'CREATE INDEX IF NOT EXISTS idx_msg_user_id ON log(user_id)',
        'CREATE INDEX IF NOT EXISTS idx_msg_group_agg ON log(group_id, id, timestamp)',
        'CREATE INDEX IF NOT EXISTS idx_msg_user_agg ON log(user_id, id, timestamp)',
        'CREATE INDEX IF NOT EXISTS idx_msg_message_id ON log(message_id)',
        'CREATE INDEX IF NOT EXISTS idx_msg_reference_id ON log(reference_id)',
    ],
    'lifecycle': [
        'CREATE INDEX IF NOT EXISTS idx_lc_user_id ON log(user_id)',
        'CREATE INDEX IF NOT EXISTS idx_lc_group_id ON log(group_id)',
    ],
}


# ==================== 迁移 ====================

_DATA_MIGRATIONS = [
    ('users', 'state', 'INTEGER DEFAULT 0'),
]


def _migrate_data_tables(conn):
    """为 data 库的旧表补齐缺失列"""
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


def _rebuild_message_table(conn, existing):
    """兼容旧 SQLite: 重建 message.log 表以移除废弃的 type 列。"""
    tmp = 'log_message_migrate_tmp'
    desired = [
        'id',
        'timestamp',
        'message_id',
        'reference_id',
        'user_id',
        'group_id',
        'content',
        'raw_message',
        'plugin_name',
        'direction',
        'context',
    ]
    conn.execute(f'DROP TABLE IF EXISTS {tmp}')
    conn.execute(f'ALTER TABLE log RENAME TO {tmp}')
    conn.execute(_SCHEMAS['message'])
    copy_cols = [c for c in desired if c in existing]
    if copy_cols:
        cols = ', '.join(copy_cols)
        conn.execute(f'INSERT INTO log ({cols}) SELECT {cols} FROM {tmp}')
    conn.execute(f'DROP TABLE IF EXISTS {tmp}')
    conn.commit()


def _backfill_message_reference_id(conn):
    """把旧版误写到 receive.context 的 REFIDX 搬到 reference_id。"""
    try:
        existing = {row[1] for row in conn.execute('PRAGMA table_info(log)').fetchall()}
        if 'reference_id' not in existing or 'context' not in existing:
            return
        rows = conn.execute(
            "SELECT id, reference_id, context FROM log WHERE direction = 'receive' AND context != ''"
        ).fetchall()
    except Exception:
        return

    meta_keys = {'event_type', 'message_reference_id', 'message_scene', 'msg_idx'}
    changed = False
    for row in rows:
        row_id, current_ref, raw_context = row[0], row[1] or '', row[2] or ''
        if not raw_context.lstrip().startswith('{'):
            continue
        try:
            ctx = json.loads(raw_context)
        except Exception:
            continue
        if not isinstance(ctx, dict):
            continue
        ref = ctx.get('message_reference_id') or ctx.get('msg_idx') or ''
        if ref and not current_ref:
            conn.execute('UPDATE log SET reference_id = ? WHERE id = ?', (str(ref), row_id))
            changed = True
        if ctx and set(ctx).issubset(meta_keys):
            conn.execute('UPDATE log SET context = ? WHERE id = ?', ('', row_id))
            changed = True
    if changed:
        conn.commit()
        log.info('自动迁移: message.log 已整理 receive.context 与 reference_id')


def _migrate_message_table(conn):
    """message 表专用迁移: 删除 type, 补齐 context/reference_id。"""
    try:
        existing = {row[1] for row in conn.execute('PRAGMA table_info(log)').fetchall()}
    except Exception:
        return

    desired_defs = {
        'timestamp': 'TEXT NOT NULL DEFAULT ""',
        'message_id': 'TEXT DEFAULT ""',
        'reference_id': 'TEXT DEFAULT ""',
        'user_id': 'TEXT DEFAULT ""',
        'group_id': 'TEXT DEFAULT ""',
        'content': 'TEXT DEFAULT ""',
        'raw_message': 'TEXT DEFAULT ""',
        'plugin_name': 'TEXT DEFAULT ""',
        'direction': 'TEXT DEFAULT ""',
        'context': 'TEXT DEFAULT ""',
    }
    for col_name, col_def in desired_defs.items():
        if col_name in existing:
            continue
        try:
            conn.execute(f'ALTER TABLE log ADD COLUMN {col_name} {col_def}')
            conn.commit()
            existing.add(col_name)
            log.info(f'自动迁移: message.log 表新增列 {col_name}')
        except Exception as e:
            log.warning(f'自动迁移 message.{col_name} 失败: {e}')

    if 'type' not in existing:
        _backfill_message_reference_id(conn)
        return

    try:
        conn.execute('ALTER TABLE log DROP COLUMN type')
        conn.commit()
        log.info('自动迁移: message.log 表删除废弃列 type')
    except Exception as e:
        log.warning(f'删除 message.type 失败, 尝试重建表: {e}')
        try:
            _rebuild_message_table(conn, existing)
            log.info('自动迁移: message.log 表已重建并删除 type')
        except Exception as rebuild_err:
            log.warning(f'重建 message.log 表失败: {rebuild_err}')
    _backfill_message_reference_id(conn)


def _ensure_indexes(conn, log_type):
    """为日志表创建必要索引 (幂等)"""
    for sql in _INDEXES.get(log_type, ()):
        try:
            conn.execute(sql)
        except Exception as e:
            log.warning(f'创建索引失败 ({log_type}): {e}')
    with contextlib.suppress(Exception):
        conn.commit()
