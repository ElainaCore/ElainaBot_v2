"""消息落库时增量维护精确统计；旧日志按主键分批补算，不在查询时扫消息表。"""

from collections import Counter

_METRICS = ('total_messages', 'private_messages', 'received_messages', 'sent_messages')
_ENTITY_KINDS = ('user', 'group', 'command')
_BACKFILL_BATCH = 2000


def init_message_stats(conn):
    """与消息库共用事务和生命周期；初始化只读取最大主键。"""
    conn.execute('''
        CREATE TABLE IF NOT EXISTS message_stats (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            backfill_until INTEGER NOT NULL,
            backfill_cursor INTEGER NOT NULL DEFAULT 0,
            total_messages INTEGER NOT NULL DEFAULT 0,
            private_messages INTEGER NOT NULL DEFAULT 0,
            received_messages INTEGER NOT NULL DEFAULT 0,
            sent_messages INTEGER NOT NULL DEFAULT 0,
            active_users INTEGER NOT NULL DEFAULT 0,
            active_groups INTEGER NOT NULL DEFAULT 0
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS message_stats_entities (
            kind TEXT NOT NULL,
            entity TEXT NOT NULL,
            message_count INTEGER NOT NULL,
            first_seen TEXT NOT NULL,
            PRIMARY KEY (kind, entity)
        ) WITHOUT ROWID
    ''')
    conn.execute('''
        CREATE INDEX IF NOT EXISTS idx_message_stats_rank
        ON message_stats_entities(kind, message_count DESC, entity)
    ''')
    conn.execute('''
        CREATE INDEX IF NOT EXISTS idx_message_stats_first_seen
        ON message_stats_entities(kind, first_seen)
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS message_stats_minutes (
            minute TEXT PRIMARY KEY,
            total_messages INTEGER NOT NULL,
            private_messages INTEGER NOT NULL,
            received_messages INTEGER NOT NULL,
            sent_messages INTEGER NOT NULL,
            active_messages INTEGER NOT NULL
        ) WITHOUT ROWID
    ''')
    conn.execute('''
        INSERT OR IGNORE INTO message_stats (id, backfill_until)
        VALUES (1, (SELECT COALESCE(MAX(id), 0) FROM log))
    ''')


def add_message_stats(conn, rows):
    """rows: (timestamp, user_id, group_id, plugin_name, direction, at_bot)。

    调用方必须在消息 INSERT 的同一事务内调用，回滚/重试不会重复计数。
    仅保存每批的不同实体，内存用量不随当天消息量增长。
    """
    totals = [0, 0, 0, 0]
    minutes = {}
    entities = Counter()
    first_seen = {}
    for ts, uid, gid, plugin, direction, at_bot in rows:
        private = int(gid in ('', 'c2c'))
        received = int(direction == 'receive')
        sent = int(direction == 'send')
        active = direction is not None and direction != 'send' and (at_bot is None or at_bot != 0)
        counts = (1, private, received, sent)
        minute = minutes.setdefault(ts[:16], [0, 0, 0, 0, 0])
        for i, count in enumerate(counts):
            totals[i] += count
            minute[i] += count
        minute[4] += int(active)

        keys = []
        if active:
            if uid not in ('', None):
                keys.append(('user', uid))
            if gid not in ('', 'c2c', None):
                keys.append(('group', gid))
        if sent and plugin not in ('', None):
            keys.append(('command', plugin))
        for key in keys:
            entities[key] += 1
            first_seen[key] = min(first_seen.get(key, ts), ts)

    new_entities = {}
    for kind in _ENTITY_KINDS:
        items = [(kind, entity, first_seen[(kind, entity)]) for k, entity in entities if k == kind]
        inserted = conn.executemany(
            'INSERT OR IGNORE INTO message_stats_entities (kind, entity, message_count, first_seen) VALUES (?, ?, 0, ?)',
            items,
        ).rowcount
        new_entities[kind] = inserted
    conn.executemany(
        '''UPDATE message_stats_entities SET message_count = message_count + ?, first_seen = MIN(first_seen, ?)
           WHERE kind = ? AND entity = ?''',
        ((count, first_seen[(kind, entity)], kind, entity) for (kind, entity), count in entities.items()),
    )
    conn.executemany('''
        INSERT INTO message_stats_minutes VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(minute) DO UPDATE SET
            total_messages = total_messages + excluded.total_messages,
            private_messages = private_messages + excluded.private_messages,
            received_messages = received_messages + excluded.received_messages,
            sent_messages = sent_messages + excluded.sent_messages,
            active_messages = active_messages + excluded.active_messages
    ''', ((minute, *counts) for minute, counts in minutes.items()))
    conn.execute('''
        UPDATE message_stats SET total_messages = total_messages + ?,
            private_messages = private_messages + ?, received_messages = received_messages + ?,
            sent_messages = sent_messages + ?, active_users = active_users + ?, active_groups = active_groups + ?
        WHERE id = 1
    ''', (*totals, new_entities['user'], new_entities['group']))


