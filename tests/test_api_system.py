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


class TestSystemDependencies:
    """依赖版本接口测试"""

    async def test_dependencies(self, api_client, auth_headers):
        resp = await api_client.get('/api/system/dependencies', headers=auth_headers)
        assert resp.status == 200
        data = await resp.json()
        assert 'python' in data
        assert 'dependencies' in data
        assert data['python'].get('version')
        assert data['python'].get('status') in ('ok', 'low', 'high')
        for dep in data['dependencies']:
            assert dep.get('name')
            assert dep.get('status') in ('ok', 'low', 'high', 'missing')

    async def test_dependencies_no_auth(self, api_client):
        resp = await api_client.get('/api/system/dependencies')
        assert resp.status == 401


class TestCheckSpecifiers:
    """版本范围比较"""

    def test_ranges(self):
        from web.tools._stats.dependencies import check_specifiers

        assert check_specifiers('6.0.3', '>=6.0.3,<7.0') == 'ok'
        assert check_specifiers('5.9', '>=6.0.3,<7.0') == 'low'
        assert check_specifiers('7.1', '>=6.0.3,<7.0') == 'high'
        assert check_specifiers('3.10.2', '>=3.11') == 'low'
        assert check_specifiers('3.12.3', '>=3.11') == 'ok'
        assert check_specifiers('1.0.0', '') == 'ok'
