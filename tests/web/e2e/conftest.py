"""E2E 测试 conftest — Playwright + aiohttp 后端 fixture"""

import os
import sys
import tempfile

import pytest

# WEB_DIR = tests/web/e2e/../../ -> tests/web/ -> but dist is at ROOT/web/dist/
# ROOT = tests/web/e2e/../../../../ -> project root
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_DIST_DIR = os.path.join(ROOT, 'web', 'dist')


@pytest.fixture(scope='session')
def dist_dir():
    """SPA 构建产物目录"""
    if os.path.isdir(_DIST_DIR):
        return _DIST_DIR
    # fallback 到 web-vue/dist/
    alt = os.path.join(ROOT, 'web-vue', 'dist')
    if os.path.isdir(alt):
        return alt
    pytest.skip('No SPA dist found')


@pytest.fixture(scope='session')
def any_available_browser_name():
    """返回可用的浏览器名称 (chromium > firefox > webkit)"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None

    try:
        with sync_playwright() as p:
            for name in ('chromium', 'firefox', 'webkit'):
                try:
                    p[name].launch()
                    return name
                except Exception:
                    continue
    except Exception:
        pass
    return None


_SPA_MIME = {
    '.js': 'application/javascript',
    '.css': 'text/css',
    '.html': 'text/html',
    '.json': 'application/json',
    '.svg': 'image/svg+xml',
    '.png': 'image/png',
    '.ico': 'image/x-icon',
    '.woff': 'font/woff',
    '.woff2': 'font/woff2',
}


@pytest.fixture
async def e2e_app(dist_dir, api_config_dir):
    """创建完整的 aiohttp 应用, 包含 API 路由 + SPA 静态文件 + 测试 API"""
    from aiohttp import web

    import web.api as _api
    import web.auth as _auth
    from core.base.config import ConfigManager, cfg

    # 重置并初始化
    ConfigManager._instance = None
    mgr = ConfigManager()
    mgr.init(api_config_dir)

    _auth.valid_sessions.clear()
    _auth.ip_access_data.clear()
    _auth._data_dir = os.path.join(api_config_dir, 'data', 'web')
    os.makedirs(_auth._data_dir, exist_ok=True)
    _auth.init(api_config_dir)

    app = web.Application()

    # API 路由
    app.router.add_routes(_api.get_routes())

    # SPA 静态文件
    async def spa_handler(request: web.Request):
        path = request.match_info.get('path', '')
        if not path or path == '/':
            path = 'index.html'

        file_path = os.path.join(dist_dir, path.replace('/', os.sep))
        file_path = os.path.normpath(file_path)

        # 安全检查
        if not file_path.startswith(os.path.normpath(dist_dir)):
            return web.Response(status=403)

        if os.path.isfile(file_path):
            ext = os.path.splitext(file_path)[1].lower()
            ct = _SPA_MIME.get(ext)
            return web.FileResponse(file_path, headers={'Content-Type': ct} if ct else {})

        # SPA fallback: 所有路径返回 index.html
        index = os.path.join(dist_dir, 'index.html')
        if os.path.isfile(index):
            return web.FileResponse(index, headers={'Content-Type': 'text/html'})

        return web.Response(text='Not Found', status=404)

    # /web/ -> /web/index.html
    app.router.add_get('/web', lambda r: web.HTTPFound('/web/'))
    app.router.add_get('/web/{path:.*}', spa_handler)

    # 测试辅助 API: /api/test/token — 生成测试 token
    async def test_token(request: web.Request):
        token = _auth.create_session(request)
        return web.json_response({'token': token})

    app.router.add_post('/api/test/token', test_token)

    # 媒体文件
    media_dir = os.path.join(api_config_dir, 'data', 'media')
    os.makedirs(media_dir, exist_ok=True)
    app.router.add_static('/api/media/', media_dir)

    return app


@pytest.fixture
async def e2e_client(e2e_app):
    """aiohttp 测试客户端 (含 SPA 服务, 不依赖 pytest-aiohttp)"""
    from aiohttp.test_utils import TestClient, TestServer

    server = TestServer(e2e_app)
    await server.start_server()
    client = TestClient(server)
    await client.start_server()
    yield client
    await client.close()
    await server.close()


@pytest.fixture
async def e2e_token(e2e_client):
    """获取 E2E 测试 token"""
    resp = await e2e_client.post('/api/test/token')
    data = await resp.json()
    return data['token']
