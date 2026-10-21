"""统计数据同步聚合：消息指标读取持久化汇总，不扫描 message.log。"""

import contextlib
import threading
import time
from datetime import datetime, timedelta

from core.storage.lifecycle_stats import (
    LIFECYCLE_COUNTS_SQL,
    compute_lifecycle_counts,
    lifecycle_counts_from_rows,
)
from web.tools._bots import iter_bots
from web.tools._stats import context
from web.tools._stats.hourly import _aggregate_hourly

_TOTALS_CACHE: dict[tuple[int, str], tuple[float, dict[str, int]]] = {}
_TOTALS_LOCK = threading.Lock()
_METRICS = ('total_messages', 'private_messages', 'received_messages', 'sent_messages', 'active_users', 'active_groups')


def _count_lifecycle_today(appid_filter, date_str):
    """按实体去重的生命周期净变化，优先在 SQL 中聚合。"""
    ev = {'group_join_count': 0, 'group_leave_count': 0, 'friend_add_count': 0, 'friend_remove_count': 0}
    for _, inst in iter_bots(context.bot_manager, appid_filter):
        with contextlib.suppress(Exception):
            try:
                counts = lifecycle_counts_from_rows(inst.log_service.query('lifecycle', LIFECYCLE_COUNTS_SQL, date=date_str))
            except Exception:
                rows = inst.log_service.query('lifecycle', 'SELECT type, user_id, group_id FROM log ORDER BY id', date=date_str)
                counts = compute_lifecycle_counts((r.get('type', ''), r.get('user_id', ''), r.get('group_id', '')) for r in rows)
            for key in ev:
                ev[key] += counts[key]
    return ev


def _count_table(appid_filter, table):
    total = 0
    for _, inst in iter_bots(context.bot_manager, appid_filter):
        rows = inst.log_service.query_data(f'SELECT COUNT(*) AS c FROM {table}')
        if rows:
            total += rows[0].get('c', 0)
    return total


def _day_metrics(date, appid_filter):
    metrics = dict.fromkeys(_METRICS, 0)
    metrics['pending'] = False
    is_today = date == datetime.now().strftime('%Y-%m-%d')
    for _, inst in iter_bots(context.bot_manager, appid_filter):
        rows = [] if is_today else inst.log_service.query('dau', 'SELECT * FROM log WHERE date=?', (date,))
        # 尚未到凌晨统计时间的昨日数据也可以读取消息汇总。
        row = rows[0] if rows else inst.log_service.message_statistics(date)
        metrics['pending'] |= row.get('pending', False)
        for key in _METRICS:
            metrics[key] += row.get(key, 0) or 0
    return metrics


def _gather_hourly_statistics_sync(appid_filter):
    now = datetime.now()
    distributions = []
    pending = False
    for day in (now, now - timedelta(days=1)):
        counts = [0] * 24
        for _, inst in iter_bots(context.bot_manager, appid_filter):
            stats = inst.log_service.message_statistics(day.strftime('%Y-%m-%d'))
            pending |= stats['pending']
            for hour, count in enumerate(stats['hourly_distribution']):
                counts[hour] += count
        distributions.append(counts)
    return {'success': True, 'data': {
        'today_hourly_distribution': distributions[0],
        'yesterday_hourly_distribution': distributions[1],
        'pending': pending,
    }}


def _gather_chart_sync(days, appid_filter):
    data = {key: [] for key in (
        'labels', 'msg_total', 'msg_private', 'msg_group', 'msg_received', 'msg_sent', 'active_users', 'active_groups',
        'ev_group_join', 'ev_group_leave', 'ev_friend_add', 'ev_friend_remove',
    )}
    data['pending'] = False
    today = datetime.now().date()
    for i in range(days - 1, -1, -1):
        day = today - timedelta(days=i)
        date = day.isoformat()
        metrics = _day_metrics(date, appid_filter)
        events = _gather_events(date, appid_filter)
        data['labels'].append(day.strftime('%m-%d'))
        data['pending'] |= metrics['pending']
        for target, source in (
            ('msg_total', 'total_messages'), ('msg_private', 'private_messages'),
            ('msg_received', 'received_messages'), ('msg_sent', 'sent_messages'),
            ('active_users', 'active_users'), ('active_groups', 'active_groups'),
        ):
            data[target].append(None if metrics['pending'] else metrics[source])
        data['msg_group'].append(None if metrics['pending'] else metrics['total_messages'] - metrics['private_messages'])
        for target, source in (
            ('ev_group_join', 'group_join_count'), ('ev_group_leave', 'group_leave_count'),
            ('ev_friend_add', 'friend_add_count'), ('ev_friend_remove', 'friend_remove_count'),
        ):
            data[target].append(events[source])
    # 与 totals 接口共享缓存，避免百万用户表在一次页面加载中重复 COUNT。
    data.update(_gather_totals(appid_filter))
    return {'success': True, 'data': data}


