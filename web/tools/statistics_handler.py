"""统计数据 — DAU / 消息统计"""

import asyncio
import logging
from datetime import datetime, timedelta

from aiohttp import web

log = logging.getLogger('ElainaBot.web.stats')

_statistics_tasks = {}
_task_results = {}
_bot_manager = None


def set_context(bot_manager):
    global _bot_manager
    _bot_manager = bot_manager


def _iter_bots(appid_filter=''):
    """按 appid 过滤机器人迭代器; 空字符串=全部"""
    if not _bot_manager:
        return []
    if appid_filter and appid_filter in _bot_manager._bots:
        return [(appid_filter, _bot_manager._bots[appid_filter])]
    return list(_bot_manager._bots.items())


def _count_table(appid_filter, table):
    """累计某张表的总行数 (data.db)"""
    total = 0
    for _, inst in _iter_bots(appid_filter):
        try:
            r = inst.log_service.query_data(f"SELECT COUNT(*) as c FROM {table}")
            if r:
                total += r[0].get('c', 0)
        except Exception:
            pass
    return total


def _aggregate_hourly(appid_filter, date):
    """聚合某天的每小时消息分布, 返回 {hour_str: count}"""
    hourly = {}
    for _, inst in _iter_bots(appid_filter):
        try:
            rows = inst.log_service.query(
                'message',
                "SELECT substr(timestamp, 12, 2) AS hr, COUNT(*) AS c FROM log GROUP BY hr",
                date=date)
            for r in rows:
                h = r.get('hr', '')
                if h:
                    hourly[h] = hourly.get(h, 0) + r.get('c', 0)
        except Exception:
            pass
    return hourly


async def handle_get_statistics(request: web.Request):
    """获取统计数据"""
    force = request.query.get('force_refresh', 'false') == 'true'
    selected_date = request.query.get('date', '')
    appid_filter = request.query.get('appid', '')

    try:
        data = _gather_stats(force, selected_date, appid_filter)
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
    return web.json_response({'success': True, 'status': task['status'],
                              'progress': task.get('progress', 0),
                              'message': task.get('message', '')})


async def handle_get_available_dates(request: web.Request):
    """返回有 DAU 数据的日期列表"""
    dates = [{'value': 'today', 'date': datetime.now().strftime('%Y-%m-%d'),
              'display': '今日数据', 'is_today': True}]
    return web.json_response({'success': True, 'dates': dates})


async def handle_get_chart_data(request: web.Request):
    """返回最近 N 天的折线图数据"""
    days = max(1, min(30, int(request.query.get('days', '7'))))
    appid_filter = request.query.get('appid', '')

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

        for _appid, inst in _iter_bots(appid_filter):
            # 从 message.db 查消息量 + 活跃
            try:
                rows = inst.log_service.query(
                    'message',
                    "SELECT COUNT(*) as cnt, "
                    "COUNT(CASE WHEN group_id = '' OR group_id = 'c2c' THEN 1 END) as priv "
                    "FROM log WHERE user_id != ''",
                    date=date_str)
                if rows:
                    day_total += rows[0].get('cnt', 0)
                    day_private += rows[0].get('priv', 0)
                uid_rows = inst.log_service.query(
                    'message',
                    "SELECT DISTINCT user_id FROM log WHERE user_id != ''",
                    date=date_str)
                for r in uid_rows:
                    day_users.add(r.get('user_id', ''))
                gid_rows = inst.log_service.query(
                    'message',
                    "SELECT DISTINCT group_id FROM log WHERE group_id != '' AND group_id != 'c2c'",
                    date=date_str)
                for r in gid_rows:
                    day_groups.add(r.get('group_id', ''))
            except Exception:
                pass
            # 从 dau.db 查事件
            try:
                dau_rows = inst.log_service.query(
                    'dau', "SELECT * FROM log WHERE date=?", (date_str,))
                if dau_rows:
                    dd = dau_rows[0]
                    day_join += dd.get('group_join_count', 0)
                    day_leave += dd.get('group_leave_count', 0)
                    day_fadd += dd.get('friend_add_count', 0)
                    day_frem += dd.get('friend_remove_count', 0)
            except Exception:
                pass

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

    return web.json_response({
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
        }
    })


