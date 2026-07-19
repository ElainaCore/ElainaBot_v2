"""每小时消息分布 — 快照缓存与聚合"""

import asyncio
import contextlib
import json
import os
from datetime import datetime, timedelta

from web.tools._bots import iter_bots
from web.tools._stats import context

_hourly_cache: dict[str, dict[str, int]] = {}
_hourly_task: asyncio.Task | None = None
_HOURLY_SQL = 'SELECT substr(timestamp,12,2) AS hr, COUNT(*) AS c FROM log GROUP BY hr'


def _hourly_path():
    return os.path.join(context.base_dir, 'data', 'hourly_cache.json')


def _save_hourly_cache():
    p = _hourly_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with contextlib.suppress(Exception), open(p, 'w', encoding='utf-8') as f:
        json.dump(_hourly_cache, f, ensure_ascii=False)


def _query_hourly_from_db(date, where='', appid_filter=''):
    """从 DB 查某天每小时分布, where 可附加过滤条件"""
    sql = _HOURLY_SQL if not where else _HOURLY_SQL.replace('GROUP BY', f'{where} GROUP BY')
    hourly = {}
    for _, inst in iter_bots(context.bot_manager, appid_filter):
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


def init():
    """加载持久化缓存并启动小时快照任务"""
    global _hourly_task
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
            for _, inst in iter_bots(context.bot_manager):
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
