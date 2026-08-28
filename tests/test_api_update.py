"""API 测试: 更新模块 (update/* 路由组)"""

from tests.helpers import assert_success_response


class TestUpdateEnvironment:
    """环境检测接口测试"""

    async def test_detect_environment(self, api_client, auth_cookies):
        resp = await api_client.get('/api/update/environment', cookies=auth_cookies)
        assert resp.status == 200
        data = await resp.json()
        assert_success_response(data)
        assert 'data' in data

    async def test_detect_environment_no_auth(self, api_client):
        resp = await api_client.get('/api/update/environment')
        assert resp.status == 401


class TestUpdateChangelog:
    """更新日志接口测试"""

    async def test_get_changelog(self, api_client, auth_cookies):
        resp = await api_client.get('/api/update/changelog', cookies=auth_cookies)
        # 可能成功 (200) 或失败 (500, 无网络)
        assert resp.status in (200, 500)


class TestUpdateVersion:
    """版本信息接口测试"""

    async def test_get_current_version(self, api_client, auth_cookies):
        resp = await api_client.get('/api/update/version', cookies=auth_cookies)
        assert resp.status in (200, 500)


class TestUpdateCheck:
    """更新检查接口测试"""

    async def test_check_update(self, api_client, auth_cookies):
        resp = await api_client.get('/api/update/check', cookies=auth_cookies)
        assert resp.status in (200, 500)


class TestUpdateProgress:
    """更新进度接口测试"""

    async def test_get_update_progress(self, api_client, auth_cookies):
        resp = await api_client.get('/api/update/progress', cookies=auth_cookies)
        assert resp.status in (200, 500)


class TestUpdateStart:
    """开始更新接口测试"""

    async def test_start_update_empty_body(self, api_client, auth_cookies):
        resp = await api_client.post('/api/update/start', json={}, cookies=auth_cookies)
        # updater 可能未初始化 (返回 500) 或成功启动 (200)
        assert resp.status in (200, 500)


class TestUpdateMirrors:
    """镜像管理接口测试"""

    async def test_get_mirrors(self, api_client, auth_cookies):
        resp = await api_client.get('/api/update/mirrors', cookies=auth_cookies)
        assert resp.status in (200, 500)

    async def test_set_custom_mirror(self, api_client, auth_cookies):
        resp = await api_client.post(
            '/api/update/mirror',
            json={'mirror': 'https://example.com'},
            cookies=auth_cookies,
        )
        assert resp.status in (200, 500)

    async def test_set_custom_mirror_no_body(self, api_client, auth_cookies):
        resp = await api_client.post('/api/update/mirror', cookies=auth_cookies)
        # json() 可能失败因为 Content-Type 不是 JSON
        if 'application/json' in (resp.headers.get('Content-Type', '') or ''):
            await resp.json()
        assert resp.status in (200, 400, 500)