def _gather_summary_active(date, appid_filter):
    metrics = _day_metrics(date, appid_filter)
    summary = {key: metrics[key] for key in _METRICS[:4]}
    summary['bots_count'] = len(context.bot_manager._bots) if context.bot_manager else 0
    summary['pending'] = metrics['pending']
    active = {key: metrics[key] for key in _METRICS[4:]}
    active['pending'] = metrics['pending']
    return summary, active


def _gather_core(date, appid_filter):
    summary, active = _gather_summary_active(date, appid_filter)
    return {'summary': summary, 'active': active, 'pending': summary['pending']}


def _gather_summary(date, appid_filter):
    return _gather_summary_active(date, appid_filter)[0]


def _gather_active(date, appid_filter):
    return _gather_summary_active(date, appid_filter)[1]


def _gather_top(date, appid_filter):
    group_msg, user_msg, cmd_msg = {}, {}, {}
    pending = False
    for _, inst in iter_bots(context.bot_manager, appid_filter):
        stats = inst.log_service.message_statistics(date)
        pending |= stats['pending']
        for name, key, count_key, dst in (
            ('top_groups', 'group_id', 'message_count', group_msg),
            ('top_users', 'user_id', 'message_count', user_msg),
            ('top_commands', 'command', 'count', cmd_msg),
        ):
            for row in stats[name]:
                dst[row[key]] = dst.get(row[key], 0) + row[count_key]
    return {
        'top_groups': [{'group_id': k, 'message_count': c} for k, c in sorted(group_msg.items(), key=lambda x: x[1], reverse=True)[:10]],
        'top_users': [{'user_id': k, 'message_count': c} for k, c in sorted(user_msg.items(), key=lambda x: x[1], reverse=True)[:10]],
        'top_commands': [{'command': k, 'count': c} for k, c in sorted(cmd_msg.items(), key=lambda x: x[1], reverse=True)[:10]],
        'pending': pending,
    }


def _gather_events(date, appid_filter):
    if date == datetime.now().strftime('%Y-%m-%d'):
        return _count_lifecycle_today(appid_filter, date)
    events = {'group_join_count': 0, 'group_leave_count': 0, 'friend_add_count': 0, 'friend_remove_count': 0}
    for _, inst in iter_bots(context.bot_manager, appid_filter):
        for row in inst.log_service.query('dau', 'SELECT * FROM log WHERE date=?', (date,)):
            for key in events:
                events[key] += row.get(key, 0) or 0
    return events


def _gather_totals(appid_filter):
    # COUNT 在百万用户数据上仍需读索引；图表、概览及并发页面共享同一次计数。
    key = (id(context.bot_manager), appid_filter)
    with _TOTALS_LOCK:
        now = time.monotonic()
        entry = _TOTALS_CACHE.get(key)
        if entry and now - entry[0] < 10:
            return entry[1]
        for expired in [k for k, (ts, _) in _TOTALS_CACHE.items() if now - ts >= 10]:
            _TOTALS_CACHE.pop(expired, None)
        totals = {
            'total_users': _count_table(appid_filter, 'users'),
            'total_groups': _count_table(appid_filter, 'groups_users'),
            'total_friends': _count_table(appid_filter, 'members'),
        }
        _TOTALS_CACHE[key] = (time.monotonic(), totals)
        return totals


def _gather_stats(selected_date='', appid_filter=''):
    """兼容旧接口。"""
    now = datetime.now()
    date = selected_date or now.strftime('%Y-%m-%d')
    summary, active = _gather_summary_active(date, appid_filter)
    top = _gather_top(date, appid_filter)
    hourly = _aggregate_hourly(appid_filter, date)
    peak_h = max(hourly, key=hourly.get) if hourly else '00'
    yesterday = None if selected_date else _aggregate_hourly(appid_filter, (now - timedelta(days=1)).strftime('%Y-%m-%d'))
    return {
        'today': {
            'message_stats': {**summary, **active, 'peak_hour': int(peak_h), 'peak_hour_count': hourly.get(peak_h, 0)},
            'hourly_distribution': [hourly.get(f'{h:02d}', 0) for h in range(24)],
            'yesterday_hourly_distribution': None if yesterday is None else [yesterday.get(f'{h:02d}', 0) for h in range(24)],
            **top,
            'event_stats': _gather_events(date, appid_filter),
            **_gather_totals(appid_filter),
        },
        'bots_count': summary['bots_count'],
        'cache_date': date,
        'pending': summary['pending'] or top['pending'],
    }
