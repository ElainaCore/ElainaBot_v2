"""WebSocket 管理 — 实时推送日志/系统状态到面板

日志不在内存中缓存, 全部持久化到 SQLite, 前端初始数据通过 API 获取。
WS 仅负责实时广播新日志条目。
"""

import json
import asyncio
import logging
from datetime import datetime

from aiohttp import web, WSMsgType

import web.auth as auth

log = logging.getLogger('ElainaBot.web.ws')

_clients: set = set()


# ==================== 推送 ====================

async def broadcast(msg_type: str, data: dict):
    """向所有连接的面板客户端广播消息"""
    if not _clients:
        return
    payload = json.dumps({'type': msg_type, 'data': data}, ensure_ascii=False, default=str)
    closed = []
    for ws in _clients:
        try:
            await ws.send_str(payload)
        except Exception:
            closed.append(ws)
    for ws in closed:
        _clients.discard(ws)


def push_log(log_type: str, entry: dict):
    """实时推送日志到面板 (不缓存, 仅广播)"""
    if 'timestamp' not in entry:
        entry['timestamp'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    if _clients:
        asyncio.ensure_future(broadcast('new_log', {'log_type': log_type, **entry}))


def push_system_info(data: dict):
    """推送系统信息更新"""
    if _clients:
        asyncio.ensure_future(broadcast('system_info', data))


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
