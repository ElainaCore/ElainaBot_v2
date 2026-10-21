"""统计 HTTP 接口：短缓存、相同请求合并，数据库读取在工作线程执行。"""

import asyncio
import time
from datetime import datetime
from typing import Any

from aiohttp import web

from web.tools._stats import context
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

_cache: dict[tuple[object, ...], tuple[float, dict[str, Any]]] = {}
_inflight: dict[tuple[object, ...], asyncio.Task[dict[str, Any]]] = {}
_CACHE_TTL = 10


def set_context(bot_manager, base_dir=''):
    context.set_context(bot_manager, base_dir)
    _cache.clear()


async def _query_cached(gatherer, *args, force=False):
    # 日期进入 key，跨零点不会复用前一天的今日统计。
    key = (id(context.bot_manager), gatherer, datetime.now().strftime('%Y-%m-%d'), *args)
    entry = _cache.get(key)
    if not force and entry and time.monotonic() < entry[0]:
        return entry[1]
    task = _inflight.get(key)
    if task is None:
        task = asyncio.create_task(asyncio.to_thread(gatherer, *args))
        _inflight[key] = task

        def finished(done):
            _inflight.pop(key, None)
            if done.cancelled() or done.exception() is not None:
                return
            result = done.result()
            data = result.get('data', result)
            ttl = 1 if data.get('pending') else _CACHE_TTL
            now = time.monotonic()
            for expired in [k for k, (until, _) in _cache.items() if until <= now]:
                _cache.pop(expired, None)
            _cache[key] = (now + ttl, result)

        task.add_done_callback(finished)
    # 浏览器取消请求不撤销工作，也不让下一个请求重复启动同一次读取。
    return await asyncio.shield(task)


async def _handle_stat_query(request, gatherer, include_date=True):
    appid = request.query.get('appid', '')
    args = (request.query.get('date', '') or datetime.now().strftime('%Y-%m-%d'), appid) if include_date else (appid,)
    try:
        data = await _query_cached(gatherer, *args)
        return web.json_response({'success': True, 'data': data})
    except Exception as exc:
        return web.json_response({'success': False, 'error': str(exc)}, status=500)


async def handle_get_statistics(request: web.Request):
    try:
        data = await _query_cached(
            _gather_stats, request.query.get('date', ''), request.query.get('appid', ''),
            force=request.query.get('force_refresh', 'false') == 'true',
        )
        return web.json_response({'success': True, 'data': data})
    except Exception as exc:
        return web.json_response({'success': False, 'error': str(exc)}, status=500)


async def handle_get_available_dates(request: web.Request):
    return web.json_response({'success': True, 'dates': [{
        'value': 'today', 'date': datetime.now().strftime('%Y-%m-%d'), 'display': '今日数据', 'is_today': True,
    }]})


async def handle_get_chart_data(request: web.Request):
    try:
        days = max(1, min(30, int(request.query.get('days', '7'))))
    except ValueError:
        return web.json_response({'success': False, 'error': 'days 必须为整数'}, status=400)
    payload = await _query_cached(_gather_chart_sync, days, request.query.get('appid', ''))
    return web.json_response(payload)


async def handle_get_hourly_statistics(request: web.Request):
    payload = await _query_cached(_gather_hourly_statistics_sync, request.query.get('appid', ''))
    return web.json_response(payload)


async def handle_get_core(request: web.Request):
    return await _handle_stat_query(request, _gather_core)


async def handle_get_summary(request: web.Request):
    return await _handle_stat_query(request, _gather_summary)


async def handle_get_active(request: web.Request):
    return await _handle_stat_query(request, _gather_active)


async def handle_get_top(request: web.Request):
    return await _handle_stat_query(request, _gather_top)


async def handle_get_events(request: web.Request):
    return await _handle_stat_query(request, _gather_events)


async def handle_get_totals(request: web.Request):
    return await _handle_stat_query(request, _gather_totals, include_date=False)
