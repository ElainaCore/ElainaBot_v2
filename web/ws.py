"""WebSocket 管理 — 实时推送日志/系统状态"""

import json
import asyncio
import logging
from datetime import datetime

from aiohttp import web, WSMsgType

import web.auth as auth

log = logging.getLogger('ElainaBot.web.ws')

_clients: set = set()       # WebSocket 客户端
_sse_queues: set = set()    # SSE 客户端队列


# ==================== 推送 ====================

def _has_clients() -> bool:
    return bool(_clients or _sse_queues)


async def broadcast(msg_type: str, data: dict):
    """向所有连接的面板客户端广播消息 (WS + SSE)"""
    if not _has_clients():
        return
    payload = json.dumps({'type': msg_type, 'data': data}, ensure_ascii=False, default=str)
    # WebSocket
    dead = set()
    for ws in list(_clients):
        try:
            await ws.send_str(payload)
        except Exception:
            dead.add(ws)
    _clients.difference_update(dead)
    # SSE
    for q in list(_sse_queues):
        try:
            q.put_nowait(payload)
        except Exception:
            pass


def _schedule_broadcast(msg_type: str, data: dict):
    """安全调度广播任务 (无事件循环时静默忽略)"""
    if not _has_clients():
        return
    try:
        asyncio.get_running_loop().create_task(broadcast(msg_type, data))
    except RuntimeError:
        pass


def push_log(log_type: str, entry: dict):
    """实时推送日志到面板 (不缓存, 仅广播)"""
    if 'timestamp' not in entry:
        entry['timestamp'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    _schedule_broadcast('new_log', {'log_type': log_type, **entry})


def push_system_info(data: dict):
    """推送系统信息更新"""
    _schedule_broadcast('system_info', data)


# ==================== WebSocket 处理器 ====================

async def handle_ws(request: web.Request) -> web.WebSocketResponse:
    """WebSocket 端点: /ws/panel?token=xxx"""
    # 验证 token
    token = request.query.get('token', '')
    if not token or token not in auth.valid_sessions:
        return web.Response(status=401, text='Unauthorized')

    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    _clients.add(ws)
    log.debug(f"面板 WebSocket 已连接 ({len(_clients)} clients)")

    try:
        # 通知前端已连接, 初始数据由前端通过 API 获取
        await ws.send_json({'type': 'init', 'data': {}})

        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    await _handle_client_msg(ws, data)
                except json.JSONDecodeError:
                    pass
            elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                break
    finally:
        _clients.discard(ws)
        log.debug(f"面板 WebSocket 已断开 ({len(_clients)} clients)")

    return ws


async def _handle_client_msg(ws: web.WebSocketResponse, data: dict):
    """处理客户端发来的消息"""
    pass


# ==================== SSE 降级通道 ====================

async def handle_sse(request: web.Request) -> web.StreamResponse:
    """SSE 端点: /api/sse/panel?token=xxx

    当 WebSocket 因 Nginx 未配置 upgrade 等原因不可用时,
    前端自动降级到 SSE, 走普通 HTTP 无需特殊代理配置。
    """
    token = request.query.get('token', '')
    if not token or token not in auth.valid_sessions:
        return web.Response(status=401, text='Unauthorized')

    resp = web.StreamResponse()
    resp.headers['Content-Type'] = 'text/event-stream'
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['X-Accel-Buffering'] = 'no'  # 禁止 Nginx 缓冲
    await resp.prepare(request)

    queue = asyncio.Queue(maxsize=256)
    _sse_queues.add(queue)
    log.debug(f"SSE 客户端已连接 (WS:{len(_clients)} SSE:{len(_sse_queues)})")

    try:
        # 发送初始连接确认
        await resp.write(b"data: {\"type\":\"init\",\"data\":{}}\n\n")
        while True:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=25)
                await resp.write(f"data: {payload}\n\n".encode())
            except asyncio.TimeoutError:
                await resp.write(b": keepalive\n\n")
    except (asyncio.CancelledError, ConnectionResetError, Exception):
        pass
    finally:
        _sse_queues.discard(queue)
        log.debug(f"SSE 客户端已断开 (WS:{len(_clients)} SSE:{len(_sse_queues)})")

    return resp
