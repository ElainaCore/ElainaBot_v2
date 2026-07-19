"""统计数据 — DAU / 消息统计"""

import asyncio
import contextlib
import json
import logging
import os
import time
from collections.abc import Hashable
from datetime import datetime, timedelta
from typing import Any, TypeVar

from aiohttp import web

from core.storage.lifecycle_stats import compute_lifecycle_counts
from web.tools._bots import iter_bots

log = logging.getLogger('ElainaBot.web.stats')

_statistics_tasks: dict[str, dict[str, Any]] = {}
_task_results: dict[str, dict[str, Any]] = {}
_bot_manager: object | None = None
_base_dir: str = ''

# 简单内存缓存: {(date, appid_filter): (timestamp, data)} — 避免短时间内重复全表扫描
_stats_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_chart_cache: dict[tuple[int, str], tuple[float, dict[str, Any]]] = {}
_core_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_CACHE_TTL = 10  # 秒


_hourly_cache: dict[str, dict[str, int]] = {}
_hourly_task: asyncio.Task | None = None
_HOURLY_SQL = 'SELECT substr(timestamp,12,2) AS hr, COUNT(*) AS c FROM log GROUP BY hr'
_K = TypeVar('_K', bound=Hashable)
_V = TypeVar('_V')


def _cached(cache: dict[_K, tuple[float, _V]], key: _K) -> _V | None:
    entry = cache.get(key)
    if entry and time.monotonic() - entry[0] < _CACHE_TTL:
        return entry[1]
    return None


def _store_cached(cache: dict[_K, tuple[float, _V]], key: _K, value: _V) -> _V:
    cache[key] = (time.monotonic(), value)
    return value


def _hourly_path():
    return os.path.join(_base_dir, 'data', 'hourly_cache.json')


def _save_hourly_cache():
    p = _hourly_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with contextlib.suppress(Exception), open(p, 'w', encoding='utf-8') as f:
        json.dump(_hourly_cache, f, ensure_ascii=False)


def _query_hourly_from_db(date, where='', appid_filter=''):
    """从 DB 查某天每小时分布, where 可附加过滤条件"""
    sql = _HOURLY_SQL if not where else _HOURLY_SQL.replace('GROUP BY', f'{where} GROUP BY')
    hourly = {}
    for _, inst in iter_bots(_bot_manager, appid_filter):
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

    # 昨日缓存可能缺少 23 点数据 (跨天时快照循环不会补齐), 未终结时从 DB 全量补齐并打上 _final 标记
    yc = _hourly_cache.get(yesterday)
    if not yc or not yc.get('_final'):
        fresh = _query_hourly_from_db(yesterday)
        if fresh:
            fresh['_final'] = 1
            yc = fresh
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


def _count_lifecycle_today(appid_filter, date_str):
    """从 lifecycle.db 实时统计今日进群/退群/加好友/删好友事件 (同一群/好友去重)"""
    ev = {'group_join_count': 0, 'group_leave_count': 0, 'friend_add_count': 0, 'friend_remove_count': 0}
    for _, inst in iter_bots(_bot_manager, appid_filter):
        with contextlib.suppress(Exception):
            rows = inst.log_service.query(
                'lifecycle',
                'SELECT type, user_id, group_id FROM log ORDER BY id',
                date=date_str,
            )
            counts = compute_lifecycle_counts(
                (r.get('type', ''), r.get('user_id', ''), r.get('group_id', '')) for r in rows
            )
            for key in ev:
                ev[key] += counts[key]
    return ev


def _count_table(appid_filter, table):
    """累计某张表的总行数 (data.db)"""
    total = 0
    for _, inst in iter_bots(_bot_manager, appid_filter):
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
        hourly.pop('_final', None)
        if date == today:
            # 补充当前小时实时数据
            cur_h = f'{now.hour:02d}'
            cnt = 0
            for _, inst in iter_bots(_bot_manager):
                with contextlib.suppress(Exception):
                    rows = inst.log_service.query(
                        'message',
                        'SELECT COUNT(*) AS c FROM log WHERE substr(timestamp,12,2)=?',
                        (cur_h,),
                        date=date,
                    )
                    if rows:
                        cnt += rows[0].get('c', 0)
            hourly[cur_h] = cnt
        return hourly

    # 无缓存 / 有 appid 过滤: 全量查
    return _query_hourly_from_db(date, appid_filter=appid_filter)


