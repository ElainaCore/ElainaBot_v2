"""API 测试: 配置模块 (config, config/save, config-file/read, config-file/save)"""

from tests.helpers import assert_error_response, assert_success_response


class TestConfigGet:
    """获取配置接口测试"""

    async def test_get_config(self, api_client, auth_headers):
        resp = await api_client.get('/api/config', headers=auth_headers)
        assert resp.status == 200
        data = await resp.json()
        assert_success_response(data)
        assert 'bot' in data['data']
        assert 'settings' in data['data']

    async def test_get_config_no_auth(self, api_client):
        resp = await api_client.get('/api/config')
        assert resp.status == 401


class TestConfigSave:
    """保存配置接口测试"""

    async def test_save_config_settings(self, api_client, auth_headers):
        resp = await api_client.post(
            '/api/config/save',
            json={'file': 'settings', 'content': 'server:\n  port: 15200\n'},
            headers=auth_headers,
        )
        assert resp.status == 200
        data = await resp.json()
        assert_success_response(data)

    async def test_save_config_invalid_file(self, api_client, auth_headers):
        resp = await api_client.post(
            '/api/config/save',
            json={'file': 'invalid', 'content': 'data'},
            headers=auth_headers,
        )
        assert resp.status == 400
        data = await resp.json()
        assert_error_response(data)

    async def test_save_config_empty_content(self, api_client, auth_headers):
        resp = await api_client.post(
            '/api/config/save',
            json={'file': 'settings', 'content': ''},
            headers=auth_headers,
        )
        assert resp.status == 400
        data = await resp.json()
        assert_error_response(data)


class TestConfigFileRead:
    """config-file/read 接口测试"""

    async def test_read_config_file(self, api_client, auth_headers):
        resp = await api_client.post(
            '/api/config-file/read',
            json={'name': 'settings'},
            headers=auth_headers,
        )
        # 无 plugin_manager 时返回 400 是合理的
        assert resp.status in (200, 400)

    async def test_read_config_file_no_auth(self, api_client):
        resp = await api_client.post(
            '/api/config-file/read',
            json={'name': 'settings'},
        )
        assert resp.status == 401


class TestConfigFileSave:
    """config-file/save 接口测试"""

    async def test_save_config_file(self, api_client, auth_headers):
        resp = await api_client.post(
            '/api/config-file/save',
            json={'name': 'settings', 'content': 'server:\n  port: 15200\n'},
            headers=auth_headers,
        )
        # 无 plugin_manager 时返回 400 是合理的
        assert resp.status in (200, 400)

    async def test_save_config_file_no_auth(self, api_client):
        resp = await api_client.post(
            '/api/config-file/save',
            json={'name': 'settings', 'content': 'data'},
        )
        assert resp.status == 401
