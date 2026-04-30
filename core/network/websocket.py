#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""WebSocket 客户端 — 异步, 每个机器人独立 WS 连接接收事件"""

import json
import asyncio
import ssl
import aiohttp
import websockets
from core.base.logger import get_logger, SERVICE
from core.message.event import Event

log = get_logger(SERVICE, "WebSocket")

_GATEWAY_URL = "https://api.sgroup.qq.com/gateway/bot"

_OP_DISPATCH = 0
_OP_HEARTBEAT = 1
_OP_IDENTIFY = 2
_OP_RESUME = 6
_OP_RECONNECT = 7
_OP_INVALID_SESSION = 9
_OP_HELLO = 10
_OP_HEARTBEAT_ACK = 11


class WSClient:
    """单个机器人的 WebSocket 客户端"""

    def __init__(self, appid, token_manager, on_event, *,
                 reconnect_interval=5, max_reconnects=-1):
        self._appid = str(appid)
        self._tm = token_manager
        self._on_event = on_event
        self._reconnect_interval = reconnect_interval
        self._max_reconnects = max_reconnects

        self._ws = None
        self._session_id = None
        self._seq = None
        self._heartbeat_interval = 45
        self._heartbeat_task = None
        self._receive_task = None
        self._closed = False
        self._reconnect_count = 0
        self._gateway_url = None

        # 复用 SSL 上下文
        self._ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self._ssl_ctx.check_hostname = False
        self._ssl_ctx.verify_mode = ssl.CERT_NONE

    async def connect(self):
        """连接并开始接收事件"""
        self._closed = False
        while not self._closed:
            try:
                url = await self._get_gateway_url()
                log.info(f"[{self._appid}] 正在连接 WebSocket: {url}")
                async with websockets.connect(url, ssl=self._ssl_ctx) as ws:
                    self._ws = ws
                    self._reconnect_count = 0
                    await self._handle_connection()
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._closed:
                    break
                self._reconnect_count += 1
                if 0 < self._max_reconnects <= self._reconnect_count:
                    log.error(f"[{self._appid}] 达到最大重连次数 {self._max_reconnects}")
                    break
                log.warning(f"[{self._appid}] 连接断开: {e}, {self._reconnect_interval}s 后重连 "
                            f"({self._reconnect_count})")
                await asyncio.sleep(self._reconnect_interval)

    async def close(self):
        self._closed = True
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        if self._ws:
            await self._ws.close()

    async def _handle_connection(self):
        """ 处理 WS 消息循环"""
        async for message in self._ws:
            try:
                payload = json.loads(message)
            except json.JSONDecodeError:
                continue
            op = payload.get('op')
            if payload.get('s') is not None:
                self._seq = payload['s']
            if op == _OP_HELLO:
                await self._on_hello(payload)
            elif op == _OP_DISPATCH:
                await self._on_dispatch(payload)
            elif op == _OP_HEARTBEAT_ACK:
                pass
            elif op == _OP_RECONNECT:
                log.info(f"[{self._appid}] 收到重连请求")
                break
            elif op == _OP_INVALID_SESSION:
                resumable = payload.get('d', False) and self._session_id
                if not resumable:
                    log.warning(f"[{self._appid}] 会话无效, 重新鉴权")
                    self._session_id = None
                    self._seq = None
                else:
                    log.warning(f"[{self._appid}] 会话无效但可恢复")
                self._gateway_url = None
                await asyncio.sleep(3)
                break

    async def _on_hello(self, payload):
        """Hello → 启动心跳 + 鉴权/恢复"""
        self._heartbeat_interval = payload.get('d', {}).get('heartbeat_interval', 45000) / 1000
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        if self._session_id and self._seq is not None:
            await self._send_resume()
        else:
            await self._send_identify()

    async def _on_dispatch(self, payload):
        """事件分发 → 构造 Event 并回调"""
        event_type = payload.get('t', '')
        if event_type == 'READY':
            self._session_id = payload.get('d', {}).get('session_id')
            log.info(f"[{self._appid}] WebSocket 已就绪 (session={self._session_id})")
            return
        if event_type == 'RESUMED':
            log.info(f"[{self._appid}] 会话已恢复")
            return
        try:
            event = Event.from_websocket(self._appid, payload)
            log.debug(f"[{self._appid}] WS事件: {event}")
            asyncio.create_task(self._on_event(event))
        except Exception as e:
            log.error(f"[{self._appid}] 事件处理异常: {e}")

    async def _send_op(self, op, data, label=''):
        """发送 WS 操作帧"""
        await self._ws.send(json.dumps({'op': op, 'd': data}))
        if label:
            log.info(f"[{self._appid}] {label}")

    async def _send_identify(self):
        token = await self._tm.get_token()
        await self._send_op(_OP_IDENTIFY, {
            'token': f"QQBot {token}",
            'intents': self._get_intents(),
            'shard': [0, 1],
            'properties': {'$os': 'python', '$browser': 'elaina-bot', '$device': 'elaina-bot'},
        }, '已发送鉴权')

    async def _send_resume(self):
        token = await self._tm.get_token()
        await self._send_op(_OP_RESUME, {
            'token': f"QQBot {token}",
            'session_id': self._session_id,
            'seq': self._seq,
        }, f'已发送恢复 (seq={self._seq})')

    async def _heartbeat_loop(self):
        while not self._closed:
            try:
                await asyncio.sleep(self._heartbeat_interval)
                if self._ws and not self._closed:
                    await self._ws.send(json.dumps({'op': _OP_HEARTBEAT, 'd': self._seq}))
            except asyncio.CancelledError:
                break
            except Exception:
                break

    async def _get_gateway_url(self):
        """获取 WS 网关地址"""
        if self._gateway_url:
            return self._gateway_url

        url = _GATEWAY_URL
        token = await self._tm.get_token()
        client = await self._tm._ensure_client()
        async with client.get(url, headers={'Authorization': f"QQBot {token}"}) as resp:
            data = await resp.json(content_type=None)
            self._gateway_url = data.get('url', '')
            if not self._gateway_url:
                raise RuntimeError(f"获取网关失败: {data}")
            return self._gateway_url

    @staticmethod
    def _get_intents():
        """GUILDS | GUILD_MESSAGE_REACTIONS | DIRECT_MESSAGE | GROUP_AND_C2C | INTERACTION | AUDIT"""
        return (1 << 0) | (1 << 10) | (1 << 12) | (1 << 25) | (1 << 26) | (1 << 27)