def _gather_stats(force=False, selected_date='', appid_filter=''):
    """收集统计数据 — 从 SQLite 查询 (实时 message.db + 已存 dau.db)"""
    now = datetime.now()
    bots_count = len(_bot_manager._bots) if _bot_manager else 0
    date = selected_date or now.strftime('%Y-%m-%d')

    total_messages = 0
    private_messages = 0
    active_users = set()
    active_groups = set()
    group_msg = {}       # {gid: count}
    user_msg = {}        # {uid: count}
    cmd_msg = {}         # {plugin_name: count}

    event_stats = {'group_join_count': 0, 'group_leave_count': 0,
                   'friend_add_count': 0, 'friend_remove_count': 0}

    if _bot_manager:
        for appid, inst in _iter_bots(appid_filter):
            try:
                rows = inst.log_service.query(
                    'message',
                    "SELECT COUNT(*) as cnt, "
                    "COUNT(DISTINCT CASE WHEN user_id != '' THEN user_id END) as users, "
                    "COUNT(DISTINCT CASE WHEN group_id != '' AND group_id != 'c2c' THEN group_id END) as groups_, "
                    "COUNT(CASE WHEN group_id = 'c2c' OR group_id = '' THEN 1 END) as private "
                    "FROM log",
                    date=date)
                if rows:
                    r = rows[0]
                    total_messages += r.get('cnt', 0)
                    private_messages += r.get('private', 0)

                uid_rows = inst.log_service.query(
                    'message', "SELECT DISTINCT user_id FROM log WHERE user_id != ''",
                    date=date)
                gid_rows = inst.log_service.query(
                    'message', "SELECT DISTINCT group_id FROM log WHERE group_id != '' AND group_id != 'c2c'",
                    date=date)
                for ur in uid_rows:
                    active_users.add(ur.get('user_id', ''))
                for gr in gid_rows:
                    active_groups.add(gr.get('group_id', ''))

                # 每小时消息分布 (在主循环后统一聚合)

                # Top 群
                g_rows = inst.log_service.query(
                    'message',
                    "SELECT group_id, COUNT(*) AS c FROM log "
                    "WHERE group_id != '' AND group_id != 'c2c' GROUP BY group_id ORDER BY c DESC LIMIT 10",
                    date=date)
                for r in g_rows:
                    gid = r.get('group_id', '')
                    if gid:
                        group_msg[gid] = group_msg.get(gid, 0) + r.get('c', 0)

                # Top 用户
                u_rows = inst.log_service.query(
                    'message',
                    "SELECT user_id, COUNT(*) AS c FROM log WHERE user_id != '' GROUP BY user_id ORDER BY c DESC LIMIT 10",
                    date=date)
                for r in u_rows:
                    uid = r.get('user_id', '')
                    if uid:
                        user_msg[uid] = user_msg.get(uid, 0) + r.get('c', 0)

                # Top 命令
                c_rows = inst.log_service.query(
                    'message',
                    "SELECT plugin_name, COUNT(*) AS c FROM log WHERE plugin_name != '' GROUP BY plugin_name ORDER BY c DESC LIMIT 10",
                    date=date)
                for r in c_rows:
                    cmd = r.get('plugin_name', '')
                    if cmd:
                        cmd_msg[cmd] = cmd_msg.get(cmd, 0) + r.get('c', 0)
            except Exception:
                pass

            try:
                dau_rows = inst.log_service.query(
                    'dau', "SELECT * FROM log WHERE date=?", (date,))
                if dau_rows:
                    d = dau_rows[0]
                    event_stats['group_join_count'] += d.get('group_join_count', 0)
                    event_stats['group_leave_count'] += d.get('group_leave_count', 0)
                    event_stats['friend_add_count'] += d.get('friend_add_count', 0)
                    event_stats['friend_remove_count'] += d.get('friend_remove_count', 0)
            except Exception:
                pass

    # 每小时分布
    hourly = _aggregate_hourly(appid_filter, date)

    # 高峰时段
    peak_hour = 0
    peak_hour_count = 0
    if hourly:
        peak_h = max(hourly, key=hourly.get)
        peak_hour = int(peak_h) if peak_h.isdigit() else 0
        peak_hour_count = hourly[peak_h]

    # 每小时分布 (24h)
    hourly_dist = [hourly.get(f'{h:02d}', 0) for h in range(24)]

    # 昨日每小时分布 (供前端 12 小时图跨越零点)
    yesterday_hourly_dist = None
    if not selected_date:
        yesterday = (now - timedelta(days=1)).strftime('%Y-%m-%d')
        yh = _aggregate_hourly(appid_filter, yesterday)
        yesterday_hourly_dist = [yh.get(f'{h:02d}', 0) for h in range(24)]

    top_groups = sorted(group_msg.items(), key=lambda x: x[1], reverse=True)[:10]
    top_users = sorted(user_msg.items(), key=lambda x: x[1], reverse=True)[:10]
    top_commands = sorted(cmd_msg.items(), key=lambda x: x[1], reverse=True)[:10]

    # 累计用户数 / 群数 (从 data.db)
    total_users_all = _count_table(appid_filter, 'users')
    total_groups_all = _count_table(appid_filter, 'groups_users')

    return {
        'today': {
            'message_stats': {
                'total_messages': total_messages,
                'private_messages': private_messages,
                'active_users': len(active_users),
                'active_groups': len(active_groups),
                'peak_hour': peak_hour,
                'peak_hour_count': peak_hour_count,
            },
            'hourly_distribution': hourly_dist,
            'yesterday_hourly_distribution': yesterday_hourly_dist,
            'top_groups': [{'group_id': g, 'message_count': c} for g, c in top_groups],
            'top_users': [{'user_id': u, 'message_count': c} for u, c in top_users],
            'top_commands': [{'command': cmd, 'count': c} for cmd, c in top_commands],
            'event_stats': event_stats,
            'total_users': total_users_all,
            'total_groups': total_groups_all,
        },
        'bots_count': bots_count,
        'cache_date': date,
    }
