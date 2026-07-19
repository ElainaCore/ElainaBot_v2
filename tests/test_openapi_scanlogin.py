"""单元测试: 新版开放平台扫码登录解析/凭证提取 (web.tools._bot.api)"""

import asyncio
import json
import socket
from http.cookies import SimpleCookie

import aiohttp
from aiohttp import web
from yarl import URL

from web.tools._bot.api import (
    QQScanLogin,
    extract_login_creds,
    parse_login_developers,
    parse_ptlogin_versions,
    parse_ptuicb,
    qq_bkn,
)
from web.tools._openapi.handler import _V2_ALLOWED_PREFIXES, _v2_cookie, _v2_ready


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def read(self):
        if isinstance(self.payload, bytes):
            return self.payload
        if isinstance(self.payload, str):
            return self.payload.encode()
        return json.dumps(self.payload).encode()


class FakeSession:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(('GET', url, kwargs))
        return FakeResponse(next(self.responses))

    def post(self, url, **kwargs):
        self.calls.append(('POST', url, kwargs))
        return FakeResponse(next(self.responses))


class FakeClientContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_args):
        return None


class TestParsePtuicb:
    """解析 ptqrlogin 返回的 ptuiCB(...) 字符串"""

    def test_success(self):
        text = "ptuiCB('0','0','https://ptlogin2.qq.com/check_sig?a=1','1','登录成功!', 'nick')"
        r = parse_ptuicb(text)
        assert r['code'] == '0'
        assert r['url'].startswith('https://ptlogin2.qq.com/check_sig')
        assert r['msg'] == '登录成功!'

    def test_waiting(self):
        r = parse_ptuicb("ptuiCB('66','0','','0','二维码未失效', '')")
        assert r['code'] == '66'
        assert r['url'] == ''

    def test_scanned(self):
        r = parse_ptuicb("ptuiCB('67','0','','0','已扫码等待确认', '')")
        assert r['code'] == '67'

    def test_expired(self):
        r = parse_ptuicb("ptuiCB('65','0','','0','二维码已失效', '')")
        assert r['code'] == '65'

    def test_garbage(self):
        assert parse_ptuicb('not a ptuicb') == {'code': '', 'url': '', 'msg': ''}
        assert parse_ptuicb('') == {'code': '', 'url': '', 'msg': ''}


class TestParsePtloginVersions:
    def test_current_xlogin_versions(self):
        html = '''
            ptui_version:encodeURIComponent("26030415")
            var frags = "https://qq-web.cdn-go.cn/monorepo/b515fdc3".split('/');
        '''
        assert parse_ptlogin_versions(html) == ('26030415', 'b515fdc3')

    def test_missing_versions_use_known_compatible_defaults(self):
        assert parse_ptlogin_versions('') == ('26030415', 'b515fdc3')


class TestExtractLoginCreds:
    """从登录态 cookie 中提取凭证并做字段映射"""

    def test_full_mapping(self):
        cookies = {
            'b-token': 'BT',
            'qticket_lite': 'QL',
            'qticket': 'QT',
            'quid': 'DEV',
            'quin': 'o2218872014',
            'skey': '@abc-123*',
            'p_skey': 'P_SK',
        }
        out = extract_login_creds(cookies)
        assert out['b_token'] == 'BT'
        assert out['qticket_lite'] == 'QL'
        assert out['qticket'] == 'QT'
        assert out['developer_id_lite'] == 'DEV'
        assert out['developerId'] == 'DEV'
        assert out['skey'] == '@abc-123*'
        assert out['p_skey'] == 'P_SK'
        # uin 应剥离前导 'o'
        assert out['uin'] == '2218872014'

    def test_developer_id_fallbacks(self):
        out = extract_login_creds({'developer_id_lite': 'DEV2', 'uin': '123'})
        assert out['developer_id_lite'] == 'DEV2'
        assert out['developerId'] == 'DEV2'
        assert out['uin'] == '123'

    def test_empty(self):
        assert extract_login_creds({}) == {}


class TestParseLoginDevelopers:
    def test_current_grouped_response(self):
        out = parse_login_developers({
            'res': {'ret': 0},
            'developers': [{
                'type_id': 3,
                'items': [{
                    'id': 'developer-1',
                    'status': 1,
                    'name': '主体一',
                    'reg_mail': 'developer@example.com',
                    'type': 4,
                }],
            }],
        })
        assert out == [{
            'id': 'developer-1',
            'name': '主体一',
            'email': 'developer@example.com',
            'subject_type': 4,
            'status': 1,
            'verified': True,
        }]

    def test_legacy_response(self):
        out = parse_login_developers({
            'data': {
                'developer_infos': [{
                    'id': 'developer-2',
                    'subject_name': '主体二',
                    'subject_email': 'legacy@example.com',
                    'subject_type': 1,
                }],
                'unverified_developer_infos': [{
                    'developer_id': 'developer-3',
                    'subject_name': '主体三',
                }],
            },
        })
        assert [item['id'] for item in out] == ['developer-2', 'developer-3']
        assert [item['verified'] for item in out] == [True, False]


