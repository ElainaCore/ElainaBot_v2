"""消息管理 — SQL 查询 (聊天列表聚合, 历史消息)"""

from datetime import timedelta, date as _date

from web.tools._message.shared import _bot_manager, _iter_bots, _batch_get_nicknames


def _recent_dates(days=1):
    """返回最近 N 天的日期字符串列表 (含今天)"""
    today = _date.today()
    return [(today - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(days)]


def _query_chat_messages_sync(chat_type, chat_id, appid_filter, days=3, limit=300):
    """查某个聊天会话的最近消息 — SQL WHERE 下推, 走索引, 避免全表扶描+Python过滤"""
    if not _bot_manager:
        return []
    dates = _recent_dates(days)
    results = []
    if chat_type == 'group':
        where = "group_id = ?"
        params = (chat_id,)
    else:
        # 私聊: user_id 匹配 且 group_id 为空或 'c2c'
        where = "user_id = ? AND (group_id = '' OR group_id = 'c2c')"
        params = (chat_id,)
    sql = f"SELECT * FROM log WHERE {where} ORDER BY id DESC LIMIT {limit}"
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
    results.sort(key=lambda r: (r.get('_date', ''), r.get('id', 0)))
    return results[-limit:]


def _query_older_messages_sync(chat_type, chat_id, appid_filter, before_date_str, limit=300, max_days=14):
    """从 before_date 前一天开始往前搜索, 找到第一个有消息的日期即返回"""
    if not _bot_manager:
        return [], '', False
    from datetime import datetime
    try:
        bd = datetime.strptime(before_date_str, '%Y-%m-%d').date()
    except ValueError:
        return [], '', False

    if chat_type == 'group':
        where = "group_id = ?"
        params = (chat_id,)
    else:
        where = "user_id = ? AND (group_id = '' OR group_id = 'c2c')"
        params = (chat_id,)
    sql = f"SELECT * FROM log WHERE {where} ORDER BY id DESC LIMIT {limit}"

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


def _aggregate_chats_sync(chat_type, appid_filter, days=3):
    """SQL 聚合聊天列表 — 避免下载几千条诡代反检柒"""
    if not _bot_manager:
        return []
    dates = _recent_dates(days)
    if chat_type == 'group':
        # 按 group_id 聚合 — 需要 idx_msg_group_id 索引加速
        agg_sql = (
            "SELECT group_id AS chat_id, MAX(id) AS last_id, MAX(timestamp) AS last_time, "
            "COUNT(*) AS msg_count FROM log WHERE group_id != '' AND group_id != 'c2c' "
            "GROUP BY group_id"
        )
    else:
        agg_sql = (
            "SELECT user_id AS chat_id, MAX(id) AS last_id, MAX(timestamp) AS last_time, "
            "COUNT(*) AS msg_count FROM log WHERE user_id != '' AND (group_id = '' OR group_id = 'c2c') "
            "GROUP BY user_id"
        )
    # 汇总: chat_key -> {appid, last_id, last_time, msg_count}
    merged = {}
    for appid, inst in _iter_bots(appid_filter):
        bot_name = getattr(inst, 'name', appid)
        for d in dates:
            try:
                rows = inst.log_service.query('message', agg_sql, date=d)
                for r in rows:
                    cid = r.get('chat_id', '')
                    if not cid:
                        continue
                    key = (appid, cid)
                    item = merged.get(key)
                    if not item:
                        item = {'chat_id': cid, 'appid': appid, 'bot_name': bot_name,
                                'last_id': 0, 'last_time': '', 'last_date': '',
                                'msg_count': 0}
                        merged[key] = item
                    item['msg_count'] += r.get('msg_count', 0) or 0
                    rid = r.get('last_id', 0) or 0
                    rts = r.get('last_time', '') or ''
                    # 在同 bot+chat 下, 选靠近的那一天作为预览来源
                    if rid and (rid > item['last_id'] or d > item['last_date']):
                        item['last_id'] = rid
                        item['last_time'] = rts
                        item['last_date'] = d
            except Exception:
                pass
    if not merged:
        return []
    # 拉取每个聊天的最后一条 content (仅 last_date 那天 + last_id)
    # 按 (appid, date) 分组 — 然后多个 id IN (...) 一次查
    by_path = {}  # (appid, date) -> [last_id]
    for (appid, _cid), item in merged.items():
        if item['last_id']:
            by_path.setdefault((appid, item['last_date']), []).append(item['last_id'])
    id_to_content = {}  # (appid, id) -> content
    for (appid, d), ids in by_path.items():
        inst = _bot_manager._bots.get(appid)
        if not inst or not ids:
            continue
        for chunk_start in range(0, len(ids), 500):
            chunk = ids[chunk_start:chunk_start + 500]
            placeholders = ','.join('?' * len(chunk))
            sql = f"SELECT id, content FROM log WHERE id IN ({placeholders})"
            try:
                rows = inst.log_service.query('message', sql, tuple(chunk), date=d)
                for r in rows:
                    id_to_content[(appid, r.get('id'))] = r.get('content', '')
            except Exception:
                pass
    chats = []
    for (appid, cid), item in merged.items():
        item['last_content'] = id_to_content.get((appid, item['last_id']), '')
        chats.append(item)
    chats.sort(key=lambda c: c.get('last_time', ''), reverse=True)
    return chats
