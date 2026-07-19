"""API 测试: 鉴权模块 (auth/login, auth/check, auth/password-status)"""

from tests.helpers import assert_200, assert_401, assert_error_response, assert_success_response, do_login


class TestAuthLogin:
    """登录接口测试"""

    async def test_login_success(self, api_client):
        resp, data = await do_login(api_client, 'test_pass')
        assert_200(resp)
        assert_success_response(data)
        assert data['data']['token']

    async def test_login_wrong_password(self, api_client):
        resp, data = await do_login(api_client, 'wrong_pass')
        assert resp.status == 401
        assert_error_response(data)
        assert 'remaining' in data['data']

    async def test_login_empty_password(self, api_client):
        resp = await api_client.post('/api/auth/login', json={'password': ''})
        data = await resp.json()
        assert resp.status in (400, 401)
        assert_error_response(data)

    async def test_login_missing_body(self, api_client):
        resp = await api_client.post('/api/auth/login')
        data = await resp.json()
        assert resp.status == 400
        assert_error_response(data)

    async def test_login_weak_password_detection(self, api_client):
        """弱密码应被检测"""
        resp, data = await do_login(api_client, '123456')
        if resp.status == 200:
            assert data['data']['is_weak'] is True

    async def test_login_token_reuse(self, api_client):
        """Token 可重复用于认证检查"""
        resp, data = await do_login(api_client)
        token = data['data']['token']
        check_resp = await api_client.get(
            '/api/auth/check',
            headers={'Authorization': f'Bearer {token}'},
        )
        assert check_resp.status == 200

    async def test_login_invalid_token(self, api_client):
        """无效 token 应返回 401"""
        resp = await api_client.get(
            '/api/auth/check',
            headers={'Authorization': 'Bearer invalid_token_xyz'},
        )
        assert resp.status == 401


class TestAuthCheck:
    """认证检查接口测试"""

    async def test_check_with_valid_token(self, api_client, auth_headers):
        resp = await api_client.get('/api/auth/check', headers=auth_headers)
        assert_200(resp)
        data = await resp.json()
        assert_success_response(data)

    async def test_check_without_token(self, api_client):
        resp = await api_client.get('/api/auth/check')
        assert_401(resp)


class TestPasswordStatus:
    """密码状态接口测试"""

    async def test_password_status(self, api_client, auth_headers):
        resp = await api_client.get('/api/auth/password-status', headers=auth_headers)
        assert_200(resp)
        data = await resp.json()
        assert_success_response(data)
        assert 'is_default' in data['data']

    async def test_password_status_no_auth(self, api_client):
        """未认证时返回 401"""
        resp = await api_client.get('/api/auth/password-status')
        assert_401(resp)
