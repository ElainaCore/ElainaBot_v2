"""统计数据 — DAU / 消息统计"""

import asyncio
import contextlib
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any

from aiohttp import web

log = logging.getLogger('ElainaBot.web.stats')

_statistics_tasks: dict[str, dict[str, Any]] = {}
_task_results: dict[str, dict[str, Any]] = {}
_bot_manager: object | None = None
_base_dir: str = ''

# 简单内存缓存: {(date, appid_filter): (timestamp, data)} — 避免短时间内重复全表扫描
_stats_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_chart_cache: dict[tuple[int, str], tuple[float, dict[str, Any]]] = {}
_CACHE_TTL = 10  # 秒


_hourly_cache: dict[str, dict[str, int]] = {}
_hourly_task: asyncio.Task | None = None
_HOURLY_SQL = 'SELECT substr(timestamp,12,2) AS hr, COUNT(*) AS c FROM log GROUP BY hr'


def _hourly_path():
    return os.path.join(_base_dir, 'data', 'hourly_cache.json')


def _save_hourly_cache():
    p = _hourly_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with contextlib.suppress(Exception), open(p, 'w', encoding='utf-8') as f:
        json.dump(_hourly_cache, f, ensure_ascii=False)


def _query_hourly_from_db(date, where=''):
    """从 DB 查某天每小时分布, where 可附加过滤条件"""
    sql = _HOURLY_SQL if not where else _HOURLY_SQL.replace('GROUP BY', f'{where} GROUP BY')
    hourly = {}
    for _, inst in _iter_bots(''):
        with contextlib.suppress(Exception):
            for r in inst.log_service.query('message', sql, date=date):
                h = r.get('hr', '')
                if h:
                    hourly[h] = hourly.get(h, 0) + r.get('c', 0)
    return hourly


def _snapshot_completed_hours():
    """快照今日已结束小时, 仅保留今天+昨天"""
    now = datetime.now()
    today, cur_h = now.strftime('%Y-%m-%d'), now.hour
    yesterday = (now - timedelta(days=1)).strftime('%Y-%m-%d')
    tc = _hourly_cache.get(today, {})

    need = {f'{h:02d}' for h in range(cur_h)} - tc.keys()
    if need:
        for h, c in _query_hourly_from_db(today, f"WHERE substr(timestamp,12,2)<'{cur_h:02d}'").items():
            if h in need:
                tc[h] = tc.get(h, 0) + c

    yc = _hourly_cache.get(yesterday) or _query_hourly_from_db(yesterday) or None
    _hourly_cache.clear()
    _hourly_cache[today] = tc
    if yc:
        _hourly_cache[yesterday] = yc
    _save_hourly_cache()


async def _hourly_snapshot_loop():
    while True:
        now = datetime.now()
        await asyncio.sleep((now.replace(minute=0, second=5, microsecond=0) + timedelta(hours=1) - now).total_seconds())
        with contextlib.suppress(Exception):
            await asyncio.get_running_loop().run_in_executor(None, _snapshot_completed_hours)


def set_context(bot_manager, base_dir=''):
    global _bot_manager, _base_dir, _hourly_task
    _bot_manager = bot_manager
    if base_dir:
        _base_dir = base_dir
    try:
        with open(_hourly_path(), encoding='utf-8') as f:
            _hourly_cache.update(json.load(f))
    except Exception:
        pass
    with contextlib.suppress(Exception):
        _snapshot_completed_hours()
    if _hourly_task is None or _hourly_task.done():
        with contextlib.suppress(RuntimeError):
            _hourly_task = asyncio.get_running_loop().create_task(_hourly_snapshot_loop())


def _iter_bots(appid_filter=''):
    """按 appid 过滤机器人迭代器; 空字符串=全部"""
    if not _bot_manager:
        return []
    if appid_filter and appid_filter in _bot_manager._bots:
        return [(appid_filter, _bot_manager._bots[appid_filter])]
    return list(_bot_manager._bots.items())


_LIFECYCLE_TYPE_MAP = {
    'group_add': 'group_join_count',
    'group_del': 'group_leave_count',
    'friend_add': 'friend_add_count',
    'friend_del': 'friend_remove_count',
}


def _count_lifecycle_today(appid_filter, date_str):
    """从 lifecycle.db 实时统计今日进群/退群/加好友/删好友事件"""
    ev = {'group_join_count': 0, 'group_leave_count': 0, 'friend_add_count': 0, 'friend_remove_count': 0}
    for _, inst in _iter_bots(appid_filter):
        with contextlib.suppress(Exception):
            rows = inst.log_service.query(
                'lifecycle',
                'SELECT type, COUNT(*) AS c FROM log GROUP BY type',
                date=date_str,
            )
            for r in rows:
                key = _LIFECYCLE_TYPE_MAP.get(r.get('type', ''))
                if key:
                    ev[key] += r.get('c', 0)
    return ev


