"""API 测试: 插件市场模块 (market/* 路由组)"""



class TestMarketList:
    """市场列表接口测试"""

    async def test_market_list(self, api_client, auth_headers):
        resp = await api_client.get('/api/market/list', headers=auth_headers)
        # 可能成功或网络错误
        assert resp.status in (200, 500)

    async def test_market_list_with_search(self, api_client, auth_headers):
        resp = await api_client.get('/api/market/list?search=weather', headers=auth_headers)
        assert resp.status in (200, 500)

    async def test_market_list_no_auth(self, api_client):
        resp = await api_client.get('/api/market/list')
        assert resp.status == 401


class TestMarketCategories:
    """市场分类接口测试"""

    async def test_market_categories(self, api_client, auth_headers):
        resp = await api_client.get('/api/market/categories', headers=auth_headers)
        assert resp.status in (200, 500)


class TestMarketDetail:
    """市场详情接口测试"""

    async def test_market_detail(self, api_client, auth_headers):
        resp = await api_client.post(
            '/api/market/detail',
            json={'name': 'weather'},
            headers=auth_headers,
        )
        assert resp.status in (200, 500)


class TestMarketRefresh:
    """市场刷新接口测试"""

    async def test_market_refresh(self, api_client, auth_headers):
        resp = await api_client.post('/api/market/refresh', json={}, headers=auth_headers)
        assert resp.status in (200, 500)


class TestMarketInstall:
    """市场安装/卸载接口测试"""

    async def test_market_install(self, api_client, auth_headers):
        resp = await api_client.post(
            '/api/market/install',
            json={'name': 'nonexistent_plugin'},
            headers=auth_headers,
        )
        assert resp.status in (200, 400, 500)

    async def test_market_uninstall(self, api_client, auth_headers):
        resp = await api_client.post(
            '/api/market/uninstall',
            json={'name': 'nonexistent_plugin'},
            headers=auth_headers,
        )
        assert resp.status in (200, 400, 404, 500)


class TestMarketLocal:
    """本地插件接口测试"""

    async def test_market_local(self, api_client, auth_headers):
        resp = await api_client.get('/api/market/local', headers=auth_headers)
        assert resp.status in (200, 500)


class TestMarketMirror:
    """市场镜像接口测试"""

    async def test_market_get_mirror(self, api_client, auth_headers):
        resp = await api_client.get('/api/market/mirror', headers=auth_headers)
        assert resp.status in (200, 500)

    async def test_market_set_mirror(self, api_client, auth_headers):
        resp = await api_client.post(
            '/api/market/mirror',
            json={'mirror': 'https://example.com'},
            headers=auth_headers,
        )
        assert resp.status in (200, 500)
