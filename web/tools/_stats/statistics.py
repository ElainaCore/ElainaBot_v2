"""统计数据 — DAU / 消息统计 HTTP 处理器 (聚合逻辑见 gather / hourly 模块)"""

import asyncio
import logging
import time
from collections.abc import Hashable
from datetime import datetime
from typing import Any, TypeVar

from aiohttp import web

from web.tools._stats import context, hourly
from web.tools._stats.gather import (
    _gather_active,
    _gather_chart_sync,
    _gather_core,
    _gather_events,
    _gather_hourly_statistics_sync,
    _gather_stats,
    _gather_summary,
    _gather_top,
    _gather_totals,
)

log = logging.getLogger('ElainaBot.web.stats')

_statistics_tasks: dict[str, dict[str, Any]] = {}
_task_results: dict[str, dict[str, Any]] = {}

# 简单内存缓存: {(date, appid_filter): (timestamp, data)} — 避免短时间内重复全表扫描
_stats_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_chart_cache: dict[tuple[int, str], tuple[float, dict[str, Any]]] = {}
_core_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_CACHE_TTL = 10  # 秒

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


def set_context(bot_manager, base_dir=''):
    context.set_context(bot_manager, base_dir)
    hourly.init()


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