def _count_table(appid_filter, table):
    """累计某张表的总行数 (data.db)"""
    total = 0
    for _, inst in _iter_bots(appid_filter):
        try:
            r = inst.log_service.query_data(f'SELECT COUNT(*) as c FROM {table}')
            if r:
                total += r[0].get('c', 0)
        except Exception:
            pass
    return total


def _aggregate_hourly(appid_filter, date):
    """聚合某天每小时消息分布 — 缓存优先, 今日仅实时查当前小时"""
    now = datetime.now()
    today = now.strftime('%Y-%m-%d')
    cached = _hourly_cache.get(date)

    # 有缓存且无 appid 过滤
    if cached and not appid_filter:
        hourly = dict(cached)
        if date == today:
            # 补充当前小时实时数据
            cur_h = f'{now.hour:02d}'
            cnt = 0
            for _, inst in _iter_bots(''):
                with contextlib.suppress(Exception):
                    rows = inst.log_service.query(
                        'message', 'SELECT COUNT(*) AS c FROM log WHERE substr(timestamp,12,2)=?',
                        (cur_h,), date=date,
                    )
                    if rows:
                        cnt += rows[0].get('c', 0)
            hourly[cur_h] = cnt
        return hourly

    # 无缓存 / 有 appid 过滤: 全量查
    return _query_hourly_from_db(date) if not appid_filter else _query_hourly_from_db_filtered(appid_filter, date)


def _query_hourly_from_db_filtered(appid_filter, date):
    """带 appid 过滤的全量查"""
    hourly = {}
    for _, inst in _iter_bots(appid_filter):
        with contextlib.suppress(Exception):
            for r in inst.log_service.query('message', _HOURLY_SQL, date=date):
                h = r.get('hr', '')
                if h:
                    hourly[h] = hourly.get(h, 0) + r.get('c', 0)
    return hourly


async def handle_get_statistics(request: web.Request):
    """获取统计数据 — SQLite 查询放到 executor, 不阻塞事件循环"""
    import time as _time

    force = request.query.get('force_refresh', 'false') == 'true'
    selected_date = request.query.get('date', '')
    appid_filter = request.query.get('appid', '')

    cache_key = (selected_date, appid_filter)
    now = _time.time()
    if not force:
        cached = _stats_cache.get(cache_key)
        if cached and now - cached[0] < _CACHE_TTL:
            return web.json_response({'success': True, 'data': cached[1]})

    try:
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(None, _gather_stats, force, selected_date, appid_filter)
        _stats_cache[cache_key] = (now, data)
        return web.json_response({'success': True, 'data': data})
    except Exception as e:
        return web.json_response({'success': False, 'error': str(e)}, status=500)


async def handle_get_task_status(request: web.Request):
    task_id = request.match_info.get('task_id', '')
    if task_id not in _statistics_tasks:
        return web.json_response({'success': False, 'error': '任务不存在'}, status=404)
    task = _statistics_tasks[task_id].copy()
    if task['status'] == 'completed' and task_id in _task_results:
        return web.json_response({'success': True, 'data': _task_results[task_id], 'task_info': task})
    return web.json_response(
        {
            'success': True,
            'status': task['status'],
            'progress': task.get('progress', 0),
            'message': task.get('message', ''),
        }
    )


async def handle_get_available_dates(request: web.Request):
    """返回有 DAU 数据的日期列表"""
    dates = [
        {
            'value': 'today',
            'date': datetime.now().strftime('%Y-%m-%d'),
            'display': '今日数据',
            'is_today': True,
        }
    ]
    return web.json_response({'success': True, 'dates': dates})


async def handle_get_chart_data(request: web.Request):
    """返回最近 N 天的折线图数据 — SQLite 查询放到 executor"""
    import time as _time

    days = max(1, min(30, int(request.query.get('days', '7'))))
    appid_filter = request.query.get('appid', '')

    cache_key = (days, appid_filter)
    now_ts = _time.time()
    cached = _chart_cache.get(cache_key)
    if cached and now_ts - cached[0] < _CACHE_TTL:
        return web.json_response(cached[1])

    loop = asyncio.get_running_loop()
    payload = await loop.run_in_executor(None, _gather_chart_sync, days, appid_filter)
    _chart_cache[cache_key] = (now_ts, payload)
    return web.json_response(payload)


async def handle_get_hourly_statistics(request: web.Request):
    """仪表盘轻量小时分布 — 避免进入仪表盘时触发完整统计扫描。"""
    import time as _time

    appid_filter = request.query.get('appid', '')
    cache_key = (0, appid_filter)
    now_ts = _time.time()
    cached = _chart_cache.get(cache_key)
    if cached and now_ts - cached[0] < _CACHE_TTL:
        return web.json_response(cached[1])

    loop = asyncio.get_running_loop()
    payload = await loop.run_in_executor(None, _gather_hourly_statistics_sync, appid_filter)
    _chart_cache[cache_key] = (now_ts, payload)
    return web.json_response(payload)


