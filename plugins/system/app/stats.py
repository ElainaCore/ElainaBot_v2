"""用户统计: DAU、用户/群统计 (每个机器人独立统计)"""

import asyncio
import json as _json
import time
from datetime import datetime, timedelta

from core.plugin.decorators import handler

from ._reply import reply


def _get_bot(event):
    """获取当前事件对应的 BotInstance"""
    from core.application import get_app

    app = get_app()
    return app.get_bot(event.appid) if app else None


def _mask_id(s, n=3):
    return s if len(s) <= n * 2 else f'{s[:n]}****{s[-n:]}'


def _count_json_array(raw):
    """统计 JSON 数组长度 (不依赖 SQLite JSON 扩展)"""
    if not raw or raw == '[]':
        return 0
    try:
        return len(_json.loads(raw))
    except Exception:
        return 0


def _fmt_diff(label, val, y_val, emoji):
    if y_val is not None:
        diff = val - y_val
        arrow = f'🔺{diff}' if diff > 0 else f'🔻{abs(diff)}' if diff < 0 else '➖0'
        return f'{emoji} {label}: {val} ({arrow})'
    return f'{emoji} {label}: {val}'


def _query_today_stats_sync(bot):
    """实时查询今日消息统计 — 拆分为可走索引的单列查询, 避免整表 COUNT(DISTINCT CASE)"""
    today = datetime.now().strftime('%Y-%m-%d')
    q = bot.log_service.query

    total_rows = q('message', 'SELECT COUNT(*) AS c FROM log', date=today)
    total = total_rows[0]['c'] if total_rows else 0
    if not total:
        return None

    users = q('message', "SELECT COUNT(DISTINCT user_id) AS c FROM log WHERE user_id != ''", date=today)
    groups = q(
        'message',
        "SELECT COUNT(DISTINCT group_id) AS c FROM log WHERE group_id != '' AND group_id != 'c2c'",
        date=today,
    )
    private = q('message', "SELECT COUNT(*) AS c FROM log WHERE group_id = 'c2c' OR group_id = ''", date=today)
    stats = {
        'total': total,
        'users': users[0]['c'] if users else 0,
        'groups_': groups[0]['c'] if groups else 0,
        'private': private[0]['c'] if private else 0,
    }

    peak = q(
        'message',
        'SELECT substr(timestamp, 12, 2) AS hr, COUNT(*) AS c FROM log GROUP BY hr ORDER BY c DESC LIMIT 1',
        date=today,
    )
    stats['peak_hour'] = int(peak[0]['hr']) if peak and peak[0].get('hr') else 0
    stats['peak_hour_count'] = peak[0]['c'] if peak else 0

    stats['top_groups'] = q(
        'message',
        """
        SELECT group_id, COUNT(*) AS c FROM log
        WHERE group_id != '' AND group_id != 'c2c'
        GROUP BY group_id ORDER BY c DESC LIMIT 3
    """,
        date=today,
    )
    stats['top_users'] = q(
        'message',
        "SELECT user_id, COUNT(*) AS c FROM log WHERE user_id != '' GROUP BY user_id ORDER BY c DESC LIMIT 3",
        date=today,
    )
    return stats


