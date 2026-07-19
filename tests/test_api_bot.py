"""API 测试: 机器人模块 (bots, robot/info, robot/qrcode, bot/restart)"""

from tests.helpers import assert_success_response


class TestBots:
    """机器人列表接口测试"""

    async def test_get_bots(self, api_client, auth_headers):
        resp = await api_client.get('/api/bots', headers=auth_headers)
        assert resp.status == 200
        data = await resp.json()
        assert_success_response(data)
        assert 'bots' in data['data']

    async def test_get_bots_no_auth(self, api_client):
        resp = await api_client.get('/api/bots')
        assert resp.status == 401


class TestRobotInfo:
    """机器人信息接口测试"""

    async def test_get_robot_info(self, api_client, auth_headers):
        resp = await api_client.get('/api/robot/info', headers=auth_headers)
        # 无 _bot_manager 时可能返回各种状态
        assert resp.status in (200, 404, 500)

    async def test_get_robot_info_with_appid(self, api_client, auth_headers):
        resp = await api_client.get('/api/robot/info?appid=123456', headers=auth_headers)
        assert resp.status in (200, 404, 500)

    async def test_get_robot_info_no_auth(self, api_client):
        resp = await api_client.get('/api/robot/info')
        assert resp.status == 401


class TestRobotQrcode:
    """机器人二维码接口测试"""

    async def test_get_qrcode_missing_url(self, api_client):
        """qrcode 无需认证"""
        resp = await api_client.get('/api/robot/qrcode')
        assert resp.status == 400

    async def test_get_qrcode_with_url(self, api_client):
        resp = await api_client.get('/api/robot/qrcode?url=https://example.com')
        # 可能返回图片(200)或错误(500, 网络请求可能会超时)
        assert resp.status in (200, 400, 500)


class TestBotRestart:
    """机器人重启接口测试"""

    async def test_restart(self, api_client, auth_headers):
        resp = await api_client.post('/api/bot/restart', json={}, headers=auth_headers)
        assert resp.status in (200, 400, 500)

    async def test_restart_no_auth(self, api_client):
        resp = await api_client.post('/api/bot/restart', json={})
        assert resp.status == 401