def _hourly_list(hourly):
    return [hourly.get(f'{h:02d}', 0) for h in range(24)]


def _gather_hourly_statistics_sync(appid_filter):
    now = datetime.now()
    today = now.strftime('%Y-%m-%d')
    yesterday = (now - timedelta(days=1)).strftime('%Y-%m-%d')
    return {
        'success': True,
        'data': {
            'today_hourly_distribution': _hourly_list(_aggregate_hourly(appid_filter, today)),
            'yesterday_hourly_distribution': _hourly_list(_aggregate_hourly(appid_filter, yesterday)),
        },
    }


def _gather_chart_sync(days, appid_filter):
    """折线图数据同步聚合 (executor 中调用)"""
    labels = []
    # 消息统计
    msg_total = []
    msg_private = []
    msg_group = []
    # 活跃统计
    active_users = []
    active_groups = []
    # 事件统计
    ev_group_join = []
    ev_group_leave = []
    ev_friend_add = []
    ev_friend_remove = []

    today_date = datetime.now().date()
    for i in range(days - 1, -1, -1):
        d = today_date - timedelta(days=i)
        date_str = d.strftime('%Y-%m-%d')
        labels.append(d.strftime('%m-%d'))

        day_total = 0
        day_private = 0
        day_users = set()
        day_groups = set()
        day_join = 0
        day_leave = 0
        day_fadd = 0
        day_frem = 0

        is_today = d == today_date
        for _appid, inst in _iter_bots(appid_filter):
            if is_today:
                # 今日: 实时读 message.db (合并查询, 一次扫表得到全部聚合)
                try:
                    rows = inst.log_service.query(
                        'message',
                        'SELECT COUNT(*) as cnt, '
                        "COUNT(CASE WHEN group_id = '' OR group_id = 'c2c' THEN 1 END) as priv, "
                        "COUNT(DISTINCT CASE WHEN user_id != '' THEN user_id END) as users, "
                        "COUNT(DISTINCT CASE WHEN group_id != '' AND group_id != 'c2c' THEN group_id END) as groups_ "
                        "FROM log WHERE user_id != ''",
                        date=date_str,
                    )
                    if rows:
                        r0 = rows[0]
                        day_total += r0.get('cnt', 0)
                        day_private += r0.get('priv', 0)
                        # 用 range 作为占位 — set 只用于 len(), 不在意元素本身
                        day_users.update(range(len(day_users), len(day_users) + r0.get('users', 0)))
                        day_groups.update(range(len(day_groups), len(day_groups) + r0.get('groups_', 0)))
                except Exception:
                    pass
            if not is_today:
                # 历史: 从 dau.db
                try:
                    dau_rows = inst.log_service.query('dau', 'SELECT * FROM log WHERE date=?', (date_str,))
                    if dau_rows:
                        dd = dau_rows[0]
                        day_join += dd.get('group_join_count', 0)
                        day_leave += dd.get('group_leave_count', 0)
                        day_fadd += dd.get('friend_add_count', 0)
                        day_frem += dd.get('friend_remove_count', 0)
                        day_total += dd.get('total_messages', 0)
                        day_private += dd.get('private_messages', 0)
                        day_users.update(range(dd.get('active_users', 0)))
                        day_groups.update(range(dd.get('active_groups', 0)))
                except Exception:
                    pass

        if is_today:
            # 今日事件: 从 lifecycle.db 实时统计
            today_ev = _count_lifecycle_today(appid_filter, date_str)
            day_join += today_ev['group_join_count']
            day_leave += today_ev['group_leave_count']
            day_fadd += today_ev['friend_add_count']
            day_frem += today_ev['friend_remove_count']

        msg_total.append(day_total)
        msg_private.append(day_private)
        msg_group.append(day_total - day_private)
        active_users.append(len(day_users))
        active_groups.append(len(day_groups))
        ev_group_join.append(day_join)
        ev_group_leave.append(day_leave)
        ev_friend_add.append(day_fadd)
        ev_friend_remove.append(day_frem)

    # 累计: 用户 / 群组 / 好友 (从 data.db)
    total_u = _count_table(appid_filter, 'users')
    total_g = _count_table(appid_filter, 'groups_users')
    total_f = _count_table(appid_filter, 'members')

    return {
        'success': True,
        'data': {
            'labels': labels,
            'msg_total': msg_total,
            'msg_private': msg_private,
            'msg_group': msg_group,
            'active_users': active_users,
            'active_groups': active_groups,
            'total_users': total_u,
            'total_groups': total_g,
            'total_friends': total_f,
            'ev_group_join': ev_group_join,
            'ev_group_leave': ev_group_leave,
            'ev_friend_add': ev_friend_add,
            'ev_friend_remove': ev_friend_remove,
        },
    }


