"""消息管理 — SQL 查询 (聊天列表聚合, 历史消息)"""

import logging
from datetime import date as _date
from datetime import datetime, timedelta

import web.tools._message.shared as _shared
from web.tools._bots import iter_bots

log = logging.getLogger('ElainaBot.web.message')

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
    for appid, inst in iter_bots(_shared._bot_manager, appid_filter):
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
            except Exception as e:
                log.debug(f'查询消息失败 {appid} {d}: {e}')
            # 今天已经够了就不查更早的日期
            if len(results) >= limit:
                break
    results.sort(key=lambda r: (r.get('_date', ''), r.get('id', 0)))
    return results[-limit:]


def _query_older_messages_sync(chat_type, chat_id, appid_filter, before_date_str, limit=300, max_days=14):
    """从 before_date 前一天开始往前搜索, 找到第一个有消息的日期即返回"""
    if not _shared._bot_manager:
        return [], '', False
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
        for appid, inst in iter_bots(_shared._bot_manager, appid_filter):
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
            except Exception as e:
                log.debug(f'查询历史消息失败 {appid} {d}: {e}')
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
    for appid, inst in iter_bots(_shared._bot_manager, appid_filter):
        for d in dates:
            try:
                rows = inst.log_service.query('lifecycle', sql, params, date=d)
                for r in rows:
                    r['appid'] = appid
                    r['_date'] = d
                results.extend(rows)
            except Exception as e:
                log.debug(f'查询生命周期事件失败 {appid} {d}: {e}')
    results.sort(key=lambda r: (r.get('_date', ''), r.get('id', 0)))
    return results[-limit:]


def _iter_distinct_chat_ids(inst, chat_type, d):
    """松散索引扫描取当日全部会话 id — 每个会话两次点查, 不做全表 GROUP BY"""
    if chat_type == 'group':
        next_sql = (
            "SELECT group_id AS cid FROM log WHERE group_id > ? AND group_id != 'c2c' "
            'ORDER BY group_id LIMIT 1'
        )
        last_sql = 'SELECT id, timestamp FROM log WHERE group_id = ? ORDER BY id DESC LIMIT 1'
    else:
        # group_id 等值分支拆开, 每个分支都可用 (group_id, user_id, id) 索引直接定位
        branch = '(SELECT user_id AS cid FROM log WHERE group_id = ? AND user_id > ? ORDER BY user_id LIMIT 1)'
        next_sql = f'SELECT MIN(cid) AS cid FROM (SELECT * FROM {branch} UNION ALL SELECT * FROM {branch})'
        branch_last = '(SELECT id, timestamp FROM log WHERE group_id = ? AND user_id = ? ORDER BY id DESC LIMIT 1)'
        last_sql = f'SELECT id, timestamp FROM (SELECT * FROM {branch_last} UNION ALL SELECT * FROM {branch_last}) ORDER BY id DESC LIMIT 1'
    is_group = chat_type == 'group'
    cur = ''
    while True:
        params = (cur,) if is_group else ('', cur, 'c2c', cur)
        rows = inst.log_service.query('message', next_sql, params, date=d)
        if not rows or not rows[0].get('cid'):
            return
        cid = rows[0]['cid']
        cur = cid
        lp = (cid,) if is_group else ('', cid, 'c2c', cid)
        last = inst.log_service.query('message', last_sql, lp, date=d)
        if last:
            yield cid, last[0].get('id', 0) or 0, last[0].get('timestamp', '') or ''


def _aggregate_chats_sync(chat_type, appid_filter):
    """聚合聊天列表 (仅今日) — 松散扫描会话 id, 仅前 200 个会话统计条数/取最后消息"""
    if not _shared._bot_manager:
        return []
    dates = _recent_dates(1)
    if chat_type == 'group':
        count_sql = 'SELECT COUNT(*) AS n FROM log WHERE group_id = ?'
    else:
        count_sql = (
            "SELECT (SELECT COUNT(*) FROM log WHERE group_id = '' AND user_id = ?) "
            "+ (SELECT COUNT(*) FROM log WHERE group_id = 'c2c' AND user_id = ?) AS n"
        )
    merged = {}
    for appid, inst in iter_bots(_shared._bot_manager, appid_filter):
        bot_name = getattr(inst, 'name', appid)
        for d in dates:
            try:
                for cid, last_id, last_time in _iter_distinct_chat_ids(inst, chat_type, d):
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
                    if last_id and (last_id > item['last_id'] or d > item['last_date']):
                        item['last_id'] = last_id
                        item['last_time'] = last_time
                        item['last_date'] = d
            except Exception as e:
                log.debug(f'聊天列表聚合失败 {appid} {d}: {e}')
                continue
    if not merged:
        return []
    # 按 last_time 排序, 仅前 200 个聊天统计消息条数和 last_content
    chats = sorted(merged.values(), key=lambda c: c.get('last_time', ''), reverse=True)
    top = chats[:200]
    for item in top:
        inst = _shared._bot_manager._bots.get(item['appid'])
        if not inst:
            continue
        cp = (item['chat_id'],) if chat_type == 'group' else (item['chat_id'], item['chat_id'])
        try:
            rows = inst.log_service.query('message', count_sql, cp, date=item['last_date'])
            item['msg_count'] = (rows[0].get('n', 0) or 0) if rows else 0
        except Exception as e:
            log.debug(f'统计消息数失败: {e}')
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
            except Exception as e:
                log.debug(f'查询消息内容失败: {e}')
    for item in top:
        item['last_content'] = id_to_content.get((item['appid'], item['last_id']), '')
    return chats