async def handle_get_statistics(request: web.Request):
    """获取统计数据 — SQLite 查询放到 executor, 不阻塞事件循环"""
    force = request.query.get('force_refresh', 'false') == 'true'
    selected_date = request.query.get('date', '')
    appid_filter = request.query.get('appid', '')

    cache_key = (selected_date, appid_filter)
    if not force:
        cached = _cached(_stats_cache, cache_key)
        if cached is not None:
            return web.json_response({'success': True, 'data': cached})

    try:
        data = await asyncio.to_thread(_gather_stats, selected_date, appid_filter)
        _store_cached(_stats_cache, cache_key, data)
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
    days = max(1, min(30, int(request.query.get('days', '7'))))
    appid_filter = request.query.get('appid', '')

    cache_key = (days, appid_filter)
    cached = _cached(_chart_cache, cache_key)
    if cached is not None:
        return web.json_response(cached)

    payload = await asyncio.to_thread(_gather_chart_sync, days, appid_filter)
    _store_cached(_chart_cache, cache_key, payload)
    return web.json_response(payload)


async def handle_get_hourly_statistics(request: web.Request):
    """仪表盘轻量小时分布 — 避免进入仪表盘时触发完整统计扫描。"""
    appid_filter = request.query.get('appid', '')
    cache_key = (0, appid_filter)
    cached = _cached(_chart_cache, cache_key)
    if cached is not None:
        return web.json_response(cached)

    payload = await asyncio.to_thread(_gather_hourly_statistics_sync, appid_filter)
    _store_cached(_chart_cache, cache_key, payload)
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
    msg_received = []
    msg_sent = []
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
        day_received = 0
        day_sent = 0
        day_users = set()
        day_groups = set()
        day_join = 0
        day_leave = 0
        day_fadd = 0
        day_frem = 0

        is_today = d == today_date
        for _appid, inst in iter_bots(_bot_manager, appid_filter):
            if is_today:
                # 今日: 实时读 message.db (合并查询, 一次扫表得到全部聚合)
                try:
                    rows = inst.log_service.query(
                        'message',
                        'SELECT COUNT(*) as cnt, '
                        "COUNT(CASE WHEN group_id = '' OR group_id = 'c2c' THEN 1 END) as priv, "
                        "COUNT(DISTINCT CASE WHEN user_id != '' AND direction != 'send' AND COALESCE(at_bot, 1) != 0 THEN user_id END) as users, "
                        "COUNT(DISTINCT CASE WHEN group_id != '' AND group_id != 'c2c' AND direction != 'send' AND COALESCE(at_bot, 1) != 0 THEN group_id END) as groups_ "
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
                    rows = inst.log_service.query(
                        'message',
                        "SELECT COUNT(CASE WHEN direction = 'receive' THEN 1 END) as received, "
                        "COUNT(CASE WHEN direction = 'send' THEN 1 END) as sent FROM log",
                        date=date_str,
                    )
                    if rows:
                        day_received += rows[0].get('received', 0)
                        day_sent += rows[0].get('sent', 0)
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
                        day_received += dd.get('received_messages', 0) or 0
                        day_sent += dd.get('sent_messages', 0) or 0
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
        msg_received.append(day_received)
        msg_sent.append(day_sent)
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
            'msg_received': msg_received,
            'msg_sent': msg_sent,
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


def _gather_stats(selected_date='', appid_filter=''):
    """收集统计数据 — 实时 message.db + 已存 dau.db (兼容旧接口, 汇总所有子查询)"""
    now = datetime.now()
    date = selected_date or now.strftime('%Y-%m-%d')

    summary = _gather_summary(date, appid_filter)
    active = _gather_active(date, appid_filter)
    top = _gather_top(date, appid_filter)
    events = _gather_events(date, appid_filter)
    totals = _gather_totals(appid_filter)

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
                'total_messages': summary['total_messages'],
                'private_messages': summary['private_messages'],
                'received_messages': summary['received_messages'],
                'sent_messages': summary['sent_messages'],
                'active_users': active['active_users'],
                'active_groups': active['active_groups'],
                'peak_hour': int(peak_h) if peak_h.isdigit() else 0,
                'peak_hour_count': hourly.get(peak_h, 0),
            },
            'hourly_distribution': hourly_dist,
            'yesterday_hourly_distribution': yesterday_dist,
            'top_groups': top['top_groups'],
            'top_users': top['top_users'],
            'top_commands': top['top_commands'],
            'event_stats': events,
            'total_users': totals['total_users'],
            'total_groups': totals['total_groups'],
        },
        'bots_count': summary['bots_count'],
        'cache_date': date,
    }