def _gather_stats(force=False, selected_date='', appid_filter=''):
    """收集统计数据 — 实时 message.db + 已存 dau.db"""
    now = datetime.now()
    bots_count = len(_bot_manager._bots) if _bot_manager else 0
    date = selected_date or now.strftime('%Y-%m-%d')
    is_today = date == now.strftime('%Y-%m-%d')

    total_msg, priv_msg, n_users, n_groups = 0, 0, 0, 0
    group_msg, user_msg, cmd_msg = {}, {}, {}
    ev = {'group_join_count': 0, 'group_leave_count': 0, 'friend_add_count': 0, 'friend_remove_count': 0}

    top_sql = {
        'groups': "SELECT group_id AS k, COUNT(*) AS c FROM log WHERE group_id!='' AND group_id!='c2c' GROUP BY k ORDER BY c DESC LIMIT 10",
        'users': "SELECT user_id AS k, COUNT(*) AS c FROM log WHERE user_id!='' GROUP BY k ORDER BY c DESC LIMIT 10",
        'cmds': "SELECT plugin_name AS k, COUNT(*) AS c FROM log WHERE plugin_name!='' GROUP BY k ORDER BY c DESC LIMIT 10",
    }

    for _, inst in _iter_bots(appid_filter):
        if is_today:
            with contextlib.suppress(Exception):
                rows = inst.log_service.query(
                    'message',
                    'SELECT COUNT(*) as cnt, '
                    "COUNT(DISTINCT CASE WHEN user_id!='' THEN user_id END) as users, "
                    "COUNT(DISTINCT CASE WHEN group_id!='' AND group_id!='c2c' THEN group_id END) as groups_, "
                    "COUNT(CASE WHEN group_id='c2c' OR group_id='' THEN 1 END) as private "
                    'FROM log', date=date,
                )
                if rows:
                    r = rows[0]
                    total_msg += r.get('cnt', 0)
                    priv_msg += r.get('private', 0)
                    n_users += r.get('users', 0)
                    n_groups += r.get('groups_', 0)
                for label, sql in top_sql.items():
                    dst = {'groups': group_msg, 'users': user_msg, 'cmds': cmd_msg}[label]
                    for r in inst.log_service.query('message', sql, date=date):
                        k = r.get('k', '')
                        if k:
                            dst[k] = dst.get(k, 0) + r.get('c', 0)
        if not is_today:
            with contextlib.suppress(Exception):
                dau = inst.log_service.query('dau', 'SELECT * FROM log WHERE date=?', (date,))
                if dau:
                    d = dau[0]
                    for ek in ev:
                        ev[ek] += d.get(ek, 0)
                    total_msg += d.get('total_messages', 0)
                    priv_msg += d.get('private_messages', 0)
                    n_users += d.get('active_users', 0)
                    n_groups += d.get('active_groups', 0)

    if is_today:
        today_ev = _count_lifecycle_today(appid_filter, date)
        for ek in ev:
            ev[ek] += today_ev.get(ek, 0)

    # 每小时分布
    hourly = _aggregate_hourly(appid_filter, date)
    peak_h = max(hourly, key=hourly.get) if hourly else '00'
    hourly_dist = [hourly.get(f'{h:02d}', 0) for h in range(24)]

    yesterday_dist = None
    if not selected_date:
        yh = _aggregate_hourly(appid_filter, (now - timedelta(days=1)).strftime('%Y-%m-%d'))
        yesterday_dist = [yh.get(f'{h:02d}', 0) for h in range(24)]

    return {
        'today': {
            'message_stats': {
                'total_messages': total_msg, 'private_messages': priv_msg,
                'active_users': n_users, 'active_groups': n_groups,
                'peak_hour': int(peak_h) if peak_h.isdigit() else 0,
                'peak_hour_count': hourly.get(peak_h, 0),
            },
            'hourly_distribution': hourly_dist,
            'yesterday_hourly_distribution': yesterday_dist,
            'top_groups': [{'group_id': k, 'message_count': c} for k, c in sorted(group_msg.items(), key=lambda x: x[1], reverse=True)[:10]],
            'top_users': [{'user_id': k, 'message_count': c} for k, c in sorted(user_msg.items(), key=lambda x: x[1], reverse=True)[:10]],
            'top_commands': [{'command': k, 'count': c} for k, c in sorted(cmd_msg.items(), key=lambda x: x[1], reverse=True)[:10]],
            'event_stats': ev,
            'total_users': _count_table(appid_filter, 'users'),
            'total_groups': _count_table(appid_filter, 'groups_users'),
        },
        'bots_count': bots_count, 'cache_date': date,
    }
