"""用户、群组和日活统计，每个机器人独立计算。"""

import asyncio
import json as _json
import struct
from datetime import datetime, timedelta
from time import perf_counter

from core.base.config import cfg
from core.base.logger import PLUGIN, get_logger
from core.plugin.decorators import handler
from core.storage.lifecycle_stats import (
    LIFECYCLE_COUNTS_SQL,
    compute_lifecycle_counts,
    lifecycle_counts_from_rows,
)

from ._dau_image import render_dau_image
from ._reply import reply
from ._shared import mask_id

log = get_logger(PLUGIN, '系统管理')


def _get_bot(event):
    """获取当前事件对应的机器人实例。"""
    from core.application import get_app

    app = get_app()
    return app.get_bot(event.appid) if app else None


def _get_hosting():
    """获取图床模块实例；未启用时返回空值。"""
    from core.application import get_app

    app = get_app()
    mm = app.module_manager if app else None
    return mm.get('image_hosting') if mm else None


async def _upload_dau_image(bot, image_bytes):
    """依次尝试已启用的图床，全部失败时返回空值。"""
    hosting = _get_hosting()
    if not hosting:
        return None
    return await hosting.upload_any(image_bytes, 'dau_stats.png', token_manager=bot.token_manager)


async def _reply_dau(event, bot, stats, date, elapsed_ms, y_stats=None, is_today=False):
    """优先发送统计图片，图床不可用时回退到文本。"""
    now = datetime.now()
    time_suffix = f' (截至{now.hour:02d}:{now.minute:02d})' if is_today else ''
    try:
        image = await asyncio.to_thread(
            render_dau_image,
            stats,
            f'{date.strftime("%m-%d")} 活跃统计',
            sub_title=f'{bot.name}{time_suffix}',
            y_stats=y_stats,
            elapsed_ms=elapsed_ms,
        )
    except Exception:
        image = None
    if image:
        url = await _upload_dau_image(bot, image)
        if url:
            if cfg.get_bot_setting(event.appid, 'message.use_markdown', True):
                w, h = _png_size(image)
                md = f'<@{event.user_id}>\n![活跃统计 #{w}px #{h}px]({url})'
                return await reply(event, md)
            return await event.reply_image(url, f'<@{event.user_id}>')
    msg = _build_dau_message(event, stats, date, elapsed_ms, y_stats=y_stats, is_today=is_today)
    await reply(event, msg)


def _png_size(data):
    """从图片头部读取宽高。"""
    return struct.unpack('>II', data[16:24])


def _count_json_array(raw):
    """统计 JSON 数组长度，不依赖 SQLite 的 JSON 扩展。"""
    if not raw or raw == '[]':
        return 0
    try:
        return len(_json.loads(raw))
    except Exception:
        return 0


def _count_group_users(all_groups):
    """逐群解析用户列表并按人数降序排列。"""
    counts = [(g['group_id'], _count_json_array(g.get('users'))) for g in (all_groups or [])]
    counts.sort(key=lambda x: x[1], reverse=True)
    return counts


# 单次扫描完成当前群人数、最大群和排名，避免搬运全部用户数据
_GROUP_STATS_SQL = """
    WITH c AS MATERIALIZED (
        SELECT group_id,
               CASE WHEN users IS NULL OR users = '' OR users = '[]' THEN 0
                    ELSE json_array_length(users) END AS cnt
        FROM groups_users
    )
    SELECT (SELECT cnt FROM c WHERE group_id = ?1) AS cur,
           (SELECT group_id FROM c ORDER BY cnt DESC LIMIT 1) AS top_gid,
           (SELECT MAX(cnt) FROM c) AS top_cnt,
           (SELECT COUNT(*) + 1 FROM c
            WHERE cnt > (SELECT cnt FROM c WHERE group_id = ?1)) AS rank
"""


async def _query_group_stats(ls, cur_gid):
    """查询当前群人数、最大群及排名，数据库不支持时回退到本地统计。"""
    try:
        rows = await ls.db_fetch_all(_GROUP_STATS_SQL, (cur_gid or '',))
        r = rows[0] if rows else {}
        return r.get('cur'), r.get('top_gid'), r.get('top_cnt'), r.get('rank')
    except Exception:
        all_groups = await ls.db_fetch_all('SELECT group_id, users FROM groups_users')
        counts = await asyncio.to_thread(_count_group_users, all_groups)
        cur = next((c for gid, c in counts if gid == cur_gid), None)
        rank = next((i for i, (gid, _) in enumerate(counts, 1) if gid == cur_gid), None)
        top_gid, top_cnt = counts[0] if counts else (None, None)
        return cur, top_gid, top_cnt, rank