def _build_dau_message(event, stats, date, elapsed_ms, y_stats=None, is_today=False):
    """构建 DAU 统计消息"""
    time_suffix = f' (截至{datetime.now().hour:02d}:{datetime.now().minute:02d})' if is_today else ''
    info = [
        f'<@{event.user_id}>',
        f'📊 {date.strftime("%m-%d")} 活跃统计{time_suffix}',
    ]

    y_users = y_stats['users'] if y_stats else None
    y_groups = y_stats['groups_'] if y_stats else None
    y_total = y_stats['total'] if y_stats else None
    y_private = y_stats['private'] if y_stats else None

    info.append(
        _fmt_diff(
            '活跃用户数',
            stats.get('users', stats.get('active_users', 0)),
            y_users,
            '👤',
        )
    )
    info.append(
        _fmt_diff(
            '活跃群聊数',
            stats.get('groups_', stats.get('active_groups', 0)),
            y_groups,
            '👥',
        )
    )
    info.append(
        _fmt_diff(
            '消息总数',
            stats.get('total', stats.get('total_messages', 0)),
            y_total,
            '💬',
        )
    )
    info.append(
        _fmt_diff(
            '私聊消息',
            stats.get('private', stats.get('private_messages', 0)),
            y_private,
            '📱',
        )
    )

    peak_hour = stats.get('peak_hour', 0)
    peak_count = stats.get('peak_hour_count', 0)
    if peak_hour or peak_count:
        info.append(f'⏰ 最活跃时段: {peak_hour}点 ({peak_count}条)')

    # Top 群
    top_groups = stats.get('top_groups', [])
    if top_groups:
        info.append('🔝 最活跃群组:')
        for i, g in enumerate(top_groups[:2], 1):
            gid = g.get('group_id', '')
            cnt = g.get('c', g.get('message_count', 0))
            info.append(f'  {i}. {_mask_id(gid)} ({cnt}条)')

    # Top 用户
    top_users = stats.get('top_users', [])
    if top_users:
        info.append('👑 最活跃用户:')
        for i, u in enumerate(top_users[:2], 1):
            uid = u.get('user_id', '')
            cnt = u.get('c', u.get('message_count', 0))
            info.append(f'  {i}. {_mask_id(uid)} ({cnt}条)')

    info.append(f'🕒 查询耗时: {elapsed_ms}ms')
    return '\n'.join(info)


# ==================== 用户统计 ====================


@handler(r'^用户统计$', name='用户统计', desc='查看当前机器人的用户/群统计', owner_only=True)
async def get_stats(event, match):
    bot = _get_bot(event)
    if not bot:
        return await reply(event, '❌ 无法获取机器人实例')

    t0 = time.time()
    ls = bot.log_service

    # 并行查询 data.db 中的用户/群/好友数
    users_q = ls.db_fetch_value('SELECT COUNT(*) FROM users', default=0)
    groups_q = ls.db_fetch_value('SELECT COUNT(*) FROM groups_users', default=0)
    members_q = ls.db_fetch_value('SELECT COUNT(*) FROM members', default=0)
    all_groups_q = ls.db_fetch_all('SELECT group_id, users FROM groups_users')

    user_count, group_count, member_count, all_groups = await asyncio.gather(users_q, groups_q, members_q, all_groups_q)

    # Python 端统计各群人数 (不依赖 SQLite JSON 扩展)
    group_counts = [(g['group_id'], _count_json_array(g.get('users'))) for g in (all_groups or [])]
    group_counts.sort(key=lambda x: x[1], reverse=True)

    info = [
        f'<@{event.user_id}>',
        f'📊 [{bot.name}] 统计信息',
    ]

    # 当前群成员数
    if event.is_group and event.group_id:
        cur = next((c for gid, c in group_counts if gid == event.group_id), None)
        if cur is not None:
            info.append(f'👥 当前群成员: {cur}')

    info.append(f'👤 好友总数: {member_count}')
    info.append(f'👥 群组总数: {group_count}')
    info.append(f'👥 所有用户数: {user_count}')

    if group_counts:
        gid, cnt = group_counts[0]
        info.append(f'🔝 最大群: {_mask_id(gid)} ({cnt}人)')

    # 当前群排名
    if event.is_group and event.group_id:
        for i, (gid, _) in enumerate(group_counts, 1):
            if gid == event.group_id:
                info.append(f'📈 当前群排名: 第{i}名')
                break

    elapsed = round((time.time() - t0) * 1000)
    info.append(f'🕒 查询耗时: {elapsed}ms')
    await reply(event, '\n'.join(info))


# ==================== DAU ====================


