"""API 测试: OpenAPI 模块 (openapi/* 路由组)"""

import pytest

from tests.helpers import assert_success_response
from web.tools._openapi import handler as openapi_handler
from web.tools._openapi.handler import _openapi_v2_data


class TestOpenapiAuth:
    """OpenAPI 鉴权测试 — 所有端点均需认证"""

    # 所有 openapi 端点列表
    ENDPOINTS = [
        '/api/openapi/start-login',
        '/api/openapi/check-login',
        '/api/openapi/login-status',
        '/api/openapi/verify-login',
        '/api/openapi/logout',
        '/api/openapi/botlist',
        '/api/openapi/botdata',
        '/api/openapi/notifications',
        '/api/openapi/whitelist',
        '/api/openapi/whitelist/update',
        '/api/openapi/whitelist/delete-qr',
        '/api/openapi/whitelist/check-delete-auth',
        '/api/openapi/whitelist/execute-delete',
        '/api/openapi/whitelist/batch-add',
    ]

    @pytest.mark.parametrize('endpoint', ENDPOINTS)
    async def test_openapi_endpoints_require_auth(self, api_client, endpoint):
        """所有 openapi 端点未认证时应返回 401"""
        resp = await api_client.post(endpoint, json={})
        assert resp.status == 401, f'{endpoint}: expected 401, got {resp.status}'


class TestOpenapiLogin:
    """OpenAPI 登录流程测试"""

    async def test_start_login_no_bot_api(self, api_client, auth_headers):
        resp = await api_client.post(
            '/api/openapi/start-login',
            json={'user_id': 'test_user'},
            headers=auth_headers,
        )
        assert resp.status in (200, 500)

    async def test_check_login_not_started(self, api_client, auth_headers):
        resp = await api_client.post(
            '/api/openapi/check-login',
            json={'user_id': 'nonexistent_user'},
            headers=auth_headers,
        )
        assert resp.status == 200
        data = await resp.json()
        # status 可能是 not_started 或 success
        assert 'status' in data or 'success' in data

    async def test_login_status(self, api_client, auth_headers):
        resp = await api_client.post(
            '/api/openapi/login-status',
            json={'user_id': 'web_user'},
            headers=auth_headers,
        )
        assert resp.status == 200
        data = await resp.json()
        assert_success_response(data)
        assert 'logged_in' in data

    async def test_logout(self, api_client, auth_headers):
        resp = await api_client.post(
            '/api/openapi/logout',
            json={'user_id': 'test_user'},
            headers=auth_headers,
        )
        assert resp.status == 200
        data = await resp.json()
        assert_success_response(data)


class TestOpenapiV2Auth:
    async def test_manual_cookie_route_is_removed(self, api_client, auth_headers):
        resp = await api_client.post(
            '/api/openapi/v2/set-creds',
            json={'user_id': 'test_user', 'cookie': 'b-token=not-used'},
            headers=auth_headers,
        )
        assert resp.status == 404

    async def test_proxy_only_suggests_qr_login(self, api_client, auth_headers, monkeypatch):
        user_id = 'qr_only_user'
        monkeypatch.setitem(_openapi_v2_data, user_id, {'type': 'ok'})
        resp = await api_client.post(
            '/api/openapi/v2/proxy',
            json={
                'user_id': user_id,
                'path': '/cgi-bin/v2/info/list_bots',
                'payload': {},
            },
            headers=auth_headers,
        )
        assert resp.status == 200
        data = await resp.json()
        assert data['success'] is False
        assert data['message'] == '新版开放平台未授权，请重新扫码登录'
        assert 'Cookie' not in data['message']

    async def test_proxy_returns_qq_response_without_wrapper(self, api_client, auth_headers, monkeypatch):
        class FakeBotApi:
            async def v2_request(self, method, path, cookie='', skey='', data=None, params=None):
                assert skey == '@abc-123*'
                assert 'skey=@abc-123*' in cookie
                return {
                    'res': {'ret': 0},
                    'data': {'bots': [{'bot_appid': 102905988}]},
                }

        user_id = 'passthrough_user'
        monkeypatch.setitem(
            _openapi_v2_data,
            user_id,
            {
                'b_token': 'token',
                'qticket_lite': 'ticket',
                'developer_id_lite': 'developer-1',
                'uin': '10001',
                'skey': '@abc-123*',
            },
        )
        monkeypatch.setattr(openapi_handler, '_bot_api', FakeBotApi())

        resp = await api_client.post(
            '/api/openapi/v2/proxy',
            json={
                'user_id': user_id,
                'path': '/cgi-bin/v2/info/list_bots',
                'payload': {},
            },
            headers=auth_headers,
        )

        assert resp.status == 200
        assert await resp.json() == {
            'res': {'ret': 0},
            'data': {'bots': [{'bot_appid': 102905988}]},
        }

    async def test_proxy_clears_expired_qq_login(self, api_client, auth_headers, monkeypatch):
        class FakeBotApi:
            async def v2_request(self, method, path, cookie='', skey='', data=None, params=None):
                return {'retcode': 10004, 'msg': '请重新登录'}

        user_id = 'expired_user'
        monkeypatch.setitem(
            _openapi_v2_data,
            user_id,
            {
                'b_token': 'token',
                'qticket_lite': 'ticket',
                'developer_id_lite': 'developer-1',
                'uin': '10001',
                'skey': '@abc-123*',
            },
        )
        monkeypatch.setattr(openapi_handler, '_bot_api', FakeBotApi())
        monkeypatch.setattr(openapi_handler, '_v2_dir', '')

        resp = await api_client.post(
            '/api/openapi/v2/proxy',
            json={
                'user_id': user_id,
                'path': '/cgi-bin/v2/info/list_bots',
                'payload': {},
            },
            headers=auth_headers,
        )

        assert resp.status == 200
        assert await resp.json() == {
            'success': False,
            'message': '请重新登录',
            'relogin': True,
        }
        assert user_id not in _openapi_v2_data


class TestOpenapiWhitelist:
    """OpenAPI 白名单操作测试"""

    async def test_get_whitelist_no_login(self, api_client, auth_headers):
        resp = await api_client.post(
            '/api/openapi/whitelist',
            json={},
            headers=auth_headers,
        )
        # 无登录信息时返回错误
        assert resp.status in (200, 400, 401)

    async def test_update_whitelist_no_login(self, api_client, auth_headers):
        resp = await api_client.post(
            '/api/openapi/whitelist/update',
            json={},
            headers=auth_headers,
        )
        assert resp.status in (200, 400, 401)

    async def test_batch_add_whitelist_no_login(self, api_client, auth_headers):
        resp = await api_client.post(
            '/api/openapi/whitelist/batch-add',
            json={},
            headers=auth_headers,
        )
        assert resp.status in (200, 400, 401)