def backfill_message_stats(conn, batch_size=_BACKFILL_BATCH):
    """调用方每批提交；新消息已实时计数，只补初始化时已有的主键范围。"""
    cursor, end = conn.execute(
        'SELECT backfill_cursor, backfill_until FROM message_stats WHERE id = 1'
    ).fetchone()
    if cursor >= end:
        return True
    rows = conn.execute('''
        SELECT id, timestamp, user_id, group_id, plugin_name, direction, at_bot
        FROM log WHERE id > ? AND id <= ? ORDER BY id LIMIT ?
    ''', (cursor, end, batch_size)).fetchall()
    add_message_stats(conn, (tuple(row)[1:] for row in rows))
    cursor = rows[-1][0] if rows else end
    conn.execute('UPDATE message_stats SET backfill_cursor = ? WHERE id = 1', (cursor,))
    return cursor >= end


def _query(conn, sql, params=()):
    cursor = conn.execute(sql, params)
    columns = [col[0] for col in cursor.description]
    return [row if isinstance(row, dict) else dict(zip(columns, row, strict=True)) for row in cursor]


def empty_message_stats():
    return {
        **dict.fromkeys((*_METRICS, 'active_users', 'active_groups', 'peak_hour', 'peak_hour_count'), 0),
        'pending': False,
        'hourly_distribution': [0] * 24,
        'top_users': [],
        'top_groups': [],
        'top_commands': [],
    }


def read_message_stats(conn, before=None):
    """普通读取只查一行计数、至多 1440 行分钟汇总和索引排行。"""
    stats = empty_message_stats()
    row = _query(conn, 'SELECT * FROM message_stats WHERE id = 1')[0]
    stats.update({key: row[key] for key in (*_METRICS, 'active_users', 'active_groups')})
    stats['pending'] = row['backfill_cursor'] < row['backfill_until']
    if stats['pending']:
        return stats

    if before is not None:
        # 已完成分钟用汇总；边界分钟精确到原查询的时间上界，最多读取一分钟日志。
        totals = _query(conn, '''
            SELECT SUM(total_messages) AS total_messages, SUM(private_messages) AS private_messages,
                SUM(received_messages) AS received_messages, SUM(sent_messages) AS sent_messages
            FROM message_stats_minutes WHERE minute < ?
        ''', (before[:16],))[0]
        boundary = _query(conn, '''
            SELECT COUNT(*) AS total_messages,
                COUNT(CASE WHEN group_id IN ('', 'c2c') THEN 1 END) AS private_messages,
                COUNT(CASE WHEN direction = 'receive' THEN 1 END) AS received_messages,
                COUNT(CASE WHEN direction = 'send' THEN 1 END) AS sent_messages
            FROM log WHERE timestamp >= ? AND timestamp <= ?
        ''', (f'{before[:16]}:00', before))[0]
        stats.update({key: (totals[key] or 0) + boundary[key] for key in _METRICS})
        stats['active_users'] = stats['active_groups'] = 0
        for row in _query(conn, '''
            SELECT kind, COUNT(*) AS c FROM message_stats_entities
            WHERE kind IN ('user', 'group') AND first_seen <= ? GROUP BY kind
        ''', (before,)):
            stats['active_users' if row['kind'] == 'user' else 'active_groups'] = row['c']
        return stats

    hours = _query(conn, '''
        SELECT substr(minute, 12, 2) AS hour, SUM(total_messages) AS total, SUM(active_messages) AS active
        FROM message_stats_minutes GROUP BY hour ORDER BY hour
    ''')
    for row in hours:
        if row['hour'].isdigit() and 0 <= int(row['hour']) < 24:
            stats['hourly_distribution'][int(row['hour'])] = row['total']
        if row['active'] > stats['peak_hour_count']:
            stats['peak_hour'] = int(row['hour']) if row['hour'].isdigit() else 0
            stats['peak_hour_count'] = row['active']
    for kind, name, key, count_key in (
        ('user', 'top_users', 'user_id', 'message_count'),
        ('group', 'top_groups', 'group_id', 'message_count'),
        ('command', 'top_commands', 'command', 'count'),
    ):
        stats[name] = [
            {key: row['entity'], count_key: row['message_count']}
            for row in _query(conn, '''
                SELECT entity, message_count FROM message_stats_entities
                WHERE kind = ? ORDER BY message_count DESC, entity LIMIT 10
            ''', (kind,))
        ]
    return stats
