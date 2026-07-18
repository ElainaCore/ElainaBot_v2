"""API 测试: 日志模块 (logs/recent, logs/login, logs/{log_type}, unban, delete-ip)"""

import pytest

from tests.helpers import assert_success_response


class TestLogsRecent:
    """最近日志接口测试"""

    async def test_recent_logs(self, api_client, auth_headers):
        resp = await api_client.get('/api/logs/recent', headers=auth_headers)
        assert resp.status == 200
        data = await resp.json()
        # 返回 dict 包含 message/framework/error/lifecycle 键
        assert 'message' in data
        assert 'framework' in data
        assert 'error' in data
        assert 'lifecycle' in data
        assert 'console' in data

    async def test_recent_logs_with_appid(self, api_client, auth_headers):
        resp = await api_client.get('/api/logs/recent?appid=123456', headers=auth_headers)
        assert resp.status == 200

    async def test_recent_logs_no_auth(self, api_client):
        resp = await api_client.get('/api/logs/recent')
        assert resp.status == 401


class TestLogsType:
    """按类型查询日志"""

    @pytest.mark.parametrize('log_type', ['message', 'framework', 'error', 'lifecycle'])
    async def test_get_logs_by_type(self, api_client, auth_headers, log_type):
        resp = await api_client.get(f'/api/logs/{log_type}', headers=auth_headers)
        assert resp.status == 200
        data = await resp.json()
        assert 'logs' in data
        assert 'total' in data
        assert 'page' in data

    async def test_get_logs_invalid_type(self, api_client, auth_headers):
        resp = await api_client.get('/api/logs/invalid_type', headers=auth_headers)
        assert resp.status == 400

    async def test_get_logs_pagination(self, api_client, auth_headers):
        resp = await api_client.get('/api/logs/message?page=2&size=10', headers=auth_headers)
        assert resp.status == 200
        data = await resp.json()
        assert data.get('page') == 2
        assert data.get('page_size') == 10


class TestLoginLogs:
    """登录日志接口测试"""

    async def test_get_login_logs(self, api_client, auth_headers):
        resp = await api_client.get('/api/logs/login', headers=auth_headers)
        assert resp.status == 200
        data = await resp.json()
        assert_success_response(data)
        assert 'data' in data
        assert 'stats' in data


class TestLogOperations:
    """日志操作接口测试"""

    async def test_unban_ip_missing_param(self, api_client, auth_headers):
        resp = await api_client.post('/api/logs/unban', json={}, headers=auth_headers)
        assert resp.status in (400, 404)

    async def test_delete_ip_missing_param(self, api_client, auth_headers):
        resp = await api_client.post('/api/logs/delete-ip', json={}, headers=auth_headers)
        assert resp.status in (400, 404)
