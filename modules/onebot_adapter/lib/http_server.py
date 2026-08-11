"""OneBot 11 正向 HTTP — 框架提供 HTTP API 供外部框架调用

外部框架通过 POST {path}/{action} (JSON 请求体为 params) 或
POST {path} (请求体 {action, params, echo}) 调用 OneBot action。
遵循 OneBot 11 HTTP 通信规范: https://github.com/botuniverse/onebot-11
"""

from __future__ import annotations

import json

from aiohttp import web

# 与 ws_server 相同的查表热更新机制 (aiohttp 路由注册后无法移除)
_ROUTE_TABLE: dict[str, OneBotHTTPServer | None] = {}


class OneBotHTTPServer:
    """OneBot 11 HTTP API 处理器 (正向 HTTP)"""

    __slots__ = ('_entries', '_on_action', '_log', '_debug')

    def __init__(self, *, entries, on_action, log, debug=False):
        # [{'name': str, 'path': str, 'token': str, 'appid': str, 'enable': bool}]
        self._entries = entries or []
        self._on_action = on_action
        self._log = log
        self._debug = debug

    def attach(self, app: web.Application) -> list[str]:
        """将 HTTP API 路由挂载到已有的 aiohttp Application, 返回成功挂载的路径列表"""
        mounted = []
        for entry in self._entries:
            path = entry['path']
            if path in _ROUTE_TABLE:
                _ROUTE_TABLE[path] = self
                mounted.append(path)
                continue
            try:
                app.router.add_route('*', path, _make_http_route_handler(path, ''))
                app.router.add_route('*', path.rstrip('/') + '/{action}', _make_http_route_handler(path, 'action'))
                _ROUTE_TABLE[path] = self
                mounted.append(path)
                self._log.info(f'正向 HTTP 路由已挂载: {path}')
            except (RuntimeError, ValueError):
                self._log.warning(f'正向 HTTP 路由注册跳过 (路由器已冻结, 需重启框架生效): {path}')
        return mounted

    def detach(self):
        """从路由表摘除本实例"""
        for path, srv in list(_ROUTE_TABLE.items()):
            if srv is self:
                _ROUTE_TABLE[path] = None

    def entry_for(self, path: str) -> dict | None:
        for entry in self._entries:
            if entry['path'] == path:
                return entry
        return None

    def status(self) -> dict:
        """返回各连接的运行状态 (供面板展示)"""
        result = {}
        for entry in self._entries:
            mounted = _ROUTE_TABLE.get(entry['path']) is self
            result[entry['name']] = {
                'mounted': mounted,
                'error': '' if mounted else '路径未挂载 (需重启框架生效)',
            }
        return result

    @staticmethod
    def _check_auth(request: web.Request, token: str) -> bool:
        if not token:
            return True
        auth = request.headers.get('Authorization', '')
        query_token = request.query.get('access_token', '')
        return auth in (f'Bearer {token}', f'Token {token}') or query_token == token

    async def handle(self, request: web.Request, entry: dict, action_in_path: str) -> web.StreamResponse:
        if not self._check_auth(request, entry.get('token', '')):
            self._log.warning(f'正向 HTTP 鉴权失败: {request.remote}')
            return web.json_response({'status': 'failed', 'retcode': 1403, 'data': None, 'msg': 'Unauthorized'}, status=401)

        action = action_in_path
        params: dict = {}
        echo = None

        if request.method == 'GET':
            params = dict(request.query)
            params.pop('access_token', None)
        else:
            try:
                raw = await request.text()
                body = json.loads(raw) if raw.strip() else {}
            except json.JSONDecodeError:
                body = None
            if body is None:
                # 兼容表单编码
                form = await request.post()
                body = dict(form)
            if action:
                params = body if isinstance(body, dict) else {}
            elif isinstance(body, dict):
                action = str(body.get('action', '') or '')
                params = body.get('params') or {}
                echo = body.get('echo')

        if not action:
            return web.json_response({'status': 'failed', 'retcode': 1400, 'data': None, 'msg': 'missing action'}, status=404)

        if self._debug:
            self._log.info(f'[HTTP←] action={action} params={json.dumps(params, ensure_ascii=False)[:500]}')

        try:
            result = await self._on_action(action, params, echo, entry.get('appid', ''))
        except Exception as e:
            self._log.error(f'onebot.http.action.{action}: {e}')
            result = {'status': 'failed', 'retcode': -1, 'data': None, 'msg': str(e), 'wording': str(e)}
            if echo is not None:
                result['echo'] = echo

        if self._debug:
            self._log.info(f'[HTTP→] resp={json.dumps(result, ensure_ascii=False)[:500]}')
        return web.json_response(result)


def _make_http_route_handler(path: str, mode: str):
    """生成查表分发的路由 handler (支持配置热更新)"""

    async def handler(request: web.Request):
        server = _ROUTE_TABLE.get(path)
        if server is None:
            return web.Response(status=404, text='Not Found')
        entry = server.entry_for(path)
        if entry is None or not entry.get('enable', True):
            return web.Response(status=404, text='Not Found')
        action = request.match_info.get('action', '') if mode == 'action' else ''
        return await server.handle(request, entry, action)

    return handler
