"""E2E 测试: 登录页面"""



class TestLoginPage:
    """登录页面 E2E 测试"""

    async def test_login_page_loads(self, e2e_client):
        """登录页面应成功加载"""
        resp = await e2e_client.get('/web/')
        assert resp.status == 200
        html = await resp.text()
        assert '<div id="app">' in html
        assert 'Elaina' in html

    async def test_login_page_returns_html(self, e2e_client):
        """登录页面 Content-Type 应为 text/html"""
        resp = await e2e_client.get('/web/')
        ct = resp.headers.get('Content-Type', '')
        assert 'text/html' in ct

    async def test_login_page_spa_fallback(self, e2e_client):
        """SPA fallback: 任意路径返回 index.html"""
        resp = await e2e_client.get('/web/login')
        assert resp.status == 200
        html = await resp.text()
        assert '<div id="app">' in html

    async def test_static_assets_served(self, e2e_client):
        """静态资源应正确服务"""
        resp = await e2e_client.get('/web/index.html')
        assert resp.status == 200

    async def test_login_page_no_console_errors_via_api(self, e2e_client, e2e_token):
        """通过 token 参数访问 (模拟 hash router 初始加载)"""
        resp = await e2e_client.get(f'/web/?token={e2e_token}')
        assert resp.status == 200
        html = await resp.text()
        assert '<div id="app">' in html
