"""Auth API 集成测试 — 使用 aiohttp test client 测试完整鉴权流程"""

import os
import sys
import tempfile

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


# ==================== Fixtures ====================


@pytest.fixture
def integration_config_dir():
    """创建含有最小配置的临时配置目录"""
    import yaml

    with tempfile.TemporaryDirectory() as tmpdir:
        settings = {
            'server': {'host': '0.0.0.0', 'port': 15200},
            'web': {'admin_password': 'test_integration_pwd'},
        }
        with open(os.path.join(tmpdir, 'settings.yaml'), 'w') as f:
            yaml.dump(settings, f)

        bots = {
            'bots': [
                {
                    'appid': '999999',
                    'secret': 'test_secret',
                    'robot_qq': '111111111',
                }
            ],
        }
        with open(os.path.join(tmpdir, 'bot.yaml'), 'w') as f:
            yaml.dump(bots, f)

        yield tmpdir


@pytest.fixture
def setup_auth_app(integration_config_dir):
    """初始化 ConfigManager + web.auth 的 aiohttp Application"""
    import web.api as api
    import web.auth as auth
    from core.base.config import cfg

    # 重置单例状态: cfg 是模块级实例, 通过 _ready=False 重新 init
    cfg._ready = False
    cfg.init(integration_config_dir)

    auth.init(integration_config_dir)

    app = web.Application()
    app.router.add_routes(api.get_routes())
    # /health 由 HttpServer 注册, 这里手动添加
    async def _health_handler(_request):
        return web.json_response({'status': 'ok', 'bots': 0, 'plugins': 0})
    app.router.add_get('/health', _health_handler)

    api._bot_manager = None
    api._base_dir = integration_config_dir

    return app


@pytest.fixture
async def auth_client(setup_auth_app):
    """创建 aiohttp TestClient (不依赖 pytest-aiohttp)"""
    server = TestServer(setup_auth_app)
    await server.start_server()
    client = TestClient(server)
    await client.start_server()
    yield client
    await client.close()
    await server.close()


# ==================== 鉴权流程测试 ====================


@pytest.mark.integration
class TestAuthLoginFlow:
    """登录 → token → 鉴权 → 密码状态完整流程"""

    async def test_password_status_requires_auth(self, auth_client):
        """/api/auth/password-status 需要鉴权, 无 token 返回 401"""
        resp = await auth_client.get('/api/auth/password-status')
        assert resp.status == 401

    async def test_login_with_correct_password(self, auth_client):
        resp = await auth_client.post(
            '/api/auth/login',
            json={'password': 'test_integration_pwd'},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data['success'] is True
        assert len(data['data']['token']) > 0

    async def test_login_with_wrong_password(self, auth_client):
        resp = await auth_client.post(
            '/api/auth/login',
            json={'password': 'wrong_password'},
        )
        assert resp.status == 401
        data = await resp.json()
        assert data['success'] is False

    async def test_login_with_empty_dict(self, auth_client):
        resp = await auth_client.post(
            '/api/auth/login',
            json={},
        )
        assert resp.status == 401

    async def test_auth_check_without_token(self, auth_client):
        resp = await auth_client.get('/api/auth/check')
        assert resp.status == 401

    async def test_full_login_and_check_flow(self, auth_client):
        login_resp = await auth_client.post(
            '/api/auth/login',
            json={'password': 'test_integration_pwd'},
        )
        assert login_resp.status == 200
        login_data = await login_resp.json()
        token = login_data['data']['token']

        check_resp = await auth_client.get(
            '/api/auth/check',
            headers={'Authorization': f'Bearer {token}'},
        )
        assert check_resp.status == 200
        check_data = await check_resp.json()
        assert check_data['success'] is True

    async def test_login_multiple_times_gets_new_tokens(self, auth_client):
        tokens = set()
        for _ in range(3):
            resp = await auth_client.post(
                '/api/auth/login',
                json={'password': 'test_integration_pwd'},
            )
            assert resp.status == 200
            data = await resp.json()
            tokens.add(data['data']['token'])
        assert len(tokens) == 3


@pytest.mark.integration
class TestAuthProtectedEndpoints:
    """受保护接口的鉴权验证"""

    async def test_config_endpoint_without_auth(self, auth_client):
        resp = await auth_client.get('/api/config')
        assert resp.status == 401

    async def test_bots_endpoint_without_auth(self, auth_client):
        resp = await auth_client.get('/api/bots')
        assert resp.status == 401

    async def test_system_info_without_auth(self, auth_client):
        resp = await auth_client.get('/api/system/info')
        assert resp.status == 401

    async def test_config_endpoint_with_valid_token(self, auth_client):
        login_resp = await auth_client.post(
            '/api/auth/login',
            json={'password': 'test_integration_pwd'},
        )
        token = (await login_resp.json())['data']['token']

        resp = await auth_client.get(
            '/api/config',
            headers={'Authorization': f'Bearer {token}'},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data['success'] is True


@pytest.mark.integration
class TestAuthEdgeCases:
    """鉴权边界情况"""

    async def test_empty_request_body_login(self, auth_client):
        resp = await auth_client.post(
            '/api/auth/login',
            data='',
            headers={'Content-Type': 'application/json'},
        )
        assert resp.status == 400

    async def test_invalid_token_format(self, auth_client):
        resp = await auth_client.get(
            '/api/auth/check',
            headers={'Authorization': 'Bearer invalid_token_xyz'},
        )
        assert resp.status == 401

    async def test_health_endpoint_no_auth_required(self, auth_client):
        resp = await auth_client.get('/health')
        assert resp.status == 200
        data = await resp.json()
        assert data['status'] == 'ok'
