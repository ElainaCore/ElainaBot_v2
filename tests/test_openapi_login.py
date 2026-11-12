import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from web.tools._bot import scan_login
from web.tools._openapi import handler


class MobileLoginTests(IsolatedAsyncioTestCase):
    async def test_mobile_callback_saves_login_without_ptlogin_keys(self):
        async def create_session(request):
            return web.json_response({'retcode': 0, 'data': {'session_id': 'test-session'}})

        async def poll(request):
            self.assertEqual(request.query['session_id'], 'test-session')
            return web.json_response({'retcode': 0, 'data': {'code': 0, 'developer_id': 'test-developer', 'redirect': '/callback'}})

        async def callback(request):
            response = web.Response()
            # 手机端 BOpen 回调下发平台票据，不含 ptlogin 的 skey 和 p_skey。
            response.set_cookie('b-token', 'test-token')
            response.set_cookie('qticket', 'test-legacy-ticket')
            response.set_cookie('qticket_lite', 'test-lite-ticket')
            response.set_cookie('quin', '10000')
            return response

        qq = web.Application()
        qq.add_routes([web.get('/lite/create_session', create_session), web.get('/lite/poll', poll), web.get('/callback', callback)])
        panel = web.Application()
        panel.add_routes(
            [
                web.post('/start', handler.handle_v2_start_login),
                web.post('/check', handler.handle_v2_check_login),
                web.post('/status', handler.handle_v2_status),
            ]
        )
        with (
            TemporaryDirectory() as root,
            patch.multiple(handler, _data_file='', _v2_dir='', _bot_manager=None, _openapi_user_data={}, _openapi_v2_data={}, _openapi_v2_login_tasks={}),
        ):
            handler.set_context(root)
            async with TestServer(qq) as upstream, TestClient(TestServer(panel)) as client:
                base_url = str(upstream.make_url('/')).rstrip('/')
                with patch.object(scan_login, '_LITE_BASE', base_url):
                    started = await (await client.post('/start', json={'method': 'mobile'})).json()
                    self.assertTrue(started['success'], started)
                    checked = await (await client.post('/check', json={'login_id': started['login_id']})).json()
                    self.assertEqual(checked['status'], 'logged_in', checked)
                status = await (await client.post('/status', json={})).json()
                self.assertTrue(status['ready'], status)
                saved = json.loads((Path(root) / 'web' / 'new_open' / 'web_user.json').read_text(encoding='utf-8'))
                self.assertEqual(saved['qticket_lite'], 'test-lite-ticket')
                self.assertEqual(saved['developer_id_lite'], 'test-developer')
                self.assertNotIn('skey', saved)
                self.assertNotIn('p_skey', saved)