@handler(
    r'^dau(?:\s+)?(\d{4})?$',
    name='DAU',
    desc='查看日活统计 (dau / dau0503)',
    owner_only=True,
)
async def handle_dau(event, match):
    bot = _get_bot(event)
    if not bot:
        return await reply(event, '❌ 无法获取机器人实例')

    date_str = match.group(1)
    if date_str:
        await _handle_history_dau(event, bot, date_str)
    else:
        await _handle_today_dau(event, bot)


async def _handle_today_dau(event, bot):
    t0 = time.time()
    loop = asyncio.get_running_loop()

    stats, y_stats = await asyncio.gather(
        loop.run_in_executor(None, _query_today_stats_sync, bot),
        loop.run_in_executor(None, _query_yesterday_same_period_sync, bot),
    )
    if not stats:
        return await reply(event, f'<@{event.user_id}>\n❌ 今日暂无消息数据')

    elapsed = round((time.time() - t0) * 1000)
    msg = _build_dau_message(event, stats, datetime.now(), elapsed, y_stats=y_stats, is_today=True)
    await reply(event, msg)


def _query_yesterday_same_period_sync(bot):
    """查询昨日同时段统计 — timestamp 直接比较可走索引, 避免 TIME() 逐行函数扫描"""
    now = datetime.now()
    yesterday = (now - timedelta(days=1)).strftime('%Y-%m-%d')
    bound = f'{yesterday} {now.hour:02d}:{now.minute:02d}:00'
    q = bot.log_service.query

    total_rows = q('message', 'SELECT COUNT(*) AS c FROM log WHERE timestamp <= ?', (bound,), date=yesterday)
    total = total_rows[0]['c'] if total_rows else 0
    if not total:
        return None

    users = q(
        'message',
        "SELECT COUNT(DISTINCT user_id) AS c FROM log WHERE user_id != '' AND timestamp <= ?",
        (bound,),
        date=yesterday,
    )
    groups = q(
        'message',
        "SELECT COUNT(DISTINCT group_id) AS c FROM log WHERE group_id != '' AND group_id != 'c2c' AND timestamp <= ?",
        (bound,),
        date=yesterday,
    )
    private = q(
        'message',
        "SELECT COUNT(*) AS c FROM log WHERE (group_id = 'c2c' OR group_id = '') AND timestamp <= ?",
        (bound,),
        date=yesterday,
    )
    return {
        'total': total,
        'users': users[0]['c'] if users else 0,
        'groups_': groups[0]['c'] if groups else 0,
        'private': private[0]['c'] if private else 0,
    }


async def _handle_history_dau(event, bot, date_str):
    """查询历史 DAU (从 dau.db)"""
    t0 = time.time()

    year = datetime.now().year
    month, day = int(date_str[:2]), int(date_str[2:])
    try:
        target = datetime(year, month, day)
        if target > datetime.now():
            target = datetime(year - 1, month, day)
    except ValueError:
        return await reply(event, '❌ 日期格式错误 (MMDD)')

    from core.application import get_app

    app = get_app()
    dau_svc = app.dau_service if app else None
    if not dau_svc:
        return await reply(event, '❌ DAU 服务未启动')

    data = await dau_svc.load(event.appid, target.strftime('%Y-%m-%d'))
    if not data:
        return await reply(event, f'<@{event.user_id}>\n❌ {date_str[:2]}-{date_str[2:]} 无 DAU 数据')

    # 将 dau.db 行转为统计格式
    detail = data.get('message_stats_detail', {})
    if isinstance(detail, str):
        import json

        try:
            detail = json.loads(detail)
        except Exception:
            detail = {}

    stats = {
        'users': data.get('active_users', 0),
        'groups_': data.get('active_groups', 0),
        'total': data.get('total_messages', 0),
        'private': data.get('private_messages', 0),
        'peak_hour': detail.get('peak_hour', 0),
        'peak_hour_count': detail.get('peak_hour_count', 0),
        'top_groups': detail.get('top_groups', []),
        'top_users': detail.get('top_users', []),
    }

    elapsed = round((time.time() - t0) * 1000)
    msg = _build_dau_message(event, stats, target, elapsed)
    await reply(event, msg)
