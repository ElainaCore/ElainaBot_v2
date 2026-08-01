"""Web 面板集成入口"""

import contextlib
import logging
import os
import re
import sys
from datetime import datetime

import aiohttp.web_fileresponse as _fr
from aiohttp import web

import web.api as _panel_api
import web.auth as _auth
import web.ws as _ws
from core.base import console as _console
from core.base.logger import on_error
from core.storage.log import SharedLogService

log = logging.getLogger('ElainaBot.web')


def _disable_sendfile_on_windows():
    """Windows 上禁用 sendfile (Proactor 回退复用缓冲区会导致静态文件损坏)"""
    if sys.platform != 'win32':
        return
    os.environ.setdefault('AIOHTTP_NOSENDFILE', '1')
    with contextlib.suppress(AttributeError):
        _fr.NOSENDFILE = True


class _WebPanelLogHandler(logging.Handler):
    """将 Python logging 记录推送到 web 面板"""

    def __init__(self, ws_module):
        super().__init__()
        self._ws = ws_module

    def emit(self, record):
        try:
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
            # 日志处理器内部异常不外抛也不再记日志, 避免递归
            pass


def _push_console_line(entry: dict):
    _ws.push_log('console', dict(entry))


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

        _console.install()
        _console.on_line(_push_console_line)

        _handler = _WebPanelLogHandler(_ws)
        _handler.setLevel(logging.INFO)
        logging.getLogger('ElainaBot').addHandler(_handler)
    except Exception as e:
        logging.getLogger('ElainaBot.web').debug(f'面板日志推送安装失败: {e}')
    app.router.add_routes(_panel_api.get_routes())

    # 媒体文件静态路由 (data/media/)
    media_dir = os.path.join(base_dir, 'data', 'media')
    os.makedirs(media_dir, exist_ok=True)
    app.router.add_static('/api/media/', media_dir)

    # 优先使用仓库内随版本发布的 web/dist, 避免旧 web-vue/dist 覆盖新构建
    _web_dir = os.path.dirname(__file__)
    _project_dir = os.path.dirname(_web_dir)
    dist_dir = _select_dist_dir(_project_dir, _web_dir)

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
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.webp': 'image/webp',
    '.ico': 'image/x-icon',
}

_ASSET_REF_PATTERN = re.compile(r'(?:/web/)?assets/[A-Za-z0-9_.-]+\.(?:css|js)')


def _missing_dist_assets(dist_dir: str) -> list[str]:
    index_path = os.path.join(dist_dir, 'index.html')
    if not os.path.isfile(index_path):
        return ['index.html']

    refs = set()
    for path in (index_path, os.path.join(dist_dir, 'assets', 'index.js')):
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding='utf-8') as file:
                refs.update(_ASSET_REF_PATTERN.findall(file.read()))
        except OSError:
            continue

    return sorted(ref for ref in refs if not os.path.isfile(os.path.join(dist_dir, ref.removeprefix('/web/'))))


def _select_dist_dir(project_dir: str, web_dir: str) -> str:
    candidates = [
        os.path.join(web_dir, 'dist'),
        os.path.join(project_dir, 'web-vue', 'dist'),
    ]
    existing = []
    for candidate in candidates:
        if not os.path.isdir(candidate):
            continue
        existing.append(candidate)
        missing = _missing_dist_assets(candidate)
        if not missing:
            return candidate
        log.error('Web 面板构建不完整 (%s): 缺少 %s', candidate, ', '.join(missing))
    return existing[0] if existing else candidates[0]


_ASSET_REF_RE = re.compile(r'((?:src|href)=")(/web/assets/[^"?]+)(")')


def _dist_version(dist_root: str) -> int:
    """以产物文件最新修改时间作为版本号"""
    latest = 0
    assets = os.path.join(dist_root, 'assets')
    for d in (dist_root, assets):
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            p = os.path.join(d, name)
            if os.path.isfile(p):
                latest = max(latest, int(os.path.getmtime(p)))
    return latest


def _serve_index(dist_root: str, index_path: str):
    """返回 index.html, 给资源引用附加 ?v=版本号 使更新后自动拉取新文件"""
    try:
        with open(index_path, encoding='utf-8') as f:
            html = f.read()
        ver = _dist_version(dist_root)
        html = _ASSET_REF_RE.sub(lambda m: f'{m.group(1)}{m.group(2)}?v={ver}{m.group(3)}', html)
        return web.Response(text=html, content_type='text/html', headers={'Cache-Control': 'no-store'})
    except OSError:
        return web.FileResponse(index_path, headers={'Content-Type': 'text/html', 'Cache-Control': 'no-store'})


def _make_spa_handler(dist_dir: str):
    dist_root = os.path.realpath(dist_dir)

    async def handler(request: web.Request):
        path = request.match_info.get('path', '')
        if not path or path == '/':
            path = 'index.html'

        file_path = os.path.join(dist_root, path.replace('/', os.sep))
        real_path = os.path.realpath(file_path)
        if real_path != dist_root and not real_path.startswith(dist_root + os.sep):
            return _spa_index_or_404(dist_root)

        if os.path.isfile(real_path):
            file_path = real_path
            ext = os.path.splitext(file_path)[1].lower()
            headers = {}
            ct = _MIME.get(ext)
            if ct:
                headers['Content-Type'] = ct
            if ext == '.html':
                return _serve_index(dist_root, file_path)
            if '/assets/' in path or path.startswith('assets/'):
                # 产物文件名不带 hash, 更新后强缓存会导致新旧分块混用报错; 用协商缓存 (304)
                headers['Cache-Control'] = 'no-cache, must-revalidate'
            return web.FileResponse(file_path, headers=headers)

        if path.startswith('assets/'):
            return web.Response(text='Not Found', status=404, headers={'Cache-Control': 'no-store'})

        return _spa_index_or_404(dist_root)

    return handler


def _spa_index_or_404(dist_root: str):
    """SPA 回退: 返回 index.html, 不存在则 404"""
    index = os.path.join(dist_root, 'index.html')
    if os.path.isfile(index):
        return _serve_index(dist_root, index)
    return web.Response(text='Not Found', status=404)


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
