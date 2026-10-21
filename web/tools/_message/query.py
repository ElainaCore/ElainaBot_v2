"""消息管理 — SQL 查询 (聊天列表聚合, 历史消息)"""

import logging
import os
from datetime import date as _date
from datetime import datetime, timedelta

import web.tools._message.shared as _shared
from web.tools._bots import iter_bots
from web.tools._message.chat_index import ChatList, read_chats
from web.tools._message.history import _read

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


# 单次覆盖索引扫描得到全部会话的最后消息 id/时间与条数, 取代逐会话点查
_GROUP_CHATS_SQL = (
    'SELECT group_id AS cid, MAX(id) AS last_id, MAX(timestamp) AS last_time, COUNT(*) AS n '
    "FROM log WHERE group_id != '' AND group_id != 'c2c' GROUP BY group_id"
)
# group_id 等值分支拆开, 每个分支都可走 (group_id, user_id, id, timestamp) 索引
_PRIVATE_CHATS_SQL = (
    'SELECT cid, MAX(last_id) AS last_id, MAX(last_time) AS last_time, SUM(n) AS n FROM ('
    'SELECT user_id AS cid, MAX(id) AS last_id, MAX(timestamp) AS last_time, COUNT(*) AS n '
    "FROM log WHERE group_id = '' AND user_id != '' GROUP BY user_id "
    'UNION ALL '
    'SELECT user_id, MAX(id), MAX(timestamp), COUNT(*) '
    "FROM log WHERE group_id = 'c2c' AND user_id != '' GROUP BY user_id"
    ') GROUP BY cid'
)


def _aggregate_chats_sync(chat_type, appid_filter):
    """读取今日增量会话索引，旧日志由独立连接在后台分批补算。"""
    if not _shared._bot_manager:
        return []
    chats = ChatList()
    today = _recent_dates(1)[0]
    cache_root = os.path.join(_shared._base_dir, 'data', 'web', 'message_chats')
    for appid, inst in iter_bots(_shared._bot_manager, appid_filter):
        if appid_filter and appid != appid_filter:
            continue
        source = inst.log_service._resolve_db_path('message', today)
        rows, pending = read_chats(source, cache_root, chat_type)
        chats.indexing |= pending
        for item in rows:
            item.update(appid=appid, bot_name=getattr(inst, 'name', appid), last_date=today)
        chats.extend(rows)
    chats.sort(key=lambda c: (c.get('last_time', ''), c.get('last_id', 0)), reverse=True)
    return chats


def _fill_chat_previews_sync(chats):
    """分页完成后只读本页的短预览，不为几万会话加载完整正文。"""
    chats = [dict(chat) for chat in chats]
    by_path = {}
    for item in chats:
        if item.get('last_id'):
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
                rows = _read(inst, 'message', d, f'SELECT id, substr(content,1,240) AS content FROM log WHERE id IN ({ph})', tuple(chunk))
                for r in rows:
                    id_to_content[(appid, r.get('id'))] = r.get('content', '')
            except Exception as e:
                log.debug(f'查询消息内容失败: {e}')
    for item in chats:
        if item.get('last_id'):
            item['last_content'] = id_to_content.get((item['appid'], item['last_id']), '')
    return chats