class TestQqBkn:
    def test_matches_qlib_skey_token(self):
        assert qq_bkn('') == 5381
        assert qq_bkn('@abc-123*') == 2016310520


class TestQQScanLogin:
    """会话对象初始状态与安全的结果结构"""

    def test_initial_state(self):
        s = QQScanLogin()
        assert s.status == 'waiting'
        assert s.creds is None
        r = s.result()
        assert set(r) == {'status', 'qr_image', 'creds', 'error', 'developers'}
        assert r['status'] == 'waiting'
        assert r['developers'] == []

    def test_web_flow_disables_automatic_subject_selection(self):
        s = QQScanLogin(auto_select=False)
        assert s.auto_select is False

    def test_select_developer_rejects_unknown_subject(self):
        async def check():
            s = QQScanLogin(auto_select=False)
            s.status = 'selecting'
            s.developers = [{'id': 'developer-1', 'name': '主体一'}]
            result = await s.select_developer('developer-2')
            assert result['status'] == 'selecting'
            assert result['error'] == '请选择有效的开发者主体'

        asyncio.run(check())

    def test_select_developer_completes_selected_subject(self):
        async def check():
            s = QQScanLogin(auto_select=False)
            s.status = 'selecting'
            s.developers = [{'id': 'developer-1', 'name': '主体一'}]
            selected = []

            async def complete_login(_session, developer_id):
                selected.append(developer_id)
                s.status = 'logged_in'
                s.creds = {'developer_id_lite': developer_id}
                return True

            s._complete_login = complete_login
            result = await s.select_developer('developer-1')
            assert selected == ['developer-1']
            assert result['status'] == 'logged_in'
            assert result['creds']['developer_id_lite'] == 'developer-1'

        asyncio.run(check())

    def test_cookiejar_no_quote(self):
        """skey/p_skey 等含 @*- 特殊字符的 cookie 不能被加引号, 否则 q.qq.com 判登录态无效"""

        async def check():
            s = QQScanLogin()
            client = s._client()
            try:
                assert s.jar is not None
                assert s.jar.quote_cookie is False
                cookies = SimpleCookie()
                cookies.load('p_skey="abc/def=="; Domain=.qq.com; Path=/')
                s.jar.update_cookies(cookies, response_url=URL('https://q.qq.com/'))
                header = s.jar.filter_cookies(URL('https://q.qq.com/')).output(header='', sep=';')
                assert header.strip() == 'p_skey=abc/def=='
            finally:
                await client.close()

        asyncio.run(check())

    def test_redirect_preserves_duplicate_cookie_paths(self):
        async def redirect(_request):
            response = web.HTTPFound('/dashboard')
            response.headers.add('Set-Cookie', 'sid=global; Path=/')
            response.headers.add('Set-Cookie', 'sid=lite; Path=/lite')
            raise response

        async def dashboard(request):
            return web.Response(text=request.headers.get('Cookie', ''))

        async def check():
            sock = socket.socket()
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
            sock.close()

            app = web.Application()
            app.router.add_get('/start', redirect)
            app.router.add_get('/dashboard', dashboard)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, '127.0.0.1', port)
            await site.start()

            scan = QQScanLogin()
            client = scan._client()
            try:
                async with client.get(f'http://127.0.0.1:{port}/start') as response:
                    cookie_header = await response.text()
                assert cookie_header == 'sid=global'
            finally:
                await client.close()
                await runner.cleanup()

        asyncio.run(check())

    def test_poll_uses_current_official_ptlogin_parameters(self):
        async def check():
            session = FakeSession(["ptuiCB('66','0','','0','二维码未失效', '')"])
            scan = QQScanLogin()
            scan.callback = (
                'https://q.qq.com/qqbot/openclaw/entity-picker.html'
                '?session_id=session-1&_wv=16777218'
            )
            scan.ptqrtoken = 123
            scan.login_sig = 'login-sig'
            scan._client = lambda: FakeClientContext(session)

            await scan._poll_once()

            method, url, kwargs = session.calls[0]
            assert method == 'GET'
            assert url == 'https://ssl.ptlogin2.qq.com/ptqrlogin'
            assert kwargs['params']['js_ver'] == '26030415'
            assert kwargs['params']['pt_js_version'] == 'b515fdc3'
            assert len(kwargs['params']['o1vId']) == 32
            assert kwargs['params']['u1'] == scan.callback
            assert 'has_onekey' not in kwargs['params']

        asyncio.run(check())

    def test_finish_notifies_confirming_before_reading_subjects(self):
        async def check():
            session = FakeSession([
                {'retcode': 0, 'msg': 'success'},
                {
                    'retcode': 0,
                    'data': {
                        'developer_infos': [{
                            'id': 'developer-1',
                            'subject_name': '主体一',
                        }],
                    },
                },
            ])
            scan = QQScanLogin(auto_select=False)
            scan.session_id = 'session-1'
            scan._jar_cookies = lambda: {'skey': '@abc-123*'}

            result = await scan._finish(session, '')

            assert result is False
            assert scan.status == 'selecting'
            assert [call[0] for call in session.calls] == ['POST', 'POST']
            assert session.calls[0][1] == 'https://q.qq.com/lite/write_login_state'
            assert session.calls[0][2]['json'] == {
                'session_id': 'session-1',
                'developer_id': '',
                'login_status': 1,
            }
            assert session.calls[1][1] == 'https://q.qq.com/lite/get_developers_for_login'
            assert session.calls[1][2]['json'] == {}
            for _, _, kwargs in session.calls:
                assert 'params' not in kwargs
                assert kwargs['headers'] == {
                    'Referer': 'https://q.qq.com/qqbot/openclaw/entity-picker.html',
                    'Origin': 'https://q.qq.com',
                }

        asyncio.run(check())

    def test_finish_propagates_subject_list_error(self):
        async def check():
            session = FakeSession([
                {'retcode': 0, 'msg': 'success'},
                {'retcode': 1001, 'msg': '参数错误，登录鉴权参数异常'},
            ])
            scan = QQScanLogin(auto_select=False)
            scan.session_id = 'session-1'
            scan._jar_cookies = lambda: {'skey': '@abc-123*'}

            result = await scan._finish(session, '')

            assert result is False
            assert scan.status == 'failed'
            assert scan.error == '参数错误，登录鉴权参数异常'
            assert len(session.calls) == 2

        asyncio.run(check())

    def test_complete_login_writes_selected_subject_with_agree_status(self):
        async def check():
            session = FakeSession([{'retcode': 1, 'msg': '选择失败'}])
            scan = QQScanLogin(auto_select=False)
            scan.session_id = 'session-1'
            scan._jar_cookies = lambda: {'skey': '@abc-123*'}

            result = await scan._complete_login(session, 'developer-1')

            assert result is False
            method, url, kwargs = session.calls[0]
            assert method == 'POST'
            assert url == 'https://q.qq.com/lite/write_login_state'
            assert kwargs['json'] == {
                'session_id': 'session-1',
                'developer_id': 'developer-1',
                'login_status': 2,
            }
            assert 'params' not in kwargs
            assert kwargs['headers'] == {
                'Referer': 'https://q.qq.com/qqbot/openclaw/entity-picker.html',
                'Origin': 'https://q.qq.com',
            }

        asyncio.run(check())

    def test_complete_login_sets_selected_subject_cookie_before_redirect(self):
        async def check():
            session = FakeSession([
                {'retcode': 0, 'msg': 'success'},
                {
                    'retcode': 0,
                    'data': {
                        'code': 0,
                        'redirect': 'https://q.qq.com/bopen/callback?code=login-code',
                        'developer_id': 'developer-1',
                    },
                },
                b'',
            ])
            scan = QQScanLogin(auto_select=False)
            scan.session_id = 'session-1'
            scan.jar = aiohttp.CookieJar(unsafe=True, quote_cookie=False)
            scan.jar.update_cookies({'skey': '@abc-123*'}, response_url=URL('https://q.qq.com/'))

            result = await scan._complete_login(session, 'developer-1')

            assert result is False
            assert session.calls[-1][1] == 'https://q.qq.com/bopen/callback?code=login-code'
            q_cookie = scan.jar.filter_cookies(URL('https://q.qq.com/'))
            bot_cookie = scan.jar.filter_cookies(URL('https://bot.q.qq.com/'))
            assert q_cookie['developer_id_lite'].value == 'developer-1'
            assert bot_cookie['developer_id_lite'].value == 'developer-1'

        asyncio.run(check())


class TestV2ProxyCredentials:
    def test_cookie_contains_all_ticket_variants(self):
        cookie = _v2_cookie({
            'uin': '2218872014',
            'developer_id_lite': 'developer-1',
            'b_token': 'BT',
            'qticket_lite': 'QL',
            'qticket': 'QT',
            'skey': '@abc-123*',
            'p_skey': 'P_SK',
        })
        assert 'quin=2218872014' in cookie
        assert 'quid=developer-1' in cookie
        assert 'b-token=BT' in cookie
        assert 'qticket_lite=QL' in cookie
        assert 'qticket=QT' in cookie
        assert 'skey=@abc-123*' in cookie
        assert 'p_skey=P_SK' in cookie

    def test_ready_requires_current_dashboard_credentials(self):
        assert _v2_ready({
            'b_token': 'BT',
            'qticket_lite': 'QL',
            'developer_id_lite': 'developer-1',
            'uin': '2218872014',
            'skey': '@abc-123*',
        })
        assert not _v2_ready({'b_token': 'BT', 'uin': '2218872014'})

    def test_current_developer_endpoint_is_allowed(self):
        assert any('/bopen/v2/get_audit_developer_info'.startswith(prefix) for prefix in _V2_ALLOWED_PREFIXES)
