"""WebSocket 管理 — 实时推送日志/系统状态"""

import asyncio
import contextlib
import json
import logging
from datetime import datetime

from aiohttp import WSMsgType, web

import web.auth as auth

log = logging.getLogger('ElainaBot.web.ws')


# ==================== WSBroadcast ====================


class WSBroadcast:
    """WebSocket/SSE 广播管理 (封装模块级全局状态)"""

    def __init__(self):
        self._clients: set = set()
        self._sse_queues: set = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def clients(self):
        return self._clients

    @property
    def sse_queues(self):
        return self._sse_queues

    def has_clients(self) -> bool:
        return bool(self._clients or self._sse_queues)

    async def broadcast(self, msg_type: str, data: dict):
        """向所有连接的面板客户端广播消息 (WS + SSE)"""
        if not self.has_clients():
            return
        payload = json.dumps({'type': msg_type, 'data': data}, ensure_ascii=False, default=str)
        # WebSocket
        dead = set()
        for ws in list(self._clients):
            try:
                await ws.send_str(payload)
            except Exception:
                dead.add(ws)
        self._clients.difference_update(dead)
        # SSE
        for q in list(self._sse_queues):
            with contextlib.suppress(Exception):
                q.put_nowait(payload)

    def schedule_broadcast(self, msg_type: str, data: dict):
        """安全调度广播任务 (非事件循环线程转发到主 loop, 无可用 loop 时静默忽略)"""
        if not self.has_clients():
            return
        try:
            asyncio.get_running_loop().create_task(self.broadcast(msg_type, data))
            return
        except RuntimeError:
            pass
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        with contextlib.suppress(RuntimeError):
            asyncio.run_coroutine_threadsafe(self.broadcast(msg_type, data), loop)

    def push_log(self, log_type: str, entry: dict):
        """实时推送日志到面板 (不缓存, 仅广播)"""
        if 'timestamp' not in entry:
            entry['timestamp'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.schedule_broadcast('new_log', {'log_type': log_type, **entry})

    def push_system_info(self, data: dict):
        """推送系统信息更新"""
        self.schedule_broadcast('system_info', data)

    def clear(self):
        """清理所有连接 (用于测试隔离)"""
        self._clients.clear()
        self._sse_queues.clear()

    def shutdown(self):
        """服务器关闭时主动断开所有面板连接, 避免阻塞 runner.shutdown()"""
        for ws in list(self._clients):
            with contextlib.suppress(Exception, RuntimeError):
                asyncio.get_running_loop().create_task(
                    ws.close(code=1001, message=b'Server shutdown')
                )
        self._clients.clear()
        self._sse_queues.clear()


# 模块级单例
_broadcast = WSBroadcast()


def get_broadcast() -> WSBroadcast:
    """获取广播管理器单例"""
    return _broadcast


def reset_broadcast():
    """重置广播管理器 (用于测试)"""
    global _broadcast
    _broadcast.clear()
    _broadcast = WSBroadcast()
    return _broadcast


# ==================== 模块级兼容函数 ====================


async def broadcast(msg_type: str, data: dict):
    """向所有连接的面板客户端广播消息 (WS + SSE)"""
    await _broadcast.broadcast(msg_type, data)


def push_log(log_type: str, entry: dict):
    """实时推送日志到面板"""
    _broadcast.push_log(log_type, entry)


def push_system_info(data: dict):
    """推送系统信息更新"""
    _broadcast.push_system_info(data)


# ==================== WebSocket 处理器 ====================


async def handle_ws(request: web.Request) -> web.WebSocketResponse:
    """WebSocket 端点: /ws/panel?token=xxx"""
    # 验证 token
    token = request.query.get('token', '')
    if not token or token not in auth.valid_sessions:
        return web.Response(status=401, text='Unauthorized')  # type: ignore[return-value]  # auth failure before WS upgrade

    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    _broadcast._loop = asyncio.get_running_loop()
    _broadcast.clients.add(ws)
    log.debug(f'面板 WebSocket 已连接 ({len(_broadcast.clients)} clients)')

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
        _broadcast.clients.discard(ws)
        log.debug(f'面板 WebSocket 已断开 ({len(_broadcast.clients)} clients)')

    return ws


async def _handle_client_msg(ws: web.WebSocketResponse, data: dict):
    """处理客户端发来的消息"""
    pass


# ==================== SSE 降级通道 ====================


async def handle_sse(request: web.Request) -> web.StreamResponse:
    """SSE 端点: /api/sse/panel?token=xxx (WebSocket 不可用时的降级通道)"""
    token = request.query.get('token', '')
    if not token or token not in auth.valid_sessions:
        return web.Response(status=401, text='Unauthorized')

    resp = web.StreamResponse()
    resp.headers['Content-Type'] = 'text/event-stream'
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['X-Accel-Buffering'] = 'no'  # 禁止 Nginx 缓冲
    await resp.prepare(request)

    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=256)
    _broadcast._loop = asyncio.get_running_loop()
    _broadcast.sse_queues.add(queue)
    log.debug(f'SSE 客户端已连接 (WS:{len(_broadcast.clients)} SSE:{len(_broadcast.sse_queues)})')

    try:
        # 发送初始连接确认
        await resp.write(b'data: {"type":"init","data":{}}\n\n')
        while True:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=25)
                await resp.write(f'data: {payload}\n\n'.encode())
            except TimeoutError:
                await resp.write(b': keepalive\n\n')
    except (asyncio.CancelledError, ConnectionResetError, Exception):
        pass
    finally:
        _broadcast.sse_queues.discard(queue)
        log.debug(f'SSE 客户端已断开 (WS:{len(_broadcast.clients)} SSE:{len(_broadcast.sse_queues)})')

    return resp
