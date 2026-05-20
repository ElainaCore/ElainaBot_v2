"""E2E 测试: 所有 SPA 页面加载验证"""

import pytest


# 所有 SPA 路由 (Hash router)
SPA_ROUTES = [
    '/web/',
    '/web/login',
    '/web/dashboard',
    '/web/bots',
    '/web/logs',
    '/web/plugins',
    '/web/config',
    '/web/messages',
    '/web/statistics',
    '/web/market',
    '/web/openapi',
    '/web/database',
    '/web/system',
]


@pytest.mark.parametrize('route', SPA_ROUTES)
async def test_spa_page_loads(e2e_client, route):
    """所有 SPA 路由应返回 index.html (hash router SPA fallback)"""
    resp = await e2e_client.get(route)
    assert resp.status == 200, f'{route}: expected 200, got {resp.status}'
    html = await resp.text()
    assert '<div id="app">' in html, f'{route}: missing Vue mount point'