def _gather_summary(date, appid_filter):
    """消息总量 / 私聊量 — 单次 COUNT, 不含 DISTINCT, 快"""
    now = datetime.now()
    is_today = date == now.strftime('%Y-%m-%d')
    bots_count = len(_bot_manager._bots) if _bot_manager else 0
    total_msg, priv_msg, recv_msg, sent_msg = 0, 0, 0, 0

    for _, inst in iter_bots(_bot_manager, appid_filter):
        if is_today:
            with contextlib.suppress(Exception):
                rows = inst.log_service.query(
                    'message',
                    "SELECT COUNT(*) as cnt, COUNT(CASE WHEN group_id='c2c' OR group_id='' THEN 1 END) as private, "
                    "COUNT(CASE WHEN direction='receive' THEN 1 END) as received, "
                    "COUNT(CASE WHEN direction='send' THEN 1 END) as sent FROM log",
                    date=date,
                )
                if rows:
                    total_msg += rows[0].get('cnt', 0)
                    priv_msg += rows[0].get('private', 0)
                    recv_msg += rows[0].get('received', 0)
                    sent_msg += rows[0].get('sent', 0)
        else:
            with contextlib.suppress(Exception):
                dau = inst.log_service.query('dau', 'SELECT * FROM log WHERE date=?', (date,))
                if dau:
                    total_msg += dau[0].get('total_messages', 0)
                    priv_msg += dau[0].get('private_messages', 0)
                    recv_msg += dau[0].get('received_messages', 0) or 0
                    sent_msg += dau[0].get('sent_messages', 0) or 0

    return {
        'total_messages': total_msg,
        'private_messages': priv_msg,
        'received_messages': recv_msg,
        'sent_messages': sent_msg,
        'bots_count': bots_count,
    }


def _gather_active(date, appid_filter):
    """活跃用户 / 群数 — COUNT(DISTINCT), 百万行级最慢的查询"""
    now = datetime.now()
    is_today = date == now.strftime('%Y-%m-%d')
    n_users, n_groups = 0, 0

    for _, inst in iter_bots(_bot_manager, appid_filter):
        if is_today:
            with contextlib.suppress(Exception):
                rows = inst.log_service.query(
                    'message',
                    'SELECT '
                    "COUNT(DISTINCT CASE WHEN user_id!='' AND direction!='send' AND COALESCE(at_bot,1)!=0 THEN user_id END) as users, "
                    "COUNT(DISTINCT CASE WHEN group_id!='' AND group_id!='c2c' AND direction!='send' AND COALESCE(at_bot,1)!=0 THEN group_id END) as groups_ "
                    'FROM log',
                    date=date,
                )
                if rows:
                    n_users += rows[0].get('users', 0)
                    n_groups += rows[0].get('groups_', 0)
        else:
            with contextlib.suppress(Exception):
                dau = inst.log_service.query('dau', 'SELECT * FROM log WHERE date=?', (date,))
                if dau:
                    n_users += dau[0].get('active_users', 0)
                    n_groups += dau[0].get('active_groups', 0)

    return {'active_users': n_users, 'active_groups': n_groups}


