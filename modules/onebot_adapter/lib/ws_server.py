"""OneBot 11 WebSocket — 同时支持正向 WS 和反向 WS

正向 WS: 挂载到框架已有端口, 外部框架连接 ws://host:port/onebot
反向 WS: 主动连接外部框架的 WS 地址, 如 ws://yunzai:2536/OneBot/v11/ws
遵循 OneBot 11 标准: https://github.com/botuniverse/onebot-11
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time

import aiohttp
from aiohttp import web

_B64_RE = re.compile(r'(base64://|"base64://|data:image[^,]*,)[A-Za-z0-9+/=]{64,}')


def _mask_b64(s: str) -> str:
    return _B64_RE.sub(lambda m: m.group(1) + '<...base64...>', s)


class _WSWrapper:
    """统一的 WS 发送接口, 兼容 aiohttp ServerWS 和 ClientWS"""

    __slots__ = ('_ws', '_is_client', 'remote', 'appid', 'self_qq')

    def __init__(self, ws, remote='', is_client=False, appid='', self_qq=0):
        self._ws = ws
        self._is_client = is_client
        self.remote = remote
        self.appid = appid  # 绑定的 appid (空=接收所有)
        self.self_qq = self_qq  # 该连接的 self_id

    async def send_str(self, data: str):
        await self._ws.send_str(data)

    async def close(self):
        await self._ws.close()

    @property
    def closed(self):
        return self._ws.closed if hasattr(self._ws, 'closed') else False


class OneBotWSServer:
    """OneBot 11 WS 处理器 (正向 + 反向)"""

    __slots__ = (
        '_token',
        '_hb_interval',
        '_ws_path',
        '_reverse_entries',
        '_on_action',
        '_default_qq',
        'qq_map',
        '_log',
        '_debug',
        '_clients',
        '_hb_task',
        '_reverse_tasks',
        '_reverse_session',
        '_reconnect_interval',
    )

    def __init__(
        self,
        *,
        access_token,
        heartbeat_interval,
        on_action,
        default_qq=0,
        qq_map=None,
        log,
        ws_path='/onebot',
        reverse_entries=None,
        reconnect_interval=5,
        debug=False,
    ):
        self._token = access_token or ''
        self._hb_interval = heartbeat_interval
        self._on_action = on_action
        self._default_qq = default_qq
        self.qq_map = qq_map or {}  # {appid_str: robot_qq_int}
        self._log = log
        self._debug = debug
        self._ws_path = ws_path
        self._reverse_entries = reverse_entries or []  # [{'url': str, 'appid': str}]
        self._reconnect_interval = reconnect_interval

        self._clients: set[_WSWrapper] = set()
        self._hb_task = None
        self._reverse_tasks: list[asyncio.Task] = []
        self._reverse_session: aiohttp.ClientSession | None = None

    def resolve_qq(self, appid: str = '') -> int:
        """按 appid 获取 self_qq, 兜底用 default_qq"""
        return self.qq_map.get(appid, self._default_qq) or self._default_qq

    @property
    def has_clients(self) -> bool:
        return bool(self._clients)

    def _lifecycle_json(self, self_qq: int, sub_type: str = 'connect') -> str:
        return json.dumps(
            {
                'time': int(time.time()),
                'self_id': self_qq,
                'post_type': 'meta_event',
                'meta_event_type': 'lifecycle',
                'sub_type': sub_type,
            },
            ensure_ascii=False,
        )

    # ==================== 正向 WS (服务端) ====================

    def attach(self, app: web.Application):
        """将正向 WS 路由挂载到已有的 aiohttp Application"""
        try:
            app.router.add_get(self._ws_path, self._forward_ws_handler)
            self._log.info(f'正向 WS 路由已挂载: {self._ws_path}')
        except (RuntimeError, ValueError):
            self._log.warning(f'正向 WS 路由注册跳过 (路由器已冻结, 需重启框架生效): {self._ws_path}')

    # ==================== 反向 WS (客户端) ====================

    @staticmethod
    def _normalize_ws_url(url: str) -> str:
        """将 http(s):// 转为 ws(s)://, 无 scheme 则补 ws://"""
        u = url.strip()
        if u.startswith('http://'):
            u = 'ws://' + u[7:]
        elif u.startswith('https://'):
            u = 'wss://' + u[8:]
        elif not u.startswith(('ws://', 'wss://')):
            u = 'ws://' + u
        return u

    async def start_reverse(self):
        """启动所有反向 WS 连接"""
        if not self._reverse_entries:
            return
        self._reverse_session = aiohttp.ClientSession()
        for entry in self._reverse_entries:
            url = self._normalize_ws_url(entry['url'])
            appid = entry.get('appid', '')
            if url:
                task = asyncio.create_task(self._reverse_ws_loop(url, appid))
                self._reverse_tasks.append(task)
                tag = f'{url} (appid={appid})' if appid else url
                self._log.info(f'反向 WS 连接任务已创建: {tag}')

    async def _reverse_ws_loop(self, url: str, appid: str = ''):
        """反向 WS 持续连接循环 (断线重连)"""
        headers = {}
        if self._token:
            headers['Authorization'] = f'Bearer {self._token}'

        while True:
            self_qq = self.resolve_qq(appid)
            headers['X-Self-ID'] = str(self_qq)
            headers['X-Client-Role'] = 'Universal'
            try:
                self._log.info(f'反向 WS 正在连接: {url}')
                async with self._reverse_session.ws_connect(url, headers=headers, ssl=False) as ws:
                    wrapper = _WSWrapper(ws, remote=url, is_client=True, appid=appid, self_qq=self_qq)
                    self._clients.add(wrapper)
                    self._log.info(f'反向 WS 已连接: {url} (self_qq={self_qq}, 当前 {len(self._clients)} 个)')
                    await wrapper.send_str(self._lifecycle_json(self_qq))

                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            await self._handle_message(wrapper, msg.data)
                        elif msg.type in (
                            aiohttp.WSMsgType.CLOSED,
                            aiohttp.WSMsgType.ERROR,
                        ):
                            break

                    self._clients.discard(wrapper)
                    self._log.warning(f'反向 WS 断开: {url}')
            except asyncio.CancelledError:
                return
            except Exception as e:
                self._log.warning(f'反向 WS 连接失败 [{url}]: {e}')

            await asyncio.sleep(self._reconnect_interval)

    # ==================== 生命周期 ====================

    def start_heartbeat(self):
        """启动心跳 (正向/反向共用)"""
        if self._hb_interval > 0 and not self._hb_task:
            self._hb_task = asyncio.create_task(self._heartbeat_loop())

    async def stop(self):
        tasks = ([self._hb_task] if self._hb_task else []) + self._reverse_tasks
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._reverse_tasks.clear()

        if self._reverse_session:
            await self._reverse_session.close()
            self._reverse_session = None

        for ws in list(self._clients):
            with contextlib.suppress(Exception):
                await ws.close()
        self._clients.clear()

    async def broadcast(self, event: dict, appid: str = ''):
        """推送事件, 按 appid 过滤 (空=全部)"""
        if not self._clients:
            return
        data = json.dumps(event, ensure_ascii=False)
        if self._debug and event.get('post_type') != 'meta_event':
            self._log.info(f'[WS→] {data}')
        dead = set()
        for ws in list(self._clients):
            if ws.appid and appid and ws.appid != appid:
                continue
            try:
                await ws.send_str(data)
            except Exception:
                dead.add(ws)
        self._clients.difference_update(dead)

    # ==================== 正向 WS 处理 ====================

    async def _forward_ws_handler(self, request: web.Request):
        # 鉴权
        if self._token:
            auth = request.headers.get('Authorization', '')
            query_token = request.query.get('access_token', '')
            valid = {f'Bearer {self._token}', f'Token {self._token}'}
            if auth not in valid and query_token != self._token:
                self._log.warning(f'正向 WS 鉴权失败: {request.remote}')
                return web.Response(status=401, text='Unauthorized')

        ws = web.WebSocketResponse()
        await ws.prepare(request)

        self_qq = self._default_qq
        wrapper = _WSWrapper(ws, remote=str(request.remote), self_qq=self_qq)
        self._clients.add(wrapper)
        self._log.info(f'正向 WS 客户端已连接: {request.remote} (当前 {len(self._clients)} 个)')
        await wrapper.send_str(self._lifecycle_json(self_qq))

        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    await self._handle_message(wrapper, msg.data)
                elif msg.type == web.WSMsgType.ERROR:
                    self._log.warning(f'正向 WS 错误: {ws.exception()}')
        except Exception as e:
            self._log.warning(f'正向 WS 连接异常: {e}')
        finally:
            self._clients.discard(wrapper)
            self._log.info(f'正向 WS 客户端已断开: {request.remote} (剩余 {len(self._clients)} 个)')

        return ws

    # ==================== 公共消息处理 ====================

    async def _handle_message(self, ws: _WSWrapper, raw: str):
        """处理客户端发来的 action 请求 (正向/反向共用)"""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            self._log.warning(f'无法解析的 WS 消息: {raw[:200]}')
            return

        action = data.get('action', '')
        params = data.get('params', {})
        echo = data.get('echo')

        if not action:
            return

        if self._debug:
            self._log.info(f'[WS←] action={action} params={_mask_b64(json.dumps(params, ensure_ascii=False))}')

        try:
            result = await self._on_action(action, params, echo, ws.appid)
        except Exception as e:
            self._log.error(f"处理 action '{action}' 异常: {e}")
            result = {
                'status': 'failed',
                'retcode': -1,
                'data': None,
                'msg': str(e),
                'wording': str(e),
            }
            if echo is not None:
                result['echo'] = echo

        resp = json.dumps(result, ensure_ascii=False)
        if self._debug:
            self._log.info(f'[WS→] resp={resp}')
        with contextlib.suppress(Exception):
            await ws.send_str(resp)

    # ==================== 心跳 ====================

    async def _heartbeat_loop(self):
        """定期发送心跳元事件 (每个连接用自己的 self_qq)"""
        while True:
            await asyncio.sleep(self._hb_interval)
            dead = []
            for ws in list(self._clients):
                hb = json.dumps(
                    {
                        'time': int(time.time()),
                        'self_id': ws.self_qq or self._default_qq,
                        'post_type': 'meta_event',
                        'meta_event_type': 'heartbeat',
                        'status': {'online': True, 'good': True},
                        'interval': self._hb_interval * 1000,
                    }
                )
                try:
                    await ws.send_str(hb)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self._clients.discard(ws)
