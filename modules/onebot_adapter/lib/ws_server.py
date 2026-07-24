"""OneBot 11 WebSocket — 同时支持正向 WS 和反向 WS

正向 WS: 挂载到框架已有端口, 外部框架连接 ws://host:port{path} (可配置多条, 各自 token);
        路径配置为 / 时不校验路径 (仅接管未被其它路由匹配的 WS 升级请求, 不影响 Web 面板);
        配置独立端口 (port) 时启动独立监听, 任意路径均可连接 (如 ws://127.0.0.1:5200)
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

# aiohttp 路由无法在注册后移除, 用模块级路由表实现热更新:
# 路由 handler 在请求时查表取当前活跃的 server 实例, 配置重载后旧实例被替换即生效
_ROUTE_TABLE: dict[str, OneBotWSServer | None] = {}


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
        '_hb_interval',
        '_forward_entries',
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
        '_msg_tasks',
        '_reverse_status',
        '_standalone_runners',
        '_standalone_status',
    )

    def __init__(
        self,
        *,
        heartbeat_interval,
        on_action,
        default_qq=0,
        qq_map=None,
        log,
        forward_entries=None,
        reverse_entries=None,
        debug=False,
    ):
        self._hb_interval = heartbeat_interval
        self._on_action = on_action
        self._default_qq = default_qq
        self.qq_map = qq_map or {}  # {appid_str: robot_qq_int}
        self._log = log
        self._debug = debug
        # [{'name': str, 'path': str, 'token': str}]
        self._forward_entries = forward_entries or []
        # [{'name': str, 'url': str, 'appid': str, 'token': str, 'reconnect_interval': int}]
        self._reverse_entries = reverse_entries or []

        self._clients: set[_WSWrapper] = set()
        self._hb_task = None
        self._reverse_tasks: list[asyncio.Task] = []
        self._reverse_session: aiohttp.ClientSession | None = None
        self._msg_tasks: set[asyncio.Task] = set()
        self._reverse_status: dict[str, dict] = {}  # name -> {connected: bool, error: str}
        self._standalone_runners: list[web.AppRunner] = []
        self._standalone_status: dict[str, dict] = {}  # name -> {listening: bool, error: str}

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

    def attach(self, app: web.Application) -> list[str]:
        """将正向 WS 路由挂载到已有的 aiohttp Application, 返回成功挂载的路径列表

        通过模块级路由表实现热更新: 已注册过的路径直接更新表项即可生效;
        新路径在路由器冻结后无法注册, 需重启框架。
        路径为 / 时通过中间件接管未被其它路由匹配的 WS 升级请求 (不影响 Web 面板等已有路由)。
        """
        mounted = []
        for entry in self._forward_entries:
            if int(entry.get('port', 0) or 0) > 0:
                continue  # 独立端口连接由 start_standalone 启动
            path = entry['path']
            if path in _ROUTE_TABLE:
                _ROUTE_TABLE[path] = self
                mounted.append(path)
                continue
            try:
                if path == '/':
                    _install_wildcard_middleware(app)
                else:
                    app.router.add_get(path, _make_ws_route_handler(path))
                _ROUTE_TABLE[path] = self
                mounted.append(path)
                self._log.info(f'正向 WS 路由已挂载: {path}' + (' (不校验路径)' if path == '/' else ''))
            except (RuntimeError, ValueError):
                self._log.warning(f'正向 WS 路由注册跳过 (路由器已冻结, 需重启框架生效): {path}')
        return mounted

    async def start_standalone(self):
        """为配置了独立端口的正向 WS 启动独立监听 (任意路径均可连接)"""
        for entry in self._forward_entries:
            port = int(entry.get('port', 0) or 0)
            if port <= 0:
                continue
            name = entry.get('name', f':{port}')

            def _make_handler(e):
                async def handler(request: web.Request):
                    return await self.handle_forward_ws(request, e)

                return handler

            app = web.Application()
            app.router.add_get('/{tail:.*}', _make_handler(entry))
            runner = web.AppRunner(app)
            try:
                await runner.setup()
                site = web.TCPSite(runner, '0.0.0.0', port)
                await site.start()
                self._standalone_runners.append(runner)
                self._standalone_status[name] = {'listening': True, 'error': ''}
                self._log.info(f'正向 WS 独立监听已启动: ws://0.0.0.0:{port} (任意路径)')
            except Exception as e:
                self._standalone_status[name] = {'listening': False, 'error': str(e)}
                self._log.error(f'正向 WS 独立监听启动失败 [:{port}]: {e}')
                with contextlib.suppress(Exception):
                    await runner.cleanup()

    def detach(self):
        """从路由表摘除本实例 (配置重载/模块停止时调用)"""
        for path, srv in list(_ROUTE_TABLE.items()):
            if srv is self:
                _ROUTE_TABLE[path] = None

    def _forward_entry_for(self, path: str) -> dict | None:
        for entry in self._forward_entries:
            if entry['path'] == path:
                return entry
        return None

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
                task = asyncio.create_task(self._reverse_ws_loop(url, entry))
                self._reverse_tasks.append(task)
                tag = f'{url} (appid={appid})' if appid else url
                self._log.info(f'反向 WS 连接任务已创建: {tag}')

    async def _reverse_ws_loop(self, url: str, entry: dict):
        """反向 WS 持续连接循环 (断线重连)"""
        appid = entry.get('appid', '')
        name = entry.get('name', url)
        token = entry.get('token', '')
        reconnect_interval = int(entry.get('reconnect_interval', 5) or 5)
        headers = {}
        if token:
            headers['Authorization'] = f'Bearer {token}'

        while True:
            self_qq = self.resolve_qq(appid)
            headers['X-Self-ID'] = str(self_qq)
            headers['X-Client-Role'] = 'Universal'
            try:
                self._log.info(f'反向 WS 正在连接: {url}')
                async with self._reverse_session.ws_connect(url, headers=headers) as ws:
                    wrapper = _WSWrapper(ws, remote=url, is_client=True, appid=appid, self_qq=self_qq)
                    self._clients.add(wrapper)
                    self._reverse_status[name] = {'connected': True, 'error': ''}
                    msg = f'反向 WS 已连接: {url} (self_qq={self_qq}, appid={appid}, 当前 {len(self._clients)} 个)'
                    self._log.info(msg)
                    await wrapper.send_str(self._lifecycle_json(self_qq))

                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            self._dispatch_message(wrapper, msg.data)
                        elif msg.type in (
                            aiohttp.WSMsgType.CLOSED,
                            aiohttp.WSMsgType.ERROR,
                        ):
                            break

                    self._clients.discard(wrapper)
                    self._reverse_status[name] = {'connected': False, 'error': '连接断开'}
                    self._log.warning(f'反向 WS 断开: {url}, appid={appid}, 当前 {len(self._clients)} 个)')
            except asyncio.CancelledError:
                self._reverse_status[name] = {'connected': False, 'error': '已停止'}
                raise
            except Exception as e:
                self._reverse_status[name] = {'connected': False, 'error': str(e)}
                self._log.warning(f'反向 WS 连接失败 [{url}], appid={appid}: {e}')

            await asyncio.sleep(reconnect_interval)

    # ==================== 生命周期 ====================

    def start_heartbeat(self):
        """启动心跳 (正向/反向共用)"""
        if self._hb_interval > 0 and not self._hb_task:
            self._hb_task = asyncio.create_task(self._heartbeat_loop())

    async def stop(self):
        self.detach()
        tasks = ([self._hb_task] if self._hb_task else []) + self._reverse_tasks
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._reverse_tasks.clear()

        # 等待未完成的消息处理任务
        if self._msg_tasks:
            await asyncio.gather(*self._msg_tasks, return_exceptions=True)
            self._msg_tasks.clear()

        if self._reverse_session:
            await self._reverse_session.close()
            self._reverse_session = None

        for ws in list(self._clients):
            with contextlib.suppress(Exception):
                await ws.close()
        self._clients.clear()

        for runner in self._standalone_runners:
            with contextlib.suppress(Exception):
                await runner.cleanup()
        self._standalone_runners.clear()
        self._standalone_status.clear()

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

    def status(self) -> dict:
        """返回各连接的运行状态 (供面板展示)"""
        forward_clients = [c for c in self._clients if not c._is_client]
        result = {'forward': {}, 'reverse': {}}
        for entry in self._forward_entries:
            if int(entry.get('port', 0) or 0) > 0:
                st = self._standalone_status.get(entry['name'], {'listening': False, 'error': '未启动'})
                result['forward'][entry['name']] = {
                    'mounted': st['listening'],
                    'clients': len(forward_clients),
                    'error': st['error'] if not st['listening'] else '',
                }
                continue
            mounted = _ROUTE_TABLE.get(entry['path']) is self
            result['forward'][entry['name']] = {
                'mounted': mounted,
                'clients': len(forward_clients),
                'error': '' if mounted else '路径未挂载 (需重启框架生效)',
            }
        for entry in self._reverse_entries:
            st = self._reverse_status.get(entry['name'], {'connected': False, 'error': ''})
            result['reverse'][entry['name']] = st
        return result

    # ==================== 正向 WS 处理 ====================

    async def handle_forward_ws(self, request: web.Request, entry: dict):
        # 鉴权 (按连接各自的 token)
        token = entry.get('token', '')
        if token:
            auth = request.headers.get('Authorization', '')
            query_token = request.query.get('access_token', '')
            valid = {f'Bearer {token}', f'Token {token}'}
            if auth not in valid and query_token != token:
                self._log.warning(f'正向 WS 鉴权失败: {request.remote}')
                return web.Response(status=401, text='Unauthorized')

        ws = web.WebSocketResponse()
        await ws.prepare(request)

        appid = entry.get('appid', '')
        self_qq = self.resolve_qq(appid)
        wrapper = _WSWrapper(ws, remote=str(request.remote), appid=appid, self_qq=self_qq)
        self._clients.add(wrapper)
        self._log.info(f'正向 WS 客户端已连接: {request.remote} (当前 {len(self._clients)} 个)')
        await wrapper.send_str(self._lifecycle_json(self_qq))

        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    self._dispatch_message(wrapper, msg.data)
                elif msg.type == web.WSMsgType.ERROR:
                    self._log.warning(f'正向 WS 错误: {ws.exception()}')
        except Exception as e:
            self._log.warning(f'正向 WS 连接异常: {e}')
        finally:
            self._clients.discard(wrapper)
            self._log.info(f'正向 WS 客户端已断开: {request.remote} (剩余 {len(self._clients)} 个)')

        return ws

    # ==================== 公共消息处理 ====================

    def _dispatch_message(self, ws: _WSWrapper, raw: str):
        """将消息处理派发为独立任务, 避免阻塞 WS 接收循环"""
        task = asyncio.create_task(self._handle_message(ws, raw))
        self._msg_tasks.add(task)
        task.add_done_callback(self._msg_tasks.discard)

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
            import traceback

            trace = ''.join(traceback.format_exception(e))
            self._log.error(f'onebot._handle_message.action.{action}:{e}\n{trace}')
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


@web.middleware
async def _wildcard_ws_middleware(request: web.Request, handler):
    """路径为 / 的正向 WS: 接管未被其它路由匹配的 WebSocket 升级请求, 其余请求原样放行"""
    if (
        request.method == 'GET'
        and 'websocket' in request.headers.get('Upgrade', '').lower()
        and getattr(request.match_info, 'http_exception', None) is not None
    ):
        server = _ROUTE_TABLE.get('/')
        if server is not None:
            entry = server._forward_entry_for('/')
            if entry is not None and entry.get('enable', True):
                return await server.handle_forward_ws(request, entry)
    return await handler(request)


def _install_wildcard_middleware(app: web.Application):
    """安装通配 WS 中间件 (幂等); app 冻结后 append 会抛 RuntimeError, 由调用方处理"""
    if _wildcard_ws_middleware not in app.middlewares:
        app.middlewares.append(_wildcard_ws_middleware)


def _make_ws_route_handler(path: str):
    """生成查表分发的路由 handler (支持配置热更新)"""

    async def handler(request: web.Request):
        server = _ROUTE_TABLE.get(path)
        if server is None:
            return web.Response(status=404, text='Not Found')
        entry = server._forward_entry_for(path)
        if entry is None or not entry.get('enable', True):
            return web.Response(status=404, text='Not Found')
        return await server.handle_forward_ws(request, entry)

    return handler