def _gather_top(date, appid_filter):
    """TOP 排行 — GROUP BY + ORDER BY, 有索引后较快"""
    now = datetime.now()
    is_today = date == now.strftime('%Y-%m-%d')
    group_msg, user_msg, cmd_msg = {}, {}, {}

    top_sql = {
        'groups': "SELECT group_id AS k, COUNT(*) AS c FROM log WHERE group_id!='' AND group_id!='c2c' AND direction!='send' AND COALESCE(at_bot,1)!=0 GROUP BY k ORDER BY c DESC LIMIT 10",
        'users': "SELECT user_id AS k, COUNT(*) AS c FROM log WHERE user_id!='' AND direction!='send' AND COALESCE(at_bot,1)!=0 GROUP BY k ORDER BY c DESC LIMIT 10",
        # plugin_name 仅在回复日志 (direction='send') 中写入
        'cmds': "SELECT plugin_name AS k, COUNT(*) AS c FROM log WHERE plugin_name!='' AND direction='send' GROUP BY k ORDER BY c DESC LIMIT 10",
    }

    for _, inst in iter_bots(_bot_manager, appid_filter):
        if is_today:
            for label, sql in top_sql.items():
                dst = {'groups': group_msg, 'users': user_msg, 'cmds': cmd_msg}[label]
                with contextlib.suppress(Exception):
                    for r in inst.log_service.query('message', sql, date=date):
                        k = r.get('k', '')
                        if k:
                            dst[k] = dst.get(k, 0) + r.get('c', 0)

    return {
        'top_groups': [{'group_id': k, 'message_count': c} for k, c in sorted(group_msg.items(), key=lambda x: x[1], reverse=True)[:10]],
        'top_users': [{'user_id': k, 'message_count': c} for k, c in sorted(user_msg.items(), key=lambda x: x[1], reverse=True)[:10]],
        'top_commands': [{'command': k, 'count': c} for k, c in sorted(cmd_msg.items(), key=lambda x: x[1], reverse=True)[:10]],
    }


def _gather_events(date, appid_filter):
    """生命周期事件统计 — lifecycle.db 小表, 快"""
    now = datetime.now()
    is_today = date == now.strftime('%Y-%m-%d')
    ev = {'group_join_count': 0, 'group_leave_count': 0, 'friend_add_count': 0, 'friend_remove_count': 0}

    if is_today:
        today_ev = _count_lifecycle_today(appid_filter, date)
        for ek in ev:
            ev[ek] += today_ev.get(ek, 0)
    else:
        for _, inst in iter_bots(_bot_manager, appid_filter):
            with contextlib.suppress(Exception):
                dau = inst.log_service.query('dau', 'SELECT * FROM log WHERE date=?', (date,))
                if dau:
                    for ek in ev:
                        ev[ek] += dau[0].get(ek, 0)
    return ev


def _gather_totals(appid_filter):
    """累计用户 / 群组数 — data.db 小表 COUNT, 快"""
    return {
        'total_users': _count_table(appid_filter, 'users'),
        'total_groups': _count_table(appid_filter, 'groups_users'),
    }


