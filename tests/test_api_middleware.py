"""API 测试: 中间件行为 (并发请求, 会话复用, 响应格式, IP 封禁)。"""

import asyncio

from tests.helpers import assert_success_response


class TestConcurrency:
    """并发请求测试"""

    async def test_concurrent_requests(self, api_client, auth_cookies):
        """多个并发请求应正确响应"""

        async def make_request():
            return await api_client.get('/api/auth/check', cookies=auth_cookies)

        tasks = [make_request() for _ in range(5)]
        results = await asyncio.gather(*tasks)
        for resp in results:
            assert resp.status == 200
            data = await resp.json()
            assert_success_response(data)


class TestSessionReuse:
    """Cookie 会话复用测试。"""

    async def test_session_reused_across_requests(self, api_client, auth_cookies):
        """同一个会话可跨请求复用。"""
        endpoints = [
            '/api/auth/check',
            '/api/bots',
            '/api/config',
            '/api/system/info',
        ]
        for endpoint in endpoints:
            resp = await api_client.get(endpoint, cookies=auth_cookies)
            assert resp.status == 200, f'{endpoint}: expected 200, got {resp.status}'

    async def test_revoked_session(self, api_client, auth_cookies):
        """已撤销的会话 Cookie 应被拒绝。"""
        from web.auth import SESSION_COOKIE, valid_sessions

        valid_sessions.pop(auth_cookies[SESSION_COOKIE])
        resp = await api_client.get('/api/auth/check', cookies=auth_cookies)
        assert resp.status == 401

    async def test_cross_origin_write_is_rejected(self, api_client, auth_cookies):
        resp = await api_client.post(
            '/api/config/save',
            json={},
            cookies=auth_cookies,
            headers={'Origin': 'https://attacker.example'},
        )
        assert resp.status == 403


class TestResponseFormat:
    """响应格式测试"""

    async def test_json_response_content_type(self, api_client):
        """所有 API 响应应为 JSON"""
        resp = await api_client.post('/api/auth/login', json={'password': 'test_pass'})
        ct = resp.headers.get('Content-Type', '')
        assert 'application/json' in ct

    async def test_api_returns_json_on_auth_fail(self, api_client):
        """认证失败也应返回 JSON"""
        resp = await api_client.get('/api/config')
        assert resp.status == 401
        ct = resp.headers.get('Content-Type', '')
        assert 'application/json' in ct

    async def test_standard_response_envelope(self, api_client):
        """公共 API 响应只包含统一 envelope"""
        resp = await api_client.post('/api/auth/login', json={'password': 'test_pass'})
        assert resp.status == 200
        data = await resp.json()
        assert_success_response(data)
        assert set(data) == {'success', 'code', 'message', 'data'}
        assert data['code'] == 0
        assert data['data'] == {'is_weak': False}


class TestIPBanning:
    """IP 封禁测试"""

    async def test_login_failure_count(self, api_client):
        """多次失败登录后的状态"""
        for i in range(5):
            resp = await api_client.post('/api/auth/login', json={'password': f'wrong_{i}'})
        assert resp.status == 403

        resp = await api_client.post('/api/auth/login', json={'password': 'test_pass'})
        assert resp.status == 403
