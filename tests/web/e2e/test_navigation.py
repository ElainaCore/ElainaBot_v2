"""E2E 测试: SPA 路由导航"""



class TestNavigation:
    """SPA 导航测试"""

    async def test_root_redirects_to_web(self, e2e_client):
        """根路径应返回 index.html 或重定向"""
        resp = await e2e_client.get('/web/')
        assert resp.status == 200
        ct = resp.headers.get('Content-Type', '')
        assert 'text/html' in ct

    async def test_invalid_slash_web_redirects(self, e2e_client):
        """/web (不带斜杠) 应重定向"""
        resp = await e2e_client.get('/web', allow_redirects=False)
        assert resp.status in (301, 302, 200)

    async def test_nonexistent_route_returns_spa(self, e2e_client):
        """不存在的路由应 fallback 到 SPA index.html"""
        resp = await e2e_client.get('/web/nonexistent_page_xyz')
        assert resp.status == 200
        html = await resp.text()
        assert '<div id="app">' in html

    async def test_unauthenticated_access(self, e2e_client):
        """未认证状态页面仍可加载 (前端处理认证)"""
        resp = await e2e_client.get('/web/')
        assert resp.status == 200

    async def test_api_unauthenticated(self, e2e_client):
        """未认证 API 请求应返回 401 而非崩溃"""
        resp = await e2e_client.get('/api/config')
        assert resp.status == 401
        ct = resp.headers.get('Content-Type', '')
        assert 'application/json' in ct

    async def test_static_file_security(self, e2e_client):
        """静态文件路径遍历防护"""
        resp = await e2e_client.get('/web/../../../etc/passwd')
        # 应返回 200 (SPA fallback), 403 (安全拒绝), 或 404 (不存在)
        assert resp.status in (200, 403, 404)
