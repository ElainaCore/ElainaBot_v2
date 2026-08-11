"""API 测试: 鉴权模块 (auth/login, auth/check, auth/password-status)"""

from tests.helpers import assert_200, assert_401, assert_error_response, assert_success_response, do_login


class TestAuthLogin:
    """登录接口测试"""

    async def test_login_success(self, api_client):
        from web.auth import SESSION_COOKIE

        resp, data = await do_login(api_client, 'test_pass')
        assert_200(resp)
        assert_success_response(data)
        assert data['data'] == {'is_weak': False}
        assert SESSION_COOKIE not in data['data']

        cookie = resp.cookies[SESSION_COOKIE]
        assert cookie.value
        assert cookie['httponly'] is True
        assert cookie['samesite'] == 'Strict'
        assert cookie['path'] == '/'

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
        from core.base.config import cfg

        cfg.set_value('settings', 'web.admin_password', '123456')
        resp, data = await do_login(api_client, '123456')
        assert_200(resp)
        assert data['data']['is_weak'] is True

    async def test_login_cookie_reuse(self, api_client):
        """登录 Cookie 可重复用于认证检查。"""
        from web.auth import SESSION_COOKIE

        resp, _ = await do_login(api_client)
        cookies = {SESSION_COOKIE: resp.cookies[SESSION_COOKIE].value}
        api_client.session.cookie_jar.clear()
        check_resp = await api_client.get('/api/auth/check', cookies=cookies)
        assert check_resp.status == 200

    async def test_login_rotates_session_cookie(self, api_client):
        """每次登录都签发新的会话 Cookie。"""
        from web.auth import SESSION_COOKIE

        sessions = set()
        for _ in range(3):
            resp, _ = await do_login(api_client)
            assert_200(resp)
            sessions.add(resp.cookies[SESSION_COOKIE].value)

        assert len(sessions) == 3

    async def test_bearer_header_is_not_accepted(self, api_client):
        """旧 Bearer 鉴权不再被接受。"""
        resp = await api_client.get(
            '/api/auth/check',
            headers={'Authorization': 'Bearer legacy_token'},
        )
        assert resp.status == 401


class TestAuthCheck:
    """认证检查接口测试"""

    async def test_check_with_valid_cookie(self, api_client, auth_cookies):
        resp = await api_client.get('/api/auth/check', cookies=auth_cookies)
        assert_200(resp)
        data = await resp.json()
        assert_success_response(data)

    async def test_check_without_cookie(self, api_client):
        resp = await api_client.get('/api/auth/check')
        assert_401(resp)

    async def test_logout_revokes_session(self, api_client, auth_cookies):
        from web.auth import SESSION_COOKIE

        logout_resp = await api_client.post('/api/auth/logout', cookies=auth_cookies)
        assert_200(logout_resp)
        assert logout_resp.cookies[SESSION_COOKIE]['max-age'] == '0'

        check_resp = await api_client.get('/api/auth/check', cookies=auth_cookies)
        assert_401(check_resp)


class TestPasswordStatus:
    """密码状态接口测试"""

    async def test_password_status(self, api_client, auth_cookies):
        resp = await api_client.get('/api/auth/password-status', cookies=auth_cookies)
        assert_200(resp)
        data = await resp.json()
        assert_success_response(data)
        assert 'is_default' in data['data']

    async def test_password_status_no_auth(self, api_client):
        """未认证时返回 401"""
        resp = await api_client.get('/api/auth/password-status')
        assert_401(resp)
