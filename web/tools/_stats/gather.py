"""统计数据同步聚合 (executor 中调用)"""

import contextlib
from datetime import datetime, timedelta

from core.storage.lifecycle_stats import compute_lifecycle_counts
from web.tools._bots import iter_bots
from web.tools._stats import context
from web.tools._stats.hourly import _aggregate_hourly


def _count_lifecycle_today(appid_filter, date_str):
    """从 lifecycle.db 实时统计今日进群/退群/加好友/删好友事件 (同一群/好友去重)"""
    ev = {'group_join_count': 0, 'group_leave_count': 0, 'friend_add_count': 0, 'friend_remove_count': 0}
    for _, inst in iter_bots(context.bot_manager, appid_filter):
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
    for _, inst in iter_bots(context.bot_manager, appid_filter):
        try:
            r = inst.log_service.query_data(f'SELECT COUNT(*) as c FROM {table}')
            if r:
                total += r[0].get('c', 0)
        except Exception:
            pass
    return total


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
        for _appid, inst in iter_bots(context.bot_manager, appid_filter):
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
    bots_count = len(context.bot_manager._bots) if context.bot_manager else 0
    total_msg, priv_msg, recv_msg, sent_msg = 0, 0, 0, 0

    for _, inst in iter_bots(context.bot_manager, appid_filter):
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

    for _, inst in iter_bots(context.bot_manager, appid_filter):
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

    for _, inst in iter_bots(context.bot_manager, appid_filter):
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
        for _, inst in iter_bots(context.bot_manager, appid_filter):
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
        'bots_count': len(context.bot_manager._bots) if context.bot_manager else 0,
    }

    for _, inst in iter_bots(context.bot_manager, appid_filter):
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
