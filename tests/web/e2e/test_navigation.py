"""E2E 测试: SPA 路由导航"""


class TestNavigation:
    """SPA 导航测试"""

    async def test_root_redirects_to_web(self, e2e_client):
        """面板根路径返回可挂载的 SPA 页面。"""
        resp = await e2e_client.get('/web/')
        assert resp.status == 200
        ct = resp.headers.get('Content-Type', '')
        assert 'text/html' in ct
        html = await resp.text()
        assert '<div id="app">' in html
        assert 'Elaina' in html

    async def test_invalid_slash_web_redirects(self, e2e_client):
        """/web (不带斜杠) 应重定向"""
        resp = await e2e_client.get('/web', allow_redirects=False)
        assert resp.status == 302
        assert resp.headers['Location'] == '/web/'

    async def test_nonexistent_route_returns_spa(self, e2e_client):
        """不存在的路由应 fallback 到 SPA index.html"""
        resp = await e2e_client.get('/web/nonexistent_page_xyz')
        assert resp.status == 200
        html = await resp.text()
        assert '<div id="app">' in html

    async def test_api_unauthenticated(self, e2e_client):
        """未认证 API 请求应返回 401 而非崩溃"""
        resp = await e2e_client.get('/api/config')
        assert resp.status == 401
        ct = resp.headers.get('Content-Type', '')
        assert 'application/json' in ct

    async def test_static_index_served(self, e2e_client):
        resp = await e2e_client.get('/web/index.html')
        assert resp.status == 200
        assert 'text/html' in resp.headers.get('Content-Type', '')

    async def test_static_file_security(self, e2e_client):
        """静态文件路径遍历防护"""
        resp = await e2e_client.get('/web/..%2F..%2Fetc%2Fpasswd')
        assert resp.status == 403
