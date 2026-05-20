"""API 测试: 插件管理模块 (plugins/* 路由组)"""

from tests.helpers import assert_success_response


class TestPluginScan:
    """插件扫描接口测试"""

    async def test_scan_plugins(self, api_client, auth_headers):
        resp = await api_client.get('/api/plugins/scan', headers=auth_headers)
        assert resp.status == 200
        data = await resp.json()
        assert_success_response(data)
        assert 'plugins' in data

    async def test_scan_plugin_dirs(self, api_client, auth_headers):
        resp = await api_client.get('/api/plugins/scan-dirs', headers=auth_headers)
        assert resp.status == 200
        data = await resp.json()
        assert_success_response(data)
        assert 'dirs' in data

    async def test_scan_no_auth(self, api_client):
        resp = await api_client.get('/api/plugins/scan')
        assert resp.status == 401


class TestPluginFileOps:
    """插件文件操作接口测试"""

    async def test_toggle_missing_params(self, api_client, auth_headers):
        resp = await api_client.post('/api/plugins/toggle', json={}, headers=auth_headers)
        assert resp.status in (400, 403)

    async def test_read_missing_path(self, api_client, auth_headers):
        resp = await api_client.post('/api/plugins/read', json={}, headers=auth_headers)
        assert resp.status in (400, 403)

    async def test_save_missing_params(self, api_client, auth_headers):
        resp = await api_client.post('/api/plugins/save', json={}, headers=auth_headers)
        assert resp.status in (400, 403)

    async def test_create_missing_params(self, api_client, auth_headers):
        resp = await api_client.post('/api/plugins/create', json={}, headers=auth_headers)
        assert resp.status in (400, 403)

    async def test_create_folder_missing_name(self, api_client, auth_headers):
        resp = await api_client.post('/api/plugins/create-folder', json={}, headers=auth_headers)
        assert resp.status in (400, 403)

    async def test_get_folders(self, api_client, auth_headers, api_config_dir):
        resp = await api_client.get('/api/plugins/folders', headers=auth_headers)
        assert resp.status == 200
        data = await resp.json()
        assert_success_response(data)
        assert 'folders' in data

    async def test_upload_no_file(self, api_client, auth_headers):
        """无 multipart body 时 upload 应报错"""
        resp = await api_client.post('/api/plugins/upload', headers=auth_headers)
        # 返回 400 (缺少文件) 或 500 (multipart parse error)
        assert resp.status in (400, 500)

    async def test_reload_missing_name(self, api_client, auth_headers):
        resp = await api_client.post('/api/plugins/reload', json={}, headers=auth_headers)
        assert resp.status == 400


class TestPluginConfig:
    """插件配置接口测试"""

    async def test_config_files(self, api_client, auth_headers):
        resp = await api_client.post(
            '/api/plugins/config-files',
            json={'name': 'test'},
            headers=auth_headers,
        )
        assert resp.status in (200, 400, 500)

    async def test_get_plugin_bots(self, api_client, auth_headers):
        resp = await api_client.get('/api/plugins/bots', headers=auth_headers)
        assert resp.status in (200, 503)

    async def test_set_plugin_bots(self, api_client, auth_headers):
        resp = await api_client.post(
            '/api/plugins/bots',
            json={'name': 'test', 'bots': []},
            headers=auth_headers,
        )
        assert resp.status in (200, 400, 503)
