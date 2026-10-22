"""可重建的会话索引；独立读连接分批追赶消息，不占用日志服务的读锁。"""

import hashlib
import logging
import sqlite3
import threading
import time
from collections import Counter
from contextlib import closing
from pathlib import Path

log = logging.getLogger('ElainaBot.web.message')
_BATCH = 2000
_TAIL = 2000
_jobs: set[str] = set()
_jobs_lock = threading.Lock()


class ChatList(list):
    indexing = False


def _reader(path):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
    conn.execute('PRAGMA query_only=ON')
    return conn


def _open_index(path, source):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=2)
    try:
        # SQLite 切换日志模式时可能直接返回 BUSY，不会等待连接的 busy_timeout。
        # 两个进程首次创建同一索引时，短暂退避后重试；已有 WAL 索引无需再次切换。
        deadline = time.monotonic() + 2
        while True:
            try:
                if conn.execute('PRAGMA journal_mode').fetchone()[0].lower() != 'wal':
                    conn.execute('PRAGMA journal_mode=WAL')
                break
            except sqlite3.OperationalError as exc:
                if ('locked' not in str(exc).lower() and 'busy' not in str(exc).lower()) or time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)
        conn.execute('PRAGMA synchronous=NORMAL')
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS progress (id INTEGER PRIMARY KEY, source TEXT NOT NULL, cursor INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS chats (
                kind TEXT NOT NULL, chat_id TEXT NOT NULL, last_id INTEGER NOT NULL,
                last_time TEXT NOT NULL, msg_count INTEGER NOT NULL,
                PRIMARY KEY (kind, chat_id)
            ) WITHOUT ROWID;
            CREATE INDEX IF NOT EXISTS idx_chats_recent ON chats(kind, last_id DESC);
        ''')
        stat = source.stat()
        identity = f'{source.resolve()}:{stat.st_dev}:{stat.st_ino}'
        row = conn.execute('SELECT source, cursor FROM progress WHERE id=1').fetchone()
        if row is None or row[0] != identity:
            with conn:
                conn.execute('BEGIN IMMEDIATE')
                row = conn.execute('SELECT source FROM progress WHERE id=1').fetchone()
                if row is None or row[0] != identity:
                    conn.execute('DELETE FROM chats')
                    conn.execute('INSERT OR REPLACE INTO progress VALUES (1, ?, 0)', (identity,))
        return conn
    except BaseException:
        conn.close()
        raise


def _summarize(rows):
    counts = Counter()
    latest = {}
    for ident, timestamp, group_id, user_id in rows:
        if group_id not in ('', 'c2c', None):
            key = ('group', str(group_id))
        elif user_id:
            key = ('user', str(user_id))
        else:
            continue
        counts[key] += 1
        if key not in latest or ident > latest[key][0]:
            latest[key] = (ident, timestamp or '')
    return [(kind, cid, *latest[(kind, cid)], count) for (kind, cid), count in counts.items()]


def sync_index(source, index, *, max_batches=None):
    """游标与计数一起提交，重启、重试和并发写入不会重复计数。"""
    source, index = Path(source), Path(index)
    if not source.is_file():
        return
    with closing(_reader(source)) as reader, closing(_open_index(index, source)) as writer:
        end = reader.execute('SELECT COALESCE(MAX(id), 0) FROM log').fetchone()[0]
        cursor = writer.execute('SELECT cursor FROM progress WHERE id=1').fetchone()[0]
        if cursor > end:
            with writer:
                writer.execute('DELETE FROM chats')
                writer.execute('UPDATE progress SET cursor=0 WHERE id=1')
            cursor = 0
        batches = 0
        while cursor < end and (max_batches is None or batches < max_batches):
            previous_cursor = cursor
            rows = reader.execute(
                'SELECT id, timestamp, group_id, user_id FROM log WHERE id>? AND id<=? ORDER BY id LIMIT ?',
                (cursor, end, _BATCH),
            ).fetchall()
            cursor = rows[-1][0] if rows else end
            with writer:
                # 同一派生索引也可能被另一个面板进程追赶，先在写事务内比较游标。
                writer.execute('BEGIN IMMEDIATE')
                stored = writer.execute('SELECT cursor FROM progress WHERE id=1').fetchone()[0]
                if stored != previous_cursor:
                    cursor = stored
                    continue
                writer.executemany('''
                    INSERT INTO chats VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(kind, chat_id) DO UPDATE SET
                        last_time=CASE WHEN excluded.last_id>last_id THEN excluded.last_time ELSE last_time END,
                        last_id=MAX(last_id, excluded.last_id), msg_count=msg_count+excluded.msg_count
                ''', _summarize(rows))
                writer.execute('UPDATE progress SET cursor=? WHERE id=1', (cursor,))
            batches += 1
            if max_batches is None:
                time.sleep(0.005)


def _schedule(source, index):
    key = str(index)
    with _jobs_lock:
        if key in _jobs:
            return
        _jobs.add(key)

    def run():
        try:
            sync_index(source, index)
        except Exception:
            log.exception('会话索引更新失败: %s', source)
        finally:
            with _jobs_lock:
                _jobs.discard(key)

    threading.Thread(target=run, name='message-chat-index', daemon=True).start()


def read_chats(source, cache_root, kind):
    """首次立即返回最近会话；历史补算在后台完成，之后只处理新增主键。"""
    source = Path(source)
    if not source.is_file():
        return [], False
    key = hashlib.sha256(str(source.resolve()).encode()).hexdigest()[:24]
    index = Path(cache_root) / (key + '.db')
    with closing(_open_index(index, source)) as conn, closing(_reader(source)) as reader:
        # 固定读快照，不能把后台刚提交的计数与旧游标混在一起。
        conn.execute('BEGIN')
        cursor = conn.execute('SELECT cursor FROM progress WHERE id=1').fetchone()[0]
        summaries = conn.execute('SELECT kind, chat_id, last_id, last_time, msg_count FROM chats WHERE kind=?', (kind,)).fetchall()
        conn.rollback()
        end = reader.execute('SELECT COALESCE(MAX(id), 0) FROM log').fetchone()[0]
        if cursor > end:
            summaries, cursor = [], 0
        tail = reader.execute(
            'SELECT id, timestamp, group_id, user_id FROM log WHERE id>? ORDER BY id DESC LIMIT ?', (cursor, _TAIL)
        ).fetchall() if cursor < end else []
        merged = {r[1]: {'chat_id': r[1], 'last_id': r[2], 'last_time': r[3], 'msg_count': r[4]} for r in summaries}
        for row_kind, cid, last_id, timestamp, count in _summarize(tail):
            if row_kind != kind:
                continue
            item = merged.setdefault(cid, {'chat_id': cid, 'last_id': 0, 'last_time': '', 'msg_count': 0})
            item['msg_count'] += count
            if last_id > item['last_id']:
                item.update(last_id=last_id, last_time=timestamp)
        pending = bool(tail and reader.execute('SELECT 1 FROM log WHERE id>? AND id<? LIMIT 1', (cursor, tail[-1][0])).fetchone())
    if cursor != end:
        _schedule(source, index)
    return list(merged.values()), pending
