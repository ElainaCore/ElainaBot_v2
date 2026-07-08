"""消息管理 — SQL 查询 (聊天列表聚合, 历史消息)"""

from datetime import date as _date
from datetime import timedelta

import web.tools._message.shared as _shared
from web.tools._message.shared import _iter_bots


_MSG_COLS = 'id, timestamp, message_id, reference_id, user_id, group_id, content, raw_message, plugin_name, direction'


def _recent_dates(days=1):
    """返回最近 N 天的日期字符串列表 (含今天)"""
    today = _date.today()
    return [(today - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(days)]


def _query_chat_messages_sync(chat_type, chat_id, appid_filter, days=3, limit=300):
    """查某个聊天会话的最近消息 — 优先查今天, 不够再往前补"""
    if not _shared._bot_manager:
        return []
    dates = _recent_dates(days)
    results = []
    if chat_type == 'group':
        where = 'group_id = ?'
        params = (chat_id,)
    else:
        where = "user_id = ? AND (group_id = '' OR group_id = 'c2c')"
        params = (chat_id,)
    sql = f'SELECT {_MSG_COLS} FROM log WHERE {where} ORDER BY id DESC LIMIT {limit}'
    for appid, inst in _iter_bots(appid_filter):
        bot_qq = getattr(inst, 'robot_qq', '') or ''
        bot_name = getattr(inst, 'name', appid)
        for d in dates:
            try:
                rows = inst.log_service.query('message', sql, params, date=d)
                for r in rows:
                    r['appid'] = appid
                    r['bot_name'] = bot_name
                    r['bot_qq'] = bot_qq
                    r['_date'] = d
                results.extend(rows)
            except Exception:
                pass
            # 今天已经够了就不查更早的日期
            if len(results) >= limit:
                break
    results.sort(key=lambda r: (r.get('_date', ''), r.get('id', 0)))
    return results[-limit:]


def _query_older_messages_sync(chat_type, chat_id, appid_filter, before_date_str, limit=300, max_days=14):
    """从 before_date 前一天开始往前搜索, 找到第一个有消息的日期即返回"""
    if not _shared._bot_manager:
        return [], '', False
    from datetime import datetime

    try:
        bd = datetime.strptime(before_date_str, '%Y-%m-%d').date()
    except ValueError:
        return [], '', False

    if chat_type == 'group':
        where = 'group_id = ?'
        params = (chat_id,)
    else:
        where = "user_id = ? AND (group_id = '' OR group_id = 'c2c')"
        params = (chat_id,)
    sql = f'SELECT {_MSG_COLS} FROM log WHERE {where} ORDER BY id DESC LIMIT {limit}'

    for offset in range(1, max_days + 1):
        d = (bd - timedelta(days=offset)).strftime('%Y-%m-%d')
        results = []
        for appid, inst in _iter_bots(appid_filter):
            bot_qq = getattr(inst, 'robot_qq', '') or ''
            bot_name = getattr(inst, 'name', appid)
            try:
                rows = inst.log_service.query('message', sql, params, date=d)
                for r in rows:
                    r['appid'] = appid
                    r['bot_name'] = bot_name
                    r['bot_qq'] = bot_qq
                    r['_date'] = d
                results.extend(rows)
            except Exception:
                pass
        if results:
            results.sort(key=lambda r: r.get('id', 0))
            return results[-limit:], d, True
    return [], '', False


def _query_lifecycle_events_sync(chat_type, chat_id, appid_filter, dates, limit=100):
    """查某个群聊的成员加入/退出事件"""
    if not _shared._bot_manager or chat_type != 'group':
        return []
    sql = "SELECT * FROM log WHERE group_id = ? AND type IN ('group_member_add', 'group_member_del') ORDER BY id DESC LIMIT ?"
    params = (chat_id, limit)
    results = []
    for appid, inst in _iter_bots(appid_filter):
        for d in dates:
            try:
                rows = inst.log_service.query('lifecycle', sql, params, date=d)
                for r in rows:
                    r['appid'] = appid
                    r['_date'] = d
                results.extend(rows)
            except Exception:
                pass
    results.sort(key=lambda r: (r.get('_date', ''), r.get('id', 0)))
    return results[-limit:]


def _aggregate_chats_sync(chat_type, appid_filter, days=1):
    """SQL 聚合聊天列表"""
    if not _shared._bot_manager:
        return []
    dates = _recent_dates(days)
    if chat_type == 'group':
        agg_sql = (
            'SELECT group_id AS chat_id, MAX(id) AS last_id, MAX(timestamp) AS last_time, '
            "COUNT(*) AS msg_count FROM log WHERE group_id != '' AND group_id != 'c2c' "
            'GROUP BY group_id'
        )
    else:
        agg_sql = (
            'SELECT user_id AS chat_id, MAX(id) AS last_id, MAX(timestamp) AS last_time, '
            "COUNT(*) AS msg_count FROM log WHERE user_id != '' AND (group_id = '' OR group_id = 'c2c') "
            'GROUP BY user_id'
        )
    merged = {}
    for appid, inst in _iter_bots(appid_filter):
        bot_name = getattr(inst, 'name', appid)
        for d in dates:
            try:
                rows = inst.log_service.query('message', agg_sql, date=d)
            except Exception:
                continue
            for r in rows:
                cid = r.get('chat_id', '')
                if not cid:
                    continue
                key = (appid, cid)
                item = merged.get(key)
                if not item:
                    item = {
                        'chat_id': cid,
                        'appid': appid,
                        'bot_name': bot_name,
                        'last_id': 0,
                        'last_time': '',
                        'last_date': '',
                        'msg_count': 0,
                    }
                    merged[key] = item
                item['msg_count'] += r.get('msg_count', 0) or 0
                rid = r.get('last_id', 0) or 0
                rts = r.get('last_time', '') or ''
                if rid and (rid > item['last_id'] or d > item['last_date']):
                    item['last_id'] = rid
                    item['last_time'] = rts
                    item['last_date'] = d
    if not merged:
        return []
    # 按 last_time 排序, 仅取前 200 个聊天的 last_content
    chats = sorted(merged.values(), key=lambda c: c.get('last_time', ''), reverse=True)
    top = chats[:200]
    by_path = {}
    for item in top:
        if item['last_id']:
            by_path.setdefault((item['appid'], item['last_date']), []).append(item['last_id'])
    id_to_content = {}
    for (appid, d), ids in by_path.items():
        inst = _shared._bot_manager._bots.get(appid)
        if not inst or not ids:
            continue
        for i in range(0, len(ids), 500):
            chunk = ids[i : i + 500]
            ph = ','.join('?' * len(chunk))
            try:
                rows = inst.log_service.query(
                    'message',
                    f'SELECT id, content FROM log WHERE id IN ({ph})',
                    tuple(chunk),
                    date=d,
                )
                for r in rows:
                    id_to_content[(appid, r.get('id'))] = r.get('content', '')
            except Exception:
                pass
    for item in top:
        item['last_content'] = id_to_content.get((item['appid'], item['last_id']), '')
    return chats
