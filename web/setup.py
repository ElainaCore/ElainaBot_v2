"""Web 面板集成入口"""

import logging
import os
import sys

from aiohttp import web

import web.api as _panel_api
import web.auth as _auth

log = logging.getLogger('ElainaBot.web')


def _disable_sendfile_on_windows():
    """Windows 上禁用 sendfile (Proactor 回退复用缓冲区会导致静态文件损坏)"""
    if sys.platform != 'win32':
        return
    os.environ.setdefault('AIOHTTP_NOSENDFILE', '1')
    try:
        import aiohttp.web_fileresponse as _fr

        _fr.NOSENDFILE = True
    except Exception:
        pass


class _WebPanelLogHandler(logging.Handler):
    """将 Python logging 记录推送到 web 面板"""

    def __init__(self, ws_module):
        super().__init__()
        self._ws = ws_module

    def emit(self, record):
        try:
            from datetime import datetime

            from core.storage.log import SharedLogService

            msg = record.getMessage()
            level = record.levelname
            entry = {
                'timestamp': datetime.fromtimestamp(record.created).strftime('%Y-%m-%d %H:%M:%S'),
                'content': msg,
                'source': record.name,
                'level': level,
            }
            # 实时推送到面板
            self._ws.push_log('framework', entry)
            # 持久化到 SQLite
            shared = SharedLogService._instance
            if shared:
                shared.add_sync('framework', entry)
        except Exception:
            pass


@web.middleware
async def _api_no_cache_middleware(request: web.Request, handler):
    """API 响应禁止浏览器/代理缓存, 保证数据实时"""
    resp = await handler(request)
    if request.path.startswith('/api/') and not request.path.startswith('/api/media/'):
        resp.headers.setdefault('Cache-Control', 'no-store')
    return resp


def setup_web(app: web.Application, bot_manager, base_dir: str):
    """将 Web 面板挂载到 aiohttp 应用"""
    _disable_sendfile_on_windows()
    app.middlewares.append(_api_no_cache_middleware)
    _auth.init(base_dir)
    _panel_api.set_context(bot_manager, base_dir)

    # 注入日志推送 / 错误回调 / logging handler
    try:
        import web.ws as _ws
        from core.base.logger import on_error

        bot_manager._web_log_cb = _ws.push_log
        for _inst in bot_manager._bots.values():
            if hasattr(_inst, 'sender'):
                _inst.sender._web_log_cb = _ws.push_log
                _inst.sender._bot_name = getattr(_inst, 'name', '')
                _inst.sender._bot_qq = getattr(_inst, 'robot_qq', '')

        # 存储 sender 日志回调到注册表, 以便热连接新机器人时自动绑定
        if hasattr(bot_manager, 'bot_registry') and bot_manager.bot_registry is not None:
            bot_manager.bot_registry._sender_log_cb = _ws.push_log

        def _push_error(error_data):
            _ws.push_log(
                'error',
                {
                    'timestamp': error_data.get('timestamp', ''),
                    'appid': error_data.get('appid', '0000'),
                    'module_type': error_data.get('module_type', ''),
                    'module_name': error_data.get('module_name', ''),
                    'content': error_data.get('content', ''),
                    'traceback': error_data.get('traceback', ''),
                    'context': error_data.get('context', {}),
                },
            )

        on_error(_push_error)

        _handler = _WebPanelLogHandler(_ws)
        _handler.setLevel(logging.INFO)
        logging.getLogger('ElainaBot').addHandler(_handler)
    except Exception:
        pass
    app.router.add_routes(_panel_api.get_routes())

    # 媒体文件静态路由 (data/media/)
    media_dir = os.path.join(base_dir, 'data', 'media')
    os.makedirs(media_dir, exist_ok=True)
    app.router.add_static('/api/media/', media_dir)

    # dist 在 web-vue/dist/ (vite 输出) 或 web/dist/ (复制)
    _web_dir = os.path.dirname(__file__)
    _project_dir = os.path.dirname(_web_dir)
    dist_dir = os.path.join(_project_dir, 'web-vue', 'dist')
    if not os.path.isdir(dist_dir):
        dist_dir = os.path.join(_web_dir, 'dist')

    # /web → 重定向到 /web/
    app.router.add_get('/web', _redirect_to_web)

    if os.path.isdir(dist_dir):
        app.router.add_get('/web/{path:.*}', _make_spa_handler(dist_dir))
        log.info(f'Web 面板已挂载 (dist: {dist_dir})')
    else:
        app.router.add_get('/web/{path:.*}', _dev_placeholder)
        log.warning(f'Web 面板未找到编译产物 (期望路径: {dist_dir})')


_MIME = {
    '.js': 'application/javascript',
    '.css': 'text/css',
    '.html': 'text/html',
    '.json': 'application/json',
    '.svg': 'image/svg+xml',
    '.png': 'image/png',
    '.ico': 'image/x-icon',
}


def _make_spa_handler(dist_dir: str):
    async def handler(request: web.Request):
        path = request.match_info.get('path', '')
        if not path or path == '/':
            path = 'index.html'

        file_path = os.path.join(dist_dir, path.replace('/', os.sep))

        if os.path.isfile(file_path):
            ext = os.path.splitext(file_path)[1].lower()
            headers = {}
            ct = _MIME.get(ext)
            if ct:
                headers['Content-Type'] = ct
            if ext == '.html':
                headers['Cache-Control'] = 'no-cache'
            elif '/assets/' in path or path.startswith('assets/'):
                headers['Cache-Control'] = 'public, max-age=31536000, immutable'
            return web.FileResponse(file_path, headers=headers)

        index = os.path.join(dist_dir, 'index.html')
        if os.path.isfile(index):
            return web.FileResponse(index, headers={'Content-Type': 'text/html', 'Cache-Control': 'no-cache'})

        return web.Response(text='Not Found', status=404)

    return handler


async def _redirect_to_web(request: web.Request):
    raise web.HTTPFound('/web/')


async def _dev_placeholder(request: web.Request):
    html = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Elaina Panel</title></head>
<body style="background:#18181c;color:#fff;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
<div style="text-align:center">
<h1 style="color:#5865f2">Elaina 管理面板</h1>
<p style="color:#a0a0b0">未找到 <code>web/dist/</code> 目录, 请确保仓库完整克隆。</p>
<p style="color:#a0a0b0;font-size:14px">开发者可在 <code>web-vue/frontend/</code> 运行:</p>
<pre style="background:#1e1e24;padding:16px;border-radius:8px;color:#43b581">npm install && npm run build</pre>
</div></body></html>"""
    return web.Response(text=html, content_type='text/html')
