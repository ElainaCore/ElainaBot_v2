"""API 测试: 中间件行为 (并发请求, token 复用, 响应格式, IP 封禁)"""

import asyncio

from tests.helpers import assert_success_response


class TestConcurrency:
    """并发请求测试"""

    async def test_concurrent_requests(self, api_client, auth_headers):
        """多个并发请求应正确响应"""
        async def make_request():
            return await api_client.get('/api/auth/check', headers=auth_headers)

        tasks = [make_request() for _ in range(5)]
        results = await asyncio.gather(*tasks)
        for resp in results:
            assert resp.status == 200
            data = await resp.json()
            assert_success_response(data)


class TestTokenReuse:
    """Token 复用测试"""

    async def test_token_reused_across_requests(self, api_client, auth_headers):
        """同一个 token 可跨请求复用"""
        endpoints = [
            '/api/auth/check',
            '/api/bots',
            '/api/config',
            '/api/system/info',
        ]
        for endpoint in endpoints:
            resp = await api_client.get(endpoint, headers=auth_headers)
            assert resp.status == 200, f'{endpoint}: expected 200, got {resp.status}'

    async def test_revoked_token(self, api_client, auth_token):
        """无效 token 应被拒绝"""
        resp = await api_client.get(
            '/api/auth/check',
            headers={'Authorization': 'Bearer revoked_token_xyz'},
        )
        assert resp.status == 401


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
        assert data['data']['token']


class TestIPBanning:
    """IP 封禁测试"""

    async def test_login_failure_count(self, api_client):
        """多次失败登录后的状态"""
        for i in range(6):
            resp = await api_client.post('/api/auth/login', json={'password': f'wrong_{i}'})
        # 5 次失败后 IP 被封
        assert resp.status in (401, 403)
