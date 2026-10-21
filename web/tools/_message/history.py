"""按时间和主键翻页；读取历史不会等待全库会话聚合，也不会跳过同日消息。"""

import base64
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qs

import web.tools._message.shared as shared

_COLS = 'id,timestamp,message_id,reference_id,user_id,group_id,content,raw_message,plugin_name,direction'


def _bots(appid):
    bots = shared._bot_manager._bots if shared._bot_manager else {}
    return [(appid, bots[appid])] if appid in bots else ([] if appid else list(bots.items()))


def _scope(chat_type, chat_id, appid):
    return hashlib.sha256(json.dumps([chat_type, chat_id, appid]).encode()).hexdigest()[:16]


def _encode(row, scope):
    value = [scope, row['timestamp'], row['appid'], row['id'], row['_kind']]
    return base64.urlsafe_b64encode(json.dumps(value, separators=(',', ':')).encode()).decode().rstrip('=')


def _decode(value, scope):
    try:
        if not isinstance(value, str) or len(value) > 1024:
            raise ValueError
        data = json.loads(base64.urlsafe_b64decode(value + '=' * (-len(value) % 4)))
        if len(data) != 5 or data[0] != scope or not isinstance(data[1], str) or not isinstance(data[2], str):
            raise ValueError
        if not isinstance(data[3], int) or data[3] < 0 or data[4] not in ('message', 'lifecycle'):
            raise ValueError
        date.fromisoformat(data[1][:10])
        return data[1:]
    except (ValueError, TypeError, IndexError, KeyError) as exc:
        raise ValueError('无效的历史消息游标') from exc


def _read(inst, kind, day, sql, params):
    path = Path(inst.log_service._resolve_db_path(kind, day))
    if not path.is_file():
        return []
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        return [dict(row) for row in conn.execute(sql, params)]


def _chat_where(chat_type, chat_id):
    if chat_type == 'group':
        return 'group_id=?', (chat_id,)
    return "user_id=? AND group_id IN ('', 'c2c')", (chat_id,)


def query_page(chat_type, chat_id, appid='', *, cursor='', before_date='', limit=60, max_days=30):
    scope = _scope(chat_type, chat_id, appid)
    boundary = _decode(cursor, scope) if cursor else None
    today = date.today()
    day = date.fromisoformat(boundary[0][:10]) if boundary else (date.fromisoformat(before_date) - timedelta(days=1) if before_date else today)
    if day > today:
        raise ValueError('历史日期不能晚于今天')
    cutoff = today - timedelta(days=max_days)
    results = []
    instances = _bots(appid)
    while day >= cutoff and len(results) <= limit:
        day_rows = []
        day_str = day.isoformat()
        for bot_id, inst in instances:
            for kind in (('message', 'lifecycle') if chat_type == 'group' else ('message',)):
                where, params = _chat_where(chat_type, chat_id)
                if kind == 'lifecycle':
                    where += " AND type IN ('group_member_add','group_member_del')"
                if boundary and day_str == boundary[0][:10]:
                    timestamp, last_appid, ident, last_kind = boundary
                    if bot_id == last_appid:
                        op = '<=' if kind < last_kind else '<'
                        where += f' AND (timestamp,id) {op} (?,?)'
                        params += (timestamp, ident)
                    else:
                        where += ' AND timestamp ' + ('<=' if bot_id < last_appid else '<') + ' ?'
                        params += (timestamp,)
                columns = _COLS if kind == 'message' else 'id,timestamp,user_id,group_id,type'
                sql = f'SELECT {columns} FROM log WHERE {where} ORDER BY timestamp DESC,id DESC LIMIT ?'
                for row in _read(inst, kind, day_str, sql, params + (limit + 1,)):
                    row.update(appid=bot_id, bot_name=getattr(inst, 'name', bot_id), bot_qq=getattr(inst, 'robot_qq', '') or '', _date=day_str, _kind=kind)
                    day_rows.append(row)
        day_rows.sort(key=lambda r: (r['timestamp'], r['appid'], r['id'], r['_kind']), reverse=True)
        results.extend(day_rows[:limit + 1])
        day -= timedelta(days=1)
        boundary = None
    has_more = len(results) > limit
    rows = results[:limit]
    next_cursor = _encode(rows[-1], scope) if rows and has_more else ''
    oldest = rows[-1]['_date'] if rows else (day + timedelta(days=1)).isoformat()
    return list(reversed(rows)), next_cursor, oldest, has_more


def query_raw(chat_type, chat_id, appid, day, ident):
    date.fromisoformat(day)
    where, params = _chat_where(chat_type, chat_id)
    for _, inst in _bots(appid):
        rows = _read(inst, 'message', day, f'SELECT raw_message FROM log WHERE {where} AND id=? LIMIT 1', params + (ident,))
        if rows:
            raw = rows[0]['raw_message'] or ''
            return '' if raw == '[recalled]' else raw
    return ''


def query_reply_id(chat_type, chat_id, appid):
    where, params = _chat_where(chat_type, chat_id)
    latest = []
    for bot_id, inst in _bots(appid):
        rows = _read(inst, 'message', date.today().isoformat(), f"SELECT timestamp,id,message_id FROM log WHERE {where} AND direction!='send' AND message_id!='' ORDER BY id DESC LIMIT 1", params)
        latest.extend((r['timestamp'], bot_id, r['id'], r['message_id']) for r in rows)
    return max(latest)[-1] if latest else ''


def compact_reference(raw):
    if not raw or not isinstance(raw, str) or not raw.lstrip().startswith('{'):
        return None
    try:
        payload = json.loads(raw)
        data = payload.get('d') or payload
        scene = data.get('message_scene') or {}
        ext = scene.get('ext') or []
        reference = ''
        for item in ext if isinstance(ext, list) else [ext]:
            if not isinstance(item, str):
                continue
            values = parse_qs(item.split('?', 1)[-1])
            for key in ('ref_msg_idx', 'ref_msg_id', 'reference_id', 'message_reference_id'):
                if values.get(key):
                    reference = values[key][0]
                    break
            if reference:
                break
        elements = data.get('msg_elements') or []
        element = next((e for e in elements if isinstance(e, dict) and e.get('msg_idx') == reference), None)
        element = element or (elements[0] if elements and isinstance(elements[0], dict) else {})
        if reference or element:
            author = element.get('author') or {}
            return {'id': reference or element.get('msg_idx', ''), 'author': author.get('username') or author.get('id') or ('Bot' if author.get('bot') else '引用消息'), 'text': ' '.join(str(element.get('content') or '').split())}
        ref = (payload.get('message_reference') or {}).get('message_id') or payload.get('message_reference_id') or payload.get('reference_message_id')
        return {'id': str(ref), 'author': '', 'text': ''} if ref else None
    except (ValueError, TypeError, AttributeError):
        return None
