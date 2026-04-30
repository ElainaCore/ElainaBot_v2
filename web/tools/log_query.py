"""日志查询 (aiohttp 版) — 从 SQLite 分页读取 + 登录日志"""

from aiohttp import web
import web.auth as auth


async def handle_get_logs(request: web.Request):
    from core.storage.log import SharedLogService
    log_type = request.match_info.get('log_type', 'message')
    if log_type not in ('message', 'framework', 'error', 'lifecycle'):
        return web.json_response({'error': '无效的日志类型'}, status=400)

    page = int(request.query.get('page', '1'))
    page_size = int(request.query.get('size', '50'))
    offset = (page - 1) * page_size

    shared = SharedLogService._instance
    if log_type in ('framework', 'error') and shared:
        rows = shared.query(log_type, f"SELECT * FROM log ORDER BY id DESC LIMIT {page_size} OFFSET {offset}")
        total_rows = shared.query(log_type, "SELECT COUNT(*) as cnt FROM log")
        total = total_rows[0]['cnt'] if total_rows else 0
    else:
        rows = []
        total = 0

    return web.json_response({
        'logs': rows, 'total': total,
        'page': page, 'page_size': page_size,
        'total_pages': (total + page_size - 1) // page_size if page_size else 0,
    })


async def handle_get_login_logs(request: web.Request):
    logs = auth.get_login_logs()
    total = len(logs)
    banned = sum(1 for l in logs if l.get('is_banned'))
    return web.json_response({
        'success': True, 'data': logs,
        'stats': {'total': total, 'banned': banned, 'active': total - banned},
    })


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
