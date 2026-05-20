"""E2E 测试: Dashboard 页面"""



class TestDashboardPage:
    """Dashboard 页面 E2E 测试"""

    async def test_dashboard_with_token(self, e2e_client, e2e_token):
        """带 token 参数访问应加载 dashboard"""
        resp = await e2e_client.get(f'/web/?token={e2e_token}')
        assert resp.status == 200
        html = await resp.text()
        assert '<div id="app">' in html

    async def test_dashboard_spa_root(self, e2e_client):
        """SPA 根路径返回 index.html"""
        resp = await e2e_client.get('/web/')
        assert resp.status == 200
        assert 'text/html' in resp.headers.get('Content-Type', '')

    async def test_dashboard_vue_mount_point(self, e2e_client):
        """Vue mount point 存在"""
        resp = await e2e_client.get('/web/')
        html = await resp.text()
        assert 'id="app"' in html or "id='app'" in html
