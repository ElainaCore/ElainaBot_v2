#!/usr/bin/env python
"""WebSocket 客户端 — 异步, 每个机器人独立 WS 连接接收事件"""

import asyncio
import json
import platform as _platform
import ssl as _ssl_mod
from collections import deque
from urllib.parse import urlsplit
from uuid import uuid4

import websockets

from core.base.logger import SERVICE, get_logger
from core.base.tasks import spawn
from core.message.event import Event
from core.module.connection_status import ConnectionCapabilities, ConnectionStatusManager

log = get_logger(SERVICE, 'WebSocket')

_OP_DISPATCH = 0
_OP_HEARTBEAT = 1
_OP_IDENTIFY = 2
_OP_RESUME = 6
_OP_RECONNECT = 7
_OP_INVALID_SESSION = 9
_OP_HELLO = 10
_OP_HEARTBEAT_ACK = 11

# 事件并发上限, 避免高频事件下 create_task 堆积致 OOM
_MAX_INFLIGHT_EVENTS = 256

# WS 握手 UA 末段 {名称}/{版本} 即 QQ 前端展示的客户端 (缺省 UA 显示「未知 ws 连接」)
_UA_PLUGIN_TAG = 'QQBotPlugin/1.7.2'
_CLIENT_VERSION = '9.9.9'
_UA_RUNTIME = f'Python/{_platform.python_version()}; {_platform.system().lower()}'

