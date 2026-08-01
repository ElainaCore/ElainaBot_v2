"""日志查询 — 分页读取 + 登录日志"""

import asyncio

from aiohttp import web

import web.auth as auth
from core.storage.log import SharedLogService


def _query_logs_sync(log_type, page_size, offset):
    """同步查询日志 (executor 中调用)"""
    shared = SharedLogService._instance
    if log_type not in ('framework', 'error') or not shared:
        return [], 0
    rows = shared.query(
        log_type,
        f'SELECT * FROM log ORDER BY id DESC LIMIT {page_size} OFFSET {offset}',
    )
    # 用 MAX(id) 估算总数 — id 是 AUTOINCREMENT, 避免全表 COUNT(*) 在大表上的开销
    total_rows = shared.query(log_type, 'SELECT MAX(id) as cnt FROM log')
    total = total_rows[0].get('cnt') or 0 if total_rows else 0
    return rows, total


async def handle_get_logs(request: web.Request):
    log_type = request.match_info.get('log_type', 'message')
    if log_type not in ('message', 'framework', 'error', 'lifecycle'):
        return web.json_response({'error': '无效的日志类型'}, status=400)

    page = int(request.query.get('page', '1'))
    page_size = int(request.query.get('size', '50'))
    offset = (page - 1) * page_size

    loop = asyncio.get_running_loop()
    rows, total = await loop.run_in_executor(None, _query_logs_sync, log_type, page_size, offset)

    return web.json_response(
        {
            'logs': rows,
            'total': total,
            'page': page,
            'page_size': page_size,
            'total_pages': (total + page_size - 1) // page_size if page_size else 0,
        }
    )


async def handle_get_login_logs(request: web.Request):
    logs = auth.get_login_logs()
    total = len(logs)
    banned = sum(1 for entry in logs if entry.get('is_banned'))
    return web.json_response(
        {
            'success': True,
            'data': logs,
            'stats': {'total': total, 'banned': banned, 'active': total - banned},
        }
    )


async def handle_unban_ip(request: web.Request):
    try:
        body = await request.json()
        ip = body.get('ip', '')
        if not ip:
            return web.json_response({'success': False, 'error': '缺少 IP'}, status=400)
        if auth.unban_ip(ip):
            return web.json_response({'success': True, 'message': f'已解封: {ip}'})
        return web.json_response({'success': False, 'error': 'IP 不存在'}, status=404)
    except Exception as e:
        return web.json_response({'success': False, 'error': str(e)}, status=500)


async def handle_delete_ip(request: web.Request):
    try:
        body = await request.json()
        ip = body.get('ip', '')
        if not ip:
            return web.json_response({'success': False, 'error': '缺少 IP'}, status=400)
        if auth.delete_ip_record(ip):
            return web.json_response({'success': True, 'message': f'已删除: {ip}'})
        return web.json_response({'success': False, 'error': 'IP 不存在'}, status=404)
    except Exception as e:
        return web.json_response({'success': False, 'error': str(e)}, status=500)