def _fmt_diff(label, val, y_val, emoji):
    if y_val is not None:
        diff = val - y_val
        arrow = f'🔺{diff}' if diff > 0 else f'🔻{abs(diff)}' if diff < 0 else '➖0'
        return f'{emoji} {label}: {val} ({arrow})'
    return f'{emoji} {label}: {val}'


# 活跃统计只计算接收消息，全量群只计算提及机器人的消息
_RECV = "direction != 'send' AND COALESCE(at_bot, 1) != 0"

# 使用覆盖索引在单次扫描中完成全部计数，避免回表
_AGG_SQL = f"""
    SELECT COUNT(*) AS total,
           COUNT(CASE WHEN group_id = 'c2c' OR group_id = '' THEN 1 END) AS private,
           COUNT(CASE WHEN direction = 'receive' THEN 1 END) AS received,
           COUNT(CASE WHEN direction = 'send' THEN 1 END) AS sent,
           COUNT(DISTINCT CASE WHEN user_id != '' AND {_RECV} THEN user_id END) AS users,
           COUNT(DISTINCT CASE WHEN group_id != '' AND group_id != 'c2c' AND {_RECV}
                               THEN group_id END) AS groups_
    FROM log
"""


def _query_today_stats_sync(bot):
    """用少量覆盖索引扫描实时查询今日消息统计。"""
    today = datetime.now().strftime('%Y-%m-%d')
    q = bot.log_service.query

    agg = q('message', _AGG_SQL, date=today)
    if not agg or not agg[0]['total']:
        return None
    stats = dict(agg[0])

    peak = q(
        'message',
        f'SELECT substr(timestamp, 12, 2) AS hr, COUNT(*) AS c FROM log WHERE {_RECV} GROUP BY hr ORDER BY c DESC LIMIT 1',
        date=today,
    )
    stats['peak_hour'] = int(peak[0]['hr']) if peak and peak[0].get('hr') else 0
    stats['peak_hour_count'] = peak[0]['c'] if peak else 0

    stats['top_groups'] = q(
        'message',
        f"""
        SELECT group_id, COUNT(*) AS c FROM log
        WHERE group_id != '' AND group_id != 'c2c' AND {_RECV}
        GROUP BY group_id ORDER BY c DESC LIMIT 3
    """,
        date=today,
    )
    stats['top_users'] = q(
        'message',
        f"SELECT user_id, COUNT(*) AS c FROM log WHERE user_id != '' AND {_RECV} GROUP BY user_id ORDER BY c DESC LIMIT 3",
        date=today,
    )

    try:
        counts = lifecycle_counts_from_rows(q('lifecycle', LIFECYCLE_COUNTS_SQL, date=today))
    except Exception:
        lifecycle = q(
            'lifecycle',
            'SELECT type, user_id, group_id FROM log ORDER BY id',
            date=today,
        )
        counts = compute_lifecycle_counts((r.get('type', ''), r.get('user_id', ''), r.get('group_id', '')) for r in lifecycle)
    stats['group_join'] = counts['group_join_count']
    stats['group_leave'] = counts['group_leave_count']
    return stats