# intents 位掩码 (订阅的事件类型)
# 频道相关 intents 默认关闭，通过 bot.yaml websocket.subscribe_channel_events 开启。
# 公域机器人可订阅的频道事件: GUILD_MEMBERS(1)、GUILD_MESSAGE_REACTIONS(10)、
#       AUDIO_ACTION(29)、PUBLIC_GUILD_MESSAGES(30)。
# GUILD_MESSAGES(9) 与 FORUMS_EVENT(28) 仅私域机器人可申请，不能随开关发送，
# 否则网关会在 IDENTIFY 后返回 INVALID_SESSION。
# 其余当前使用的 intents 按群聊/单聊及交互事件处理。
_BASE_INTENTS = (1 << 0) | (1 << 12) | (1 << 24) | (1 << 25) | (1 << 26) | (1 << 27)
_CHANNEL_INTENTS = (1 << 1) | (1 << 10) | (1 << 29) | (1 << 30)


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
        ack_interaction,
        on_event_received=None,
        connection_status=None,
        bot_generation=None,
        reconnect_interval=5,
        max_reconnects=-1,
        custom_url='',
        client_name='ElainaBot',
        subscribe_channel_events=False,
    ):
        self._appid = str(appid)
        self.connection_status = connection_status if connection_status is not None else ConnectionStatusManager()
        self._status = self.connection_status._register(self._appid, 'websocket', bot_generation=bot_generation)
        self._login_kind = None
        self._auth_mode = None
        self._ready_seen = False
        self._resumed_seen = False
        self._receive_index = 0
        self._resume_pending = False
        self._resume_from_sequence = None
        self._resume_provable = False
        self._resume_max_sequence = None
        self._connect_task = None
        self._stop_generation = 0
        self._tm = token_manager
        self._on_event = on_event
        self._on_event_received = on_event_received
        self._ack_interaction = ack_interaction
        self._reconnect_interval = reconnect_interval
        self._max_reconnects = max_reconnects
        self._custom_url = custom_url.strip() if custom_url else ''
        self._client_name = str(client_name).strip() or 'ElainaBot'
        self._subscribe_channel_events = bool(subscribe_channel_events)
        self._intents = _BASE_INTENTS | (_CHANNEL_INTENTS if self._subscribe_channel_events else 0)

        self._ws = None
        self._session_id = None
        self._seq = None
        self._heartbeat_interval = 45
        self._heartbeat_task = None
        self._receive_task = None
        self._closed = False
        self._reconnect_count = 0
        self._gateway_url = None
        # 固定 worker 消化事件，避免每条事件创建一个等待信号量的 Task。
        self._event_queue = asyncio.Queue()
        self._event_workers = []
        self._deferred_events = {}
        self._deferred_tasks = set()
        self._interaction_tasks = {}
        # 交互事件仍单独走信号量路径，保证 ACK 倒计时不被普通消息队列阻塞。
        self._event_sem = asyncio.Semaphore(_MAX_INFLIGHT_EVENTS)

    def connect(self):
        """返回连接协程；close 使此前创建但尚未执行的连接请求失效。"""
        return self._connect(self._stop_generation)

    async def _connect(self, stop_generation):
        if stop_generation != self._stop_generation:
            return
        if self._connect_task is not None:
            raise RuntimeError('WebSocket client is already connecting')
        if not self._status.reactivate():
            return
        if self._session_id is None:
            self._status.update_context(session_id=None, session_generation=None, resume_attempt_id=None)
        self._connect_task = asyncio.current_task()
        self._closed = False
        self._ensure_event_workers()
        try:
            while not self._closed:
                opened, failed = False, False
                reason = 'socket_eof'
                self._status.update_context(connection_generation=uuid4().hex, resume_attempt_id=None)
                self._status.publish('connecting', reason='connect_attempt')
                try:
                    url = await self._get_gateway_url()
                    parsed = urlsplit(url)
                    official = (
                        not self._custom_url
                        and self._tm.api_base in {'https://api.bot.qq.com', 'https://api.sgroup.qq.com'}
                        and parsed.scheme == 'wss'
                        and parsed.hostname in {'api.bot.qq.com', 'api.sgroup.qq.com'}
                        and parsed.port in (None, 443)
                        and parsed.username is None
                    )
                    self._status.capabilities = ConnectionCapabilities(True, official, official, 'qq_official_ws' if official else 'unverified_gateway')
                    log.info(f'[{self._appid}] 正在连接 WebSocket: {url}')
                    _ssl = _ssl_mod.create_default_context() if url.startswith('wss://') else None
                    ua = build_user_agent(self._client_name)
                    kw = {'ssl': _ssl, 'additional_headers': {'User-Agent': ua}}
                    async with websockets.connect(url, **kw) as ws:
                        self._ws = ws
                        opened = True
                        self._auth_mode = None
                        self._ready_seen = False
                        self._resumed_seen = False
                        self._status.publish('connected', reason='socket_open')
                        self._reconnect_count = 0
                        reason = await self._handle_connection()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    if self._closed:
                        break
                    failed = True
                    reason = 'connection_error'
                    self._reconnect_count += 1
                    log.warning(f'[{self._appid}] 连接断开: {e}, {self._reconnect_interval}s 后重连 ({self._reconnect_count})')
                finally:
                    self._ws = None
                    if self._heartbeat_task and not self._heartbeat_task.done():
                        self._heartbeat_task.cancel()
                if self._closed:
                    break
                if self._resume_pending:
                    self._status.publish('resume_failed', reason=reason)
                    self._resume_pending = False
                self._status.publish('disconnected' if opened else 'unknown', reason=reason)
                if failed and 0 < self._max_reconnects <= self._reconnect_count:
                    log.error(f'[{self._appid}] 达到最大重连次数 {self._max_reconnects}')
                    self._status.publish('stopped', reason='reconnect_exhausted')
                    break
                self._status.publish('reconnecting', reason=reason)
                if failed:
                    await asyncio.sleep(self._reconnect_interval)
        except asyncio.CancelledError:
            if not self._closed:
                await self.close(reason='connect_cancelled')
            raise
        finally:
            self._connect_task = None

    async def close(self, *, reason='local_stop'):
        self._stop_generation += 1
        self._closed = True
        self._status.publish('stopped', reason=reason)
        connecting = self._connect_task
        if connecting is not None and connecting is not asyncio.current_task():
            connecting.cancel()
        self._session_id = None
        self._seq = None
        self._resume_pending = False
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        workers, self._event_workers = self._event_workers, []
        for worker in workers:
            worker.cancel()
        deferred = tuple(self._deferred_tasks)
        for task in deferred:
            task.cancel()
        # 首次等待前取消全部任务，避免普通回调清理期间放行等待中的按钮。
        interactions = tuple(self._interaction_tasks)
        for task in interactions:
            self._interaction_tasks[task].finish_dispatch()
            task.cancel()
        if workers or deferred or interactions:
            await asyncio.gather(*workers, *deferred, *interactions, return_exceptions=True)
        # 恢复任务可能在首次运行前就被取消，需兜底清理未执行 finally 的暂存。
        for waiting in self._deferred_events.values():
            while waiting:
                waiting.popleft()
                self._event_queue.task_done()
        self._deferred_events.clear()
        while not self._event_queue.empty():
            self._event_queue.get_nowait()
            self._event_queue.task_done()
        if self._ws:
            await self._ws.close()
        if connecting is not None and connecting is not asyncio.current_task():
            await asyncio.gather(connecting, return_exceptions=True)

    def _ensure_event_workers(self):
        if any(not worker.done() for worker in self._event_workers):
            return
        self._event_workers = [asyncio.create_task(self._event_worker()) for _ in range(_MAX_INFLIGHT_EVENTS)]

    async def _event_worker(self):
        while True:
            event = await self._event_queue.get()
            if self._defer_event(event):
                continue
            try:
                await self._on_event(event)
            except Exception as e:
                log.error(f'[{self._appid}] 事件回调异常: {e}')
            finally:
                self._event_queue.task_done()

    def _defer_event(self, event):
        # 等待群屏障的事件不能占满固定 worker。
        barriers = tuple(task for task in event._group_lifecycle_barriers if not task.done())
        if not barriers:
            return False
        scope = event._group_lifecycle_scope
        waiting = self._deferred_events.get(scope)
        if waiting is None:
            waiting = self._deferred_events[scope] = deque()
            task = spawn(self._resume_deferred(scope, waiting))
            self._deferred_tasks.add(task)
            task.add_done_callback(self._deferred_tasks.discard)
        waiting.append(event)
        return True

    async def _resume_deferred(self, scope, waiting):
        try:
            while waiting:
                event = waiting[0]
                for barrier in event._group_lifecycle_barriers:
                    await asyncio.shield(barrier)
                waiting.popleft()
                self._event_queue.put_nowait(event)
                self._event_queue.task_done()
        finally:
            # 配平暂存前的队列计数；连接关闭取消时同样清理。
            for _ in waiting:
                self._event_queue.task_done()
            self._deferred_events.pop(scope, None)

    async def _handle_connection(self):
        """处理 WS 消息循环"""
        ws = self._ws
        async for message in ws:
            if self._closed or self._ws is not ws:
                return 'stale_connection'
            try:
                payload = json.loads(message)
            except json.JSONDecodeError:
                self._resume_provable = False
                self._status.publish('unknown', reason='invalid_payload')
                continue
            if not isinstance(payload, dict):
                self._resume_provable = False
                self._status.publish('unknown', reason='invalid_payload')
                continue
            self._receive_index += 1
            sequence = payload.get('s')
            self._status.update_context(receive_index=self._receive_index, sequence=sequence if type(sequence) is int else None)
            if payload.get('s') is not None:
                self._seq = payload['s']
            op = payload.get('op')
            if type(op) is not int:
                self._status.publish('unknown', reason='invalid_opcode')
                continue
            if self._resume_pending and op == _OP_DISPATCH:
                if type(sequence) is not int or sequence < self._resume_max_sequence:
                    self._resume_provable = False
                else:
                    self._resume_max_sequence = sequence
            if op == _OP_HELLO:
                await self._on_hello(payload)
            elif op == _OP_DISPATCH:
                await self._on_dispatch(payload)
            elif op == _OP_HEARTBEAT_ACK:
                pass
            elif op == _OP_RECONNECT:
                log.info(f'[{self._appid}] 收到重连请求')
                return 'server_reconnect'
            elif op == _OP_INVALID_SESSION:
                self._status.publish('resume_failed' if self._auth_mode == 'resume' else 'unknown', reason='invalid_session')
                self._resume_pending = False
                resumable = payload.get('d', False) and self._session_id
                if not resumable:
                    log.warning(f'[{self._appid}] 会话无效, 重新鉴权')
                    self._session_id = None
                    self._seq = None
                else:
                    log.warning(f'[{self._appid}] 会话无效但可恢复')
                self._gateway_url = None
                await asyncio.sleep(3)
                return 'invalid_session'
            else:
                self._status.publish('unknown', reason='unknown_opcode')
        return 'socket_eof'

    async def _on_hello(self, payload):
        """Hello → 启动心跳 + 鉴权/恢复"""
        if self._auth_mode is not None:
            self._resume_provable = False
            self._status.publish('unknown', reason='unexpected_hello')
            return
        self._heartbeat_interval = payload.get('d', {}).get('heartbeat_interval', 45000) / 1000
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        if self._session_id and self._seq is not None:
            await self._send_resume()
        else:
            self._auth_mode = 'identify'
            self._status.update_context(session_id=None, session_generation=None, resume_attempt_id=None)
            self._status.publish('identifying', reason='identify')
            await self._send_identify()

    async def _on_dispatch(self, payload):
        """事件分发 → 构造 Event 并回调"""
        if self._closed:
            return
        event_type = payload.get('t', '')
        if event_type == 'READY':
            if self._ready_seen:
                return
            if self._auth_mode != 'identify':
                self._resume_pending = False
                self._status.publish('unknown', reason='unexpected_ready')
                return
            data = payload.get('d')
            session_id = data.get('session_id') if isinstance(data, dict) else None
            if not isinstance(session_id, str) or not session_id:
                self._status.publish('unknown', reason='invalid_ready')
                return
            self._ready_seen = True
            self._session_id = session_id
            self._login_kind = self._status.new_session(session_id)
            self._status.publish('ready', reason='ready', login_kind=self._login_kind)
            log.info(f'[{self._appid}] WebSocket 已就绪 (session={self._session_id})')
            return
        if event_type == 'RESUMED':
            if self._resumed_seen:
                return
            self._resumed_seen = True
            if not self._resume_pending or self._auth_mode != 'resume':
                self._status.publish('unknown', reason='unexpected_resumed')
                return
            self._resume_pending = False
            sequence = payload.get('s')
            if not self._resume_provable or type(sequence) is not int or sequence < self._resume_from_sequence:
                self._status.publish('unknown', reason='unproven_resumed', login_kind='resume')
                return
            self._status.publish(
                'replay_complete',
                reason='resumed',
                continuity='resumed',
                login_kind='resume',
                resume_from_sequence=self._resume_from_sequence,
                replay_end_sequence=sequence,
                replay_end_receive_index=self._receive_index,
            )
            log.info(f'[{self._appid}] 会话已恢复')
            return
        try:
            event = Event.from_websocket(self._appid, payload)
            event.connection_context = self._status.context
            if self._on_event_received is not None:
                self._on_event_received(event, transport='websocket', session_id=self._session_id)
            log.debug(f'[{self._appid}] WS事件: {event}')
            if event_type == 'INTERACTION_CREATE':
                # 分发插件后用其 code 回复交互回调 (独立任务等待, 不阻塞读取循环)
                event.start_ack_countdown()
                task = self._start_interaction_task(self._dispatch_with_backpressure(event), event)
                task.add_done_callback(lambda t, ev=event: ev.finish_dispatch())
                self._start_interaction_task(self._ack_interaction_when_ready(event), event)
            else:
                self._ensure_event_workers()
                await self._event_queue.put(event)
        except Exception as e:
            log.error(f'[{self._appid}] 事件处理异常: {e}')

    def _start_interaction_task(self, coro, event):
        task = spawn(coro)
        self._interaction_tasks[task] = event
        task.add_done_callback(lambda finished: self._interaction_tasks.pop(finished, None))
        return task

    async def _ack_interaction_when_ready(self, event):
        """等待插件设置 code (或分发结束/超时) 后通过 HTTP PUT /interactions 回复 (与 WH 链路一致)。"""
        code = await event.wait_ack_code()
        try:
            await self._ack_interaction(event, code)
        except Exception as e:
            log.warning(f'[{self._appid}] 交互回调 ACK 异常: {e}')

    async def _dispatch_with_backpressure(self, event):
        """背压包装: Semaphore 控制并发, 满载时新事件等待空闲槽位"""
        for barrier in event._group_lifecycle_barriers:
            await asyncio.shield(barrier)
        async with self._event_sem:
            if self._closed:
                return
            try:
                await self._on_event(event)
            except Exception as e:
                log.error(f'[{self._appid}] 事件回调异常: {e}')

    async def _send_op(self, op, data, label=''):
        """发送 WS 操作帧"""
        await self._ws.send(json.dumps({'op': op, 'd': data}))
        if label:
            log.info(f'[{self._appid}] {label}')

    async def _send_identify(self):
        token = await self._tm.get_token()
        payload = {
            'token': f'QQBot {token}',
            'intents': self._intents,
            'shard': [0, 1],
        }
        await self._send_op(_OP_IDENTIFY, payload, '已发送鉴权')

    async def _send_resume(self):
        token = await self._tm.get_token()
        self._auth_mode = 'resume'
        self._resume_pending = True
        self._resume_from_sequence = self._seq
        self._resume_provable = (
            self._status.capabilities.replay_boundary and self._status.context.session_generation is not None and type(self._seq) is int and self._seq >= 0
        )
        self._resume_max_sequence = self._seq if type(self._seq) is int else -1
        self._status.update_context(resume_attempt_id=uuid4().hex)
        self._status.publish(
            'resuming',
            reason='resume',
            continuity='pending' if self._resume_provable else 'unknown',
            login_kind='resume',
            resume_from_sequence=self._seq,
        )
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
                raise
            except Exception as e:
                log.debug(f'[{self._appid}] 心跳发送失败, 退出心跳循环: {e}')
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

        url = f'{self._tm.api_base}/gateway/bot'
        token = await self._tm.get_token()
        client = await self._tm.get_client()
        resp = await client.get(url, headers={'Authorization': f'QQBot {token}'})
        data = resp.json()
        self._gateway_url = data.get('url', '')
        if not self._gateway_url:
            raise RuntimeError(f'获取网关失败: {data}')
        return self._gateway_url
