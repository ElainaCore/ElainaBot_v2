#!/usr/bin/env python
"""WebSocket 客户端 — 异步, 每个机器人独立 WS 连接接收事件"""

import asyncio
import contextlib
import json
import platform as _platform
import ssl as _ssl_mod

import websockets

from core.base.logger import SERVICE, get_logger
from core.message.event import Event

log = get_logger(SERVICE, 'WebSocket')

_GATEWAY_URL = 'https://api.sgroup.qq.com/gateway/bot'

_OP_DISPATCH = 0
_OP_HEARTBEAT = 1
_OP_IDENTIFY = 2
_OP_RESUME = 6
_OP_RECONNECT = 7
_OP_INVALID_SESSION = 9
_OP_HELLO = 10
_OP_HEARTBEAT_ACK = 11
_OP_EVENT_ACK = 12

# 事件并发上限, 避免高频事件下 create_task 堆积致 OOM
_MAX_INFLIGHT_EVENTS = 256

# WS 握手 UA 末段 {名称}/{版本} 即 QQ 前端展示的客户端 (缺省 UA 显示「未知 ws 连接」)
_UA_PLUGIN_TAG = 'QQBotPlugin/1.7.2'
_CLIENT_VERSION = '9.9.9'
_UA_RUNTIME = f'Python/{_platform.python_version()}; {_platform.system().lower()}'

# intents 位掩码 (订阅的事件类型)
_INTENTS = (1 << 0) | (1 << 10) | (1 << 12) | (1 << 24) | (1 << 25) | (1 << 26) | (1 << 27) | (1 << 30)


def build_user_agent(client_name=''):
    """构建 WS 握手 User-Agent, QQ 前端据末段 {名称}/{版本} 显示连接客户端。"""
    name = str(client_name).strip() or 'ElainaBot'
    return f'{_UA_PLUGIN_TAG} ({_UA_RUNTIME}; {name}/{_CLIENT_VERSION})'


class WSClient:
    """单个机器人的 WebSocket 客户端"""

    def __init__(
        self,
        appid,
        token_manager,
        on_event,
        *,
        reconnect_interval=5,
        max_reconnects=-1,
        custom_url='',
        custom_api_base='',
        client_name='ElainaBot',
    ):
        self._appid = str(appid)
        self._tm = token_manager
        self._on_event = on_event
        self._reconnect_interval = reconnect_interval
        self._max_reconnects = max_reconnects
        self._custom_url = custom_url.strip() if custom_url else ''
        self._custom_api_base = custom_api_base.strip().rstrip('/') if custom_api_base else ''
        self._client_name = str(client_name).strip() or 'ElainaBot'

        self._ws = None
        self._session_id = None
        self._seq = None
        self._heartbeat_interval = 45
        self._heartbeat_task = None
        self._receive_task = None
        self._closed = False
        self._reconnect_count = 0
        self._gateway_url = None
        # 背压: 限制并发处理事件数, 避免下游慢导致任务堆积
        self._event_sem = asyncio.Semaphore(_MAX_INFLIGHT_EVENTS)

    async def connect(self):
        """连接并开始接收事件"""
        self._closed = False
        while not self._closed:
            try:
                url = await self._get_gateway_url()
                log.info(f'[{self._appid}] 正在连接 WebSocket: {url}')
                _ssl = _ssl_mod.create_default_context() if url.startswith('wss://') else None
                ua = build_user_agent(self._client_name)
                kw = {'ssl': _ssl, 'additional_headers': {'User-Agent': ua}}
                async with websockets.connect(url, **kw) as ws:
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
                    log.error(f'[{self._appid}] 达到最大重连次数 {self._max_reconnects}')
                    break
                log.warning(f'[{self._appid}] 连接断开: {e}, {self._reconnect_interval}s 后重连 ({self._reconnect_count})')
                await asyncio.sleep(self._reconnect_interval)

    async def close(self):
        self._closed = True
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        if self._ws:
            await self._ws.close()

    async def _handle_connection(self):
        """处理 WS 消息循环"""
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
                log.info(f'[{self._appid}] 收到重连请求')
                break
            elif op == _OP_INVALID_SESSION:
                resumable = payload.get('d', False) and self._session_id
                if not resumable:
                    log.warning(f'[{self._appid}] 会话无效, 重新鉴权')
                    self._session_id = None
                    self._seq = None
                else:
                    log.warning(f'[{self._appid}] 会话无效但可恢复')
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
            log.info(f'[{self._appid}] WebSocket 已就绪 (session={self._session_id})')
            return
        if event_type == 'RESUMED':
            log.info(f'[{self._appid}] 会话已恢复')
            return
        try:
            event = Event.from_websocket(self._appid, payload)
            log.debug(f'[{self._appid}] WS事件: {event}')
            if event_type == 'INTERACTION_CREATE':
                # 分发插件后用其 code 发 op12 ACK (独立任务等待, 不阻塞读取循环)
                event.start_ack_countdown()
                task = asyncio.create_task(self._dispatch_with_backpressure(event))
                task.add_done_callback(lambda t, ev=event: ev.finish_dispatch())
                asyncio.create_task(self._ack_interaction_when_ready(event, payload))
            else:
                asyncio.create_task(self._dispatch_with_backpressure(event))
        except Exception as e:
            log.error(f'[{self._appid}] 事件处理异常: {e}')

    async def _ack_interaction_when_ready(self, event, payload):
        """等待插件设置 code (或分发结束/超时) 后发送 op12 ACK。"""
        try:
            code = await event.wait_ack_code()
        except Exception:
            code = 0
        await self._send_event_ack(payload, code)

    async def _dispatch_with_backpressure(self, event):
        """背压包装: Semaphore 控制并发, 满载时新事件等待空闲槽位"""
        async with self._event_sem:
            try:
                await self._on_event(event)
            except Exception as e:
                log.error(f'[{self._appid}] 事件回调异常: {e}')

    async def _send_event_ack(self, payload, code=0):
        """回复事件确认 (op 12)"""
        with contextlib.suppress(Exception):
            await self._ws.send(json.dumps({'op': _OP_EVENT_ACK, 'code': code}))

    async def _send_op(self, op, data, label=''):
        """发送 WS 操作帧"""
        await self._ws.send(json.dumps({'op': op, 'd': data}))
        if label:
            log.info(f'[{self._appid}] {label}')

    async def _send_identify(self):
        token = await self._tm.get_token()
        payload = {
            'token': f'QQBot {token}',
            'intents': _INTENTS,
            'shard': [0, 1],
        }
        await self._send_op(_OP_IDENTIFY, payload, '已发送鉴权')

    async def _send_resume(self):
        token = await self._tm.get_token()
        await self._send_op(
            _OP_RESUME,
            {
                'token': f'QQBot {token}',
                'session_id': self._session_id,
                'seq': self._seq,
            },
            f'已发送恢复 (seq={self._seq})',
        )

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

        # 优先使用自定义 WS 地址
        if self._custom_url:
            self._gateway_url = self._custom_url
            log.info(f'[{self._appid}] 使用自定义 WS 地址: {self._custom_url}')
            return self._gateway_url

        # 自定义 API 基址时, 从该基址获取网关
        url = f'{self._custom_api_base}/gateway/bot' if self._custom_api_base else _GATEWAY_URL
        token = await self._tm.get_token()
        client = await self._tm.get_client()
        resp = await client.get(url, headers={'Authorization': f'QQBot {token}'})
        data = resp.json()
        self._gateway_url = data.get('url', '')
        if not self._gateway_url:
            raise RuntimeError(f'获取网关失败: {data}')
        return self._gateway_url