def _build_dau_message(event, stats, date, elapsed_ms, y_stats=None, is_today=False):
    """构建日活统计消息。"""
    now = datetime.now()
    time_suffix = f' (截至{now.hour:02d}:{now.minute:02d})' if is_today else ''
    info = [
        f'<@{event.user_id}>',
        f'📊 {date.strftime("%m-%d")} 活跃统计{time_suffix}',
    ]

    y_users = y_stats['users'] if y_stats else None
    y_groups = y_stats['groups_'] if y_stats else None
    y_received = y_stats.get('received') if y_stats else None
    y_sent = y_stats.get('sent') if y_stats else None
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
            '上行消息数',
            stats.get('received', stats.get('received_messages', 0)),
            y_received,
            '💬',
        )
    )
    info.append(
        _fmt_diff(
            '下行消息数',
            stats.get('sent', stats.get('sent_messages', 0)),
            y_sent,
            '📤',
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

    if 'group_join' in stats:
        info.append(f'➕ 今日加群: {stats.get("group_join", 0)}')
        info.append(f'➖ 今日退群: {stats.get("group_leave", 0)}')

    peak_hour = stats.get('peak_hour', 0)
    peak_count = stats.get('peak_hour_count', 0)
    if peak_hour or peak_count:
        info.append(f'⏰ 最活跃时段: {peak_hour}点 ({peak_count}条)')

    # 最活跃群组
    top_groups = stats.get('top_groups', [])
    if top_groups:
        info.append('🔝 最活跃群组:')
        for i, g in enumerate(top_groups[:2], 1):
            gid = g.get('group_id', '')
            cnt = g.get('c', g.get('message_count', 0))
            info.append(f'  {i}. {mask_id(gid)} ({cnt}条)')

    # 最活跃用户
    top_users = stats.get('top_users', [])
    if top_users:
        info.append('👑 最活跃用户:')
        for i, u in enumerate(top_users[:2], 1):
            uid = u.get('user_id', '')
            cnt = u.get('c', u.get('message_count', 0))
            info.append(f'  {i}. {mask_id(uid)} ({cnt}条)')

    info.append(f'🕒 查询耗时: {elapsed_ms}ms')
    return '\n'.join(info)


# ==================== 用户统计 ====================


@handler(r'^用户统计$', name='用户统计', desc='查看当前机器人的用户/群统计', owner_only=True)
async def get_stats(event, match):
    bot = _get_bot(event)
    if not bot:
        return await reply(event, '❌ 无法获取机器人实例')

    started_at = perf_counter()
    ls = bot.log_service

    # 并行查询用户、群组、好友和当前群统计
    users_q = ls.db_fetch_value('SELECT COUNT(*) FROM users', default=0)
    groups_q = ls.db_fetch_value('SELECT COUNT(*) FROM groups_users', default=0)
    members_q = ls.db_fetch_value('SELECT COUNT(*) FROM members', default=0)
    cur_gid = event.group_id if event.is_group else None
    group_stats_q = _query_group_stats(ls, cur_gid)

    user_count, group_count, member_count, (cur, top_gid, top_cnt, rank) = await asyncio.gather(users_q, groups_q, members_q, group_stats_q)

    info = [
        f'<@{event.user_id}>',
        f'📊 [{bot.name}] 统计信息',
    ]

    if cur_gid and cur is not None:
        info.append(f'👥 当前群成员: {cur}')

    info.append(f'👤 好友总数: {member_count}')
    info.append(f'👥 群组总数: {group_count}')
    info.append(f'👥 所有用户数: {user_count}')

    if top_gid is not None:
        info.append(f'🔝 最大群: {mask_id(top_gid)} ({top_cnt}人)')

    if cur_gid and cur is not None and rank is not None:
        info.append(f'📈 当前群排名: 第{rank}名')

    elapsed = round((perf_counter() - started_at) * 1000)
    info.append(f'🕒 查询耗时: {elapsed}ms')
    await reply(event, '\n'.join(info))


# ==================== 日活统计 ====================


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
    started_at = perf_counter()

    stats, y_stats = await asyncio.gather(
        asyncio.to_thread(_query_today_stats_sync, bot),
        asyncio.to_thread(_query_yesterday_same_period_sync, bot),
    )
    if not stats:
        return await reply(event, f'<@{event.user_id}>\n❌ 今日暂无消息数据')

    elapsed = round((perf_counter() - started_at) * 1000)
    await _reply_dau(event, bot, stats, datetime.now(), elapsed, y_stats=y_stats, is_today=True)


def _query_yesterday_same_period_sync(bot):
    """用单次覆盖索引扫描查询昨日同时段统计。"""
    now = datetime.now()
    yesterday = (now - timedelta(days=1)).strftime('%Y-%m-%d')
    bound = f'{yesterday} {now.hour:02d}:{now.minute:02d}:00'

    agg = bot.log_service.query('message', f'{_AGG_SQL} WHERE timestamp <= ?', (bound,), date=yesterday)
    if not agg or not agg[0]['total']:
        return None
    return dict(agg[0])


async def _handle_history_dau(event, bot, date_str):
    """从日活数据库查询历史统计。"""
    started_at = perf_counter()

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

    # 将数据库记录转换为统一统计结构
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
        'received': data.get('received_messages', 0) or 0,
        'sent': data.get('sent_messages', 0) or 0,
        'private': data.get('private_messages', 0),
        'group_join': data.get('group_join_count', 0) or 0,
        'group_leave': data.get('group_leave_count', 0) or 0,
        'peak_hour': detail.get('peak_hour', 0),
        'peak_hour_count': detail.get('peak_hour_count', 0),
        'top_groups': detail.get('top_groups', []),
        'top_users': detail.get('top_users', []),
    }

    elapsed = round((perf_counter() - started_at) * 1000)
    await _reply_dau(event, bot, stats, target, elapsed)