def _gather_summary_active(date, appid_filter):
    is_today = date == datetime.now().strftime('%Y-%m-%d')
    metrics = {
        'total_messages': 0,
        'private_messages': 0,
        'received_messages': 0,
        'sent_messages': 0,
        'active_users': 0,
        'active_groups': 0,
        'bots_count': len(_bot_manager._bots) if _bot_manager else 0,
    }

    for _, inst in iter_bots(_bot_manager, appid_filter):
        with contextlib.suppress(Exception):
            if is_today:
                rows = inst.log_service.query(
                    'message',
                    'SELECT COUNT(*) as cnt, '
                    "COUNT(CASE WHEN group_id='c2c' OR group_id='' THEN 1 END) as private, "
                    "COUNT(CASE WHEN direction='receive' THEN 1 END) as received, "
                    "COUNT(CASE WHEN direction='send' THEN 1 END) as sent, "
                    "COUNT(DISTINCT CASE WHEN user_id!='' AND direction!='send' AND COALESCE(at_bot,1)!=0 THEN user_id END) as users, "
                    "COUNT(DISTINCT CASE WHEN group_id!='' AND group_id!='c2c' AND direction!='send' AND COALESCE(at_bot,1)!=0 THEN group_id END) as groups_ "
                    'FROM log',
                    date=date,
                )
                if not rows:
                    continue
                row = rows[0]
                metrics['total_messages'] += row.get('cnt', 0)
                metrics['private_messages'] += row.get('private', 0)
                metrics['received_messages'] += row.get('received', 0)
                metrics['sent_messages'] += row.get('sent', 0)
                metrics['active_users'] += row.get('users', 0)
                metrics['active_groups'] += row.get('groups_', 0)
            else:
                rows = inst.log_service.query('dau', 'SELECT * FROM log WHERE date=?', (date,))
                if not rows:
                    continue
                row = rows[0]
                metrics['total_messages'] += row.get('total_messages', 0)
                metrics['private_messages'] += row.get('private_messages', 0)
                metrics['received_messages'] += row.get('received_messages', 0) or 0
                metrics['sent_messages'] += row.get('sent_messages', 0) or 0
                metrics['active_users'] += row.get('active_users', 0)
                metrics['active_groups'] += row.get('active_groups', 0)

    return (
        {
            'total_messages': metrics['total_messages'],
            'private_messages': metrics['private_messages'],
            'received_messages': metrics['received_messages'],
            'sent_messages': metrics['sent_messages'],
            'bots_count': metrics['bots_count'],
        },
        {
            'active_users': metrics['active_users'],
            'active_groups': metrics['active_groups'],
        },
    )


def _gather_core(date, appid_filter):
    summary, active = _gather_summary_active(date, appid_filter)
    return {'summary': summary, 'active': active}


async def handle_get_core(request: web.Request):
    date = request.query.get('date', '') or datetime.now().strftime('%Y-%m-%d')
    appid = request.query.get('appid', '')
    cache_key = (date, appid)
    cached = _cached(_core_cache, cache_key)
    if cached is not None:
        return web.json_response({'success': True, 'data': cached})
    try:
        data = await asyncio.to_thread(_gather_core, date, appid)
        _store_cached(_core_cache, cache_key, data)
        return web.json_response({'success': True, 'data': data})
    except Exception as e:
        return web.json_response({'success': False, 'error': str(e)}, status=500)


async def _handle_stat_query(request, gatherer, include_date=True):
    appid = request.query.get('appid', '')
    args = (request.query.get('date', '') or datetime.now().strftime('%Y-%m-%d'), appid) if include_date else (appid,)
    try:
        data = await asyncio.to_thread(gatherer, *args)
        return web.json_response({'success': True, 'data': data})
    except Exception as e:
        return web.json_response({'success': False, 'error': str(e)}, status=500)


async def handle_get_summary(request: web.Request):
    """消息总量 (快)"""
    return await _handle_stat_query(request, _gather_summary)


async def handle_get_active(request: web.Request):
    """活跃用户/群 (慢 — COUNT DISTINCT)"""
    return await _handle_stat_query(request, _gather_active)


async def handle_get_top(request: web.Request):
    """TOP 排行 (中等)"""
    return await _handle_stat_query(request, _gather_top)


async def handle_get_events(request: web.Request):
    """生命周期事件 (快)"""
    return await _handle_stat_query(request, _gather_events)


async def handle_get_totals(request: web.Request):
    """累计用户/群组 (快)"""
    return await _handle_stat_query(request, _gather_totals, include_date=False)
