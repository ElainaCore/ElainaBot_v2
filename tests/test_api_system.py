"""API 测试: 系统信息模块 (system/info)"""



class TestSystemInfo:
    """系统信息接口测试"""

    async def test_system_info(self, api_client, auth_headers):
        resp = await api_client.get('/api/system/info', headers=auth_headers)
        assert resp.status == 200
        data = await resp.json()
        assert 'cpu_percent' in data
        assert 'memory_percent' in data
        assert 'uptime' in data
        assert 'system_version' in data

    async def test_system_info_fields(self, api_client, auth_headers):
        """验证所有期望字段存在"""
        resp = await api_client.get('/api/system/info', headers=auth_headers)
        data = await resp.json()

        expected_fields = [
            'cpu_percent',
            'framework_cpu_percent',
            'cpu_cores',
            'cpu_model',
            'memory_percent',
            'memory_used',
            'memory_total',
            'framework_memory_percent',
            'framework_memory_total',
            'disk_info',
            'uptime',
            'system_uptime',
            'start_time',
            'system_version',
            'plugins_count',
            'bots_count',
        ]
        for field in expected_fields:
            assert field in data, f'Missing field: {field}'

    async def test_system_info_disk_fields(self, api_client, auth_headers):
        resp = await api_client.get('/api/system/info', headers=auth_headers)
        data = await resp.json()
        disk = data.get('disk_info', {})
        assert 'total' in disk
        assert 'used' in disk
        assert 'free' in disk
        assert 'percent' in disk

    async def test_system_info_no_auth(self, api_client):
        resp = await api_client.get('/api/system/info')
        assert resp.status == 401
