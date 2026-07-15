"""QQ 开放平台 HTTP API 封装"""

import asyncio
import base64
import gzip
import json
import random
import re
import secrets
import socket
import time
from collections.abc import Sequence
from datetime import datetime
from http.cookies import BaseCookie, CookieError, Morsel, SimpleCookie
from urllib.parse import quote, urlencode

import aiohttp
from yarl import URL

_GZIP_MAGIC = b'\x1f\x8b\x08'
_TIMEOUT = aiohttp.ClientTimeout(total=15)
_BASE_HEADERS = {
    'Content-Type': 'application/json',
    'User-Agent': ('Mozilla/5.0 (Linux; U; Android 14; zh-cn) AppleWebKit/537.36 Chrome/109.0.5414.118 Mobile Safari/537.36'),
}
_QQ_HEADERS = {
    'Host': 'q.qq.com',
    'Origin': 'https://q.qq.com',
    'Referer': 'https://q.qq.com/',
}
_BOT_HEADERS = {
    'Host': 'bot.q.qq.com',
    'Origin': 'https://q.qq.com',
    'Referer': 'https://q.qq.com/',
}

_STATUS_MAP = {1: '未提审', 2: '审核中', 3: '审核通过'}
_TYPE_MAP = {1: '按钮模板', 2: 'markdown模板'}
_MSG_FIELDS = {
    'report_date': '报告日期',
    'up_msg_cnt': '上行消息量',
    'up_msg_uv': '上行消息人数',
    'down_msg_cnt': '下行消息量',
    'down_passive_msg_cnt': '被动消息数',
    'down_initiative_msg_cnt': '主动消息数',
    'bot_msg_cnt': '总消息量',
}
_GROUP_FIELDS = {
    'report_date': '报告日期',
    'existing_groups': '现有群组',
    'used_groups': '已使用群组',
    'added_groups': '新增群组',
    'removed_groups': '移除群组',
}
_FRIEND_FIELDS = {
    'report_date': '报告日期',
    'stock_added_friends': '现有好友数',
    'used_friends': '已使用好友数',
    'new_added_friends': '新增好友数',
    'new_removed_friends': '移除好友数',
}
_DATA_TYPE_MAP = {
    1: ('msg_data', _MSG_FIELDS),
    2: ('group_data', _GROUP_FIELDS),
    3: ('friend_data', _FRIEND_FIELDS),
}

_HTML_RE = re.compile(r'<[^>]+>')
_URL_RE = re.compile(r'https?://[^\s]+')
_DETAIL_RE = re.compile(r'\[查看详情\]\(')

# ── 新版开放平台扫码登录 (q.qq.com 官方登录流程) ──

_PT_APPID = '1600001541'
_PT_DAID = '750'
_PT_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'
_LITE_BASE = 'https://q.qq.com'
_PT_XUI = 'https://xui.ptlogin2.qq.com'
_PT_SSL = 'https://ssl.ptlogin2.qq.com'
_PT_CALLBACK = f'{_LITE_BASE}/qqbot/openclaw/entity-picker.html'
_EMPTY_URL = URL()
_PTUI_VERSION = '26030415'
_PT_JS_VERSION = 'b515fdc3'

_PTUICB_RE = re.compile(r"ptuiCB\((.*)\)", re.S)
_PTUI_VERSION_RE = re.compile(r'ptui_version:encodeURIComponent\("([^"]+)"\)')
_PT_JS_VERSION_RE = re.compile(r'/monorepo/([^/"]+)')


def _pt_hash33(s: str) -> int:
    """腾讯 ptqrtoken 计算 (hash33 of qrsig, 初始值 0)"""
    e = 0
    for ch in s or '':
        e += (e << 5) + ord(ch)
    return 2147483647 & e


def qq_bkn(skey: str) -> int:
    """腾讯 bkn/g_tk 计算 (hash33 of skey, 初始值 5381)"""
    value = 5381
    for ch in skey or '':
        value += (value << 5) + ord(ch)
    return value & 2147483647


def parse_ptuicb(text: str) -> dict:
    """解析 ptqrlogin 返回的 ptuiCB('code', ..., 'check_url', '', 'msg', ...)"""
    m = _PTUICB_RE.search(text or '')
    if not m:
        return {'code': '', 'url': '', 'msg': ''}
    args = []
    for part in m.group(1).split(','):
        args.append(part.strip().strip("'").strip('"'))
    return {
        'code': args[0] if len(args) > 0 else '',
        'url': args[2] if len(args) > 2 else '',
        'msg': args[4] if len(args) > 4 else '',
    }


def parse_ptlogin_versions(text: str) -> tuple[str, str]:
    ptui = _PTUI_VERSION_RE.search(text or '')
    js = _PT_JS_VERSION_RE.search(text or '')
    return (
        ptui.group(1) if ptui else _PTUI_VERSION,
        js.group(1) if js else _PT_JS_VERSION,
    )


def extract_login_creds(cookies: dict) -> dict:
    """从登录态 cookie 中提取新版/旧版开放平台所需凭证"""
    out = {}
    if cookies.get('b-token'):
        out['b_token'] = cookies['b-token']
    if cookies.get('qticket_lite'):
        out['qticket_lite'] = cookies['qticket_lite']
    if cookies.get('qticket'):
        out['qticket'] = cookies['qticket']
    if cookies.get('skey'):
        out['skey'] = cookies['skey']
    if cookies.get('p_skey'):
        out['p_skey'] = cookies['p_skey']
    quid = cookies.get('quid') or cookies.get('developer_id_lite') or cookies.get('developerId')
    if quid:
        out['developer_id_lite'] = quid
        out['developerId'] = quid
    uin = cookies.get('quin') or cookies.get('uin')
    if uin:
        out['uin'] = str(uin).lstrip('o')
    return out


def parse_login_developers(payload: dict) -> list[dict]:
    """兼容新版分组结构与旧版开发者主体响应。"""
    developers = []
    for group in payload.get('developers') or []:
        subject_type = group.get('typeId')
        if subject_type is None:
            subject_type = group.get('type_id')
        for item in group.get('items') or []:
            developer_id = item.get('id') or item.get('developer_id') or item.get('developerId')
            if not developer_id:
                continue
            item_type = item.get('type')
            developers.append({
                'id': str(developer_id),
                'name': item.get('name') or item.get('subject_name') or '开发者主体',
                'email': item.get('regMail') or item.get('reg_mail') or item.get('subject_email') or '',
                'subject_type': item_type if item_type is not None else (
                    subject_type if subject_type is not None else item.get('subject_type')
                ),
                'status': item.get('status', 0),
                'verified': True,
            })
    if developers:
        return developers

    data = payload.get('data') or {}
    infos = data.get('developer_infos') or data.get('developerInfos') or []
    unverified = data.get('unverified_developer_infos') or data.get('unverifiedDeveloperInfos') or []
    for items, verified in ((infos, True), (unverified, False)):
        for item in items:
            developer_id = item.get('id') or item.get('developer_id') or item.get('developerId')
            if not developer_id:
                continue
            developers.append({
                'id': str(developer_id),
                'name': item.get('subject_name') or item.get('name') or '开发者主体',
                'email': item.get('subject_email') or item.get('regMail') or '',
                'subject_type': item.get('subject_type'),
                'status': item.get('status', 0),
                'verified': verified,
            })
    return developers


class _UnquotedCookieJar(aiohttp.CookieJar):
    @property
    def quote_cookie(self) -> bool:
        return self._quote_cookie

    def filter_cookies(self, request_url: URL = _EMPTY_URL) -> BaseCookie[str]:
        cookies = super().filter_cookies(request_url)
        unquoted: BaseCookie[str] = BaseCookie()
        for name, cookie in cookies.items():
            morsel: Morsel[str] = Morsel()
            morsel.__setstate__({
                'key': name,
                'value': cookie.value,
                'coded_value': cookie.value,
            })
            unquoted[name] = morsel
        return unquoted

    def update_raw_cookies(self, headers: Sequence[str], response_url: URL):
        for header in headers:
            cookies = SimpleCookie()
            try:
                cookies.load(header)
            except CookieError:
                continue
            self.update_cookies(cookies, response_url)


class QQScanLogin:
    """一次性的 q.qq.com 新版扫码登录会话。

    流程: create_session → ptlogin2 扫码 → check_sig → write_login_state 绑定
    → /lite/poll → bopen/callback 302 下发 b-token/qticket 等凭证。
    跨请求共享同一 CookieJar 以保持登录态, 每次操作新建短生命周期 ClientSession。
    状态: waiting / scanned / confirming / selecting / expired / rejected / failed / logged_in
    """

    APPID = _PT_APPID
    DAID = _PT_DAID
    TTL = 300

    def __init__(self, auto_select=True):
        self.jar = None
        self.session_id = ''
        self.login_sig = ''
        self.ptqrtoken = 0
        self.callback = ''
        self.u1 = ''
        self.device_id = secrets.token_hex(16)
        self.ptui_version = _PTUI_VERSION
        self.pt_js_version = _PT_JS_VERSION
        self.status = 'waiting'
        self.creds = None
        self.qr_image = ''
        self.error = ''
        self.developers = []
        self.auto_select = auto_select
        self.created = time.time()
        self._lock = None
        self._finishing = False

    def _get_lock(self):
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def _client(self):
        if self.jar is None:
            # quote_cookie=False: skey/p_skey 等含 @*- 特殊字符, 加引号会被 q.qq.com 判定登录态无效
            self.jar = _UnquotedCookieJar(unsafe=True, quote_cookie=False)
        trace = aiohttp.TraceConfig()
        trace.on_request_redirect.append(self._sync_response_cookies)
        trace.on_request_end.append(self._sync_response_cookies)
        conn = aiohttp.TCPConnector(family=socket.AF_INET, ssl=False)
        return aiohttp.ClientSession(
            timeout=_TIMEOUT,
            connector=conn,
            cookie_jar=self.jar,
            headers={'User-Agent': _PT_UA},
            trace_configs=[trace],
        )

    async def _sync_response_cookies(self, _session, _context, params):
        headers = params.response.headers.getall('Set-Cookie', ())
        self.jar.update_raw_cookies(headers, params.response.url)

    def _jar_cookies(self) -> dict:
        out = {}
        for morsel in self.jar:
            out[morsel.key] = morsel.value
        return out

    def _set_dashboard_developer(self, developer_id: str):
        cookie = SimpleCookie()
        cookie['developer_id_lite'] = developer_id
        cookie['developer_id_lite']['domain'] = 'q.qq.com'
        cookie['developer_id_lite']['path'] = '/'
        self.jar.update_cookies(cookie, response_url=URL(_LITE_BASE))

    async def _create_qr(self, s: aiohttp.ClientSession):
        """在给定会话内新建 /lite 会话并生成 ptlogin 二维码"""
        async with s.get(
            f'{_LITE_BASE}/lite/create_session',
            headers={'Referer': 'https://q.qq.com/'},
        ) as r:
            j = json.loads(QQBotAPI._decode(await r.read()))
        self.session_id = (j.get('data') or {}).get('session_id', '')
        if not self.session_id:
            self.status = 'failed'
            self.error = '创建登录会话失败'
            return
        callback = f'{_PT_CALLBACK}?session_id={self.session_id}&_wv=16777218'
        self.callback = callback
        self.u1 = quote(callback, safe='')
        xurl = (
            f'{_PT_XUI}/cgi-bin/xlogin?appid={self.APPID}&daid={self.DAID}&target=self&style=22'
            f'&hide_title_bar=1&hide_border=1&hide_title=1&theme=10&s_url={self.u1}'
        )
        async with s.get(xurl) as r:
            xlogin = QQBotAPI._decode(await r.read())
        self.ptui_version, self.pt_js_version = parse_ptlogin_versions(xlogin)
        self.login_sig = self._jar_cookies().get('pt_login_sig', '')
        show = (
            f'{_PT_SSL}/ptqrshow?appid={self.APPID}&e=2&l=M&s=3&d=72&v=4&t={random.random()}'
            f'&daid={self.DAID}&pt_3rd_aid=0&u1={self.u1}'
        )
        async with s.get(show, headers={'Referer': _PT_XUI + '/'}) as r:
            png = await r.read()
        self.ptqrtoken = _pt_hash33(self._jar_cookies().get('qrsig', ''))
        self.qr_image = 'data:image/png;base64,' + base64.b64encode(png).decode()
        self.status = 'waiting'
        self.created = time.time()

    async def start(self) -> dict:
        """开始登录: 生成二维码, 返回状态字典"""
        try:
            async with self._client() as s:
                await self._create_qr(s)
        except Exception as e:
            self.status = 'failed'
            self.error = f'初始化失败: {e}'
        return self.result()

    async def poll(self) -> dict:
        """轮询一次登录状态。串行化以避免并发轮询在确认阶段互相破坏会话。"""
        if self.status in ('logged_in', 'failed', 'selecting') or self._finishing:
            return self.result()
        lock = self._get_lock()
        if lock.locked():
            return self.result()
        async with lock:
            if self.status in ('logged_in', 'failed') or self._finishing:
                return self.result()
            return await self._poll_once()

    async def _poll_once(self) -> dict:
        try:
            async with self._client() as s:
                params = {
                    'u1': self.callback,
                    'ptqrtoken': self.ptqrtoken,
                    'ptredirect': 0,
                    'h': 1,
                    't': 1,
                    'g': 1,
                    'from_ui': 1,
                    'ptlang': 2052,
                    'action': f'0-0-{int(time.time() * 1000)}',
                    'js_ver': self.ptui_version,
                    'js_type': 1,
                    'login_sig': self.login_sig,
                    'pt_uistyle': 40,
                    'aid': self.APPID,
                    'daid': self.DAID,
                    'o1vId': self.device_id,
                    'pt_js_version': self.pt_js_version,
                }
                async with s.get(
                    f'{_PT_SSL}/ptqrlogin',
                    params=params,
                    headers={'Referer': _PT_XUI + '/'},
                ) as r:
                    txt = (await r.read()).decode('utf-8', errors='ignore')
                pt = parse_ptuicb(txt)
                code = pt['code']
                if code == '0':
                    self.status = 'confirming'
                    self._finishing = True
                    try:
                        await self._finish(s, pt['url'])
                    finally:
                        self._finishing = False
                elif code == '67':
                    self.status = 'scanned'
                elif code == '66':
                    self.status = 'waiting'
                elif code == '65':
                    self.status = 'waiting'
                    await self._create_qr(s)
                elif code == '68':
                    self.status = 'rejected'
                else:
                    self.status = 'waiting'
        except Exception as e:
            self.status = 'failed'
            self.error = f'轮询失败: {e}'
        return self.result()

    async def select_developer(self, developer_id: str) -> dict:
        """选择开发者主体并继续完成登录。"""
        if self.status != 'selecting':
            return self.result()
        if developer_id not in {item['id'] for item in self.developers}:
            self.error = '请选择有效的开发者主体'
            return self.result()
        async with self._get_lock():
            if self.status != 'selecting':
                return self.result()
            self.status = 'confirming'
            self.error = ''
            async with self._client() as s:
                await self._complete_login(s, developer_id)
        return self.result()

    async def _finish(self, s: aiohttp.ClientSession, check_url: str) -> bool:
        """ptlogin 成功后完成 q.qq.com 绑定并换取 b-token"""
        try:
            if check_url:
                async with s.get(check_url, headers={'Referer': _PT_XUI + '/'}) as r:
                    await r.read()
            qhdr = {
                'Referer': _PT_CALLBACK,
                'Origin': _LITE_BASE,
            }
            async with s.post(
                f'{_LITE_BASE}/lite/write_login_state',
                json={'session_id': self.session_id, 'developer_id': '', 'login_status': 1},
                headers=qhdr,
            ) as r:
                confirming = json.loads(QQBotAPI._decode(await r.read()))
            if confirming.get('retcode') != 0:
                self.status = 'failed'
                self.error = confirming.get('msg') or '更新登录状态失败'
                return False
            async with s.post(
                f'{_LITE_BASE}/lite/get_developers_for_login',
                json={},
                headers=qhdr,
            ) as r:
                dj = json.loads(QQBotAPI._decode(await r.read()))
            if dj.get('retcode') != 0:
                self.status = 'failed'
                self.error = dj.get('msg') or '获取开发者主体失败'
                return False
            self.developers = parse_login_developers(dj)
            if not self.developers:
                self.status = 'failed'
                self.error = dj.get('msg') or '未找到可登录的开发者主体'
                return False
            if not self.auto_select:
                self.status = 'selecting'
                return False
            return await self._complete_login(s, self.developers[0]['id'])
        except Exception as e:
            self.status = 'failed'
            self.error = f'绑定失败: {e}'
            return False

    async def _complete_login(self, s: aiohttp.ClientSession, developer_id: str) -> bool:
        qhdr = {
            'Referer': _PT_CALLBACK,
            'Origin': _LITE_BASE,
        }
        try:
            async with s.post(
                f'{_LITE_BASE}/lite/write_login_state',
                json={'session_id': self.session_id, 'developer_id': developer_id, 'login_status': 2},
                headers=qhdr,
            ) as r:
                wr = json.loads(QQBotAPI._decode(await r.read()))
            if wr.get('retcode') != 0:
                self.status = 'failed'
                self.error = wr.get('msg') or '选择开发者主体失败'
                return False
            redirect = ''
            dev = developer_id
            for _ in range(20):
                async with s.get(
                    f'{_LITE_BASE}/lite/poll',
                    params={'session_id': self.session_id},
                    headers={'Referer': 'https://q.qq.com/'},
                ) as r:
                    pj = json.loads(QQBotAPI._decode(await r.read()))
                d = pj.get('data') or {}
                if pj.get('retcode') == 0 and d.get('code') == 0:
                    redirect = d.get('redirect', '')
                    dev = dev or d.get('developer_id', '')
                    break
                if d.get('code') in (2, 4):
                    self.status = 'failed'
                    self.error = d.get('message') or '登录失败'
                    return False
                await asyncio.sleep(1)
            if redirect:
                self._set_dashboard_developer(dev)
                async with s.get(redirect, headers={'Referer': 'https://q.qq.com/'}) as r:
                    await r.read()
            creds = extract_login_creds(self._jar_cookies())
            if dev:
                creds.setdefault('developer_id_lite', dev)
                creds.setdefault('developerId', dev)
            required = ('b_token', 'qticket_lite', 'developer_id_lite', 'uin', 'skey')
            if all(creds.get(key) for key in required):
                self.creds = creds
                self.status = 'logged_in'
                return True
            self.status = 'failed'
            self.error = self.error or '登录凭证不完整，请重新扫码登录'
            return False
        except Exception as e:
            self.status = 'failed'
            self.error = f'完成登录失败: {e}'
            return False

    def result(self) -> dict:
        return {
            'status': self.status,
            'qr_image': self.qr_image,
            'creds': self.creds,
            'error': self.error,
            'developers': self.developers,
        }


class QQBotAPI:
    __slots__ = ()

    @staticmethod
    def _build_cookie(uin='', quid='', ticket=''):
        parts = []
        if uin:
            parts.append(f'quin={uin}')
        if quid:
            parts.extend([f'quid={quid}', f'developerId={quid}'])
        if ticket:
            parts.append(f'qticket={ticket}')
        return '; '.join(parts)

    @staticmethod
    def _decode(content: bytes) -> str:
        try:
            return gzip.decompress(content).decode('utf-8') if content[:3] == _GZIP_MAGIC else content.decode('utf-8')
        except Exception:
            return content.decode('utf-8', errors='ignore')

    async def _request(self, method, url, uin='', quid='', ticket='', data=None, extra_headers=None, return_cookies=False):
        try:
            headers = _BASE_HEADERS.copy()
            if uin or quid or ticket:
                headers['Cookie'] = self._build_cookie(uin, quid, ticket)
            if extra_headers:
                headers.update(extra_headers)
            conn = aiohttp.TCPConnector(family=socket.AF_INET, ssl=False)
            async with aiohttp.ClientSession(timeout=_TIMEOUT, connector=conn) as session:
                if method == 'GET':
                    async with session.get(url, headers=headers) as resp:
                        raw = await resp.read()
                        cookies = {k: m.value for k, m in resp.cookies.items()} if return_cookies else {}
                else:
                    async with session.post(url, json=data, headers=headers) as resp:
                        raw = await resp.read()
                        cookies = {k: m.value for k, m in resp.cookies.items()} if return_cookies else {}
            body = json.loads(self._decode(raw))
            return (body, cookies) if return_cookies else body
        except Exception as e:
            err = {'code': 500, 'msg': f'请求失败: {e}'}
            return (err, {}) if return_cookies else err

    async def v2_request(self, method, path, cookie='', skey='', data=None, params=None):
        """新版开放平台 v2 接口代理请求, 返回 QQ 原始 JSON"""
        host = 'bot.q.qq.com' if path.startswith('/cgi-bin/') else 'q.qq.com'
        url = f'https://{host}{path}'
        query = dict(params or {})
        if skey:
            query.setdefault('bkn', qq_bkn(skey))
        if query:
            url += ('&' if '?' in url else '?') + urlencode(query)
        extra = dict(_BOT_HEADERS if host == 'bot.q.qq.com' else _QQ_HEADERS)
        extra['Referer'] = 'https://q.qq.com/qqbot/dashboard/'
        try:
            headers = _BASE_HEADERS.copy()
            headers.update(extra)
            if cookie:
                headers['Cookie'] = cookie
            conn = aiohttp.TCPConnector(family=socket.AF_INET, ssl=False)
            async with aiohttp.ClientSession(timeout=_TIMEOUT, connector=conn) as session:
                if method.upper() == 'GET':
                    async with session.get(url, headers=headers) as resp:
                        raw = await resp.read()
                        ctype = resp.headers.get('Content-Type', '')
                        disp = resp.headers.get('Content-Disposition', '')
                else:
                    async with session.post(url, json=data, headers=headers) as resp:
                        raw = await resp.read()
                        ctype = resp.headers.get('Content-Type', '')
                        disp = resp.headers.get('Content-Disposition', '')
            if 'application/json' not in ctype and ('text/csv' in ctype or 'octet-stream' in ctype or 'spreadsheet' in ctype or disp):
                return {'_binary': True, 'content': raw, 'content_type': ctype or 'application/octet-stream', 'disposition': disp}
            return json.loads(self._decode(raw))
        except Exception as e:
            return {'retcode': 500, 'msg': f'请求失败: {e}'}

    async def v2_switch_developer(self, developer_id, cookie='', skey=''):
        url = 'https://q.qq.com/api/v3/login/switch_developer'
        if skey:
            url += '?' + urlencode({'bkn': qq_bkn(skey)})
        headers = _BASE_HEADERS.copy()
        headers.update(_QQ_HEADERS)
        headers.update({
            'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
            'Referer': 'https://q.qq.com/qqbot/dashboard/',
        })
        if cookie:
            headers['Cookie'] = cookie
        try:
            conn = aiohttp.TCPConnector(family=socket.AF_INET, ssl=False)
            async with (
                aiohttp.ClientSession(timeout=_TIMEOUT, connector=conn) as session,
                session.post(url, data={'target_developer_id': developer_id}, headers=headers) as resp,
            ):
                raw = await resp.read()
                cookies = {key: morsel.value for key, morsel in resp.cookies.items()}
                for value in resp.headers.getall('Set-Cookie', ()):
                    parsed = SimpleCookie()
                    try:
                        parsed.load(value)
                    except CookieError:
                        continue
                    cookies.update({key: morsel.value for key, morsel in parsed.items()})
            return json.loads(self._decode(raw)), cookies
        except Exception as e:
            return {'ret': 500, 'msg': f'请求失败: {e}'}, {}

    @staticmethod
    def _fmt_ts(ts):
        try:
            return datetime.fromtimestamp(float(ts)).strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # ── 模板 ──

    async def get_message_templates(self, uin='', quid='', ticket='', appid='', start=0, limit=30):
        resp = await self._request(
            'POST',
            'https://bot.q.qq.com/cgi-bin/msg_tpl/list',
            uin,
            quid,
            ticket,
            {'bot_appid': appid, 'start': start, 'limit': limit},
        )
        if resp.get('code') == 500:
            return resp
        resp['code'] = 0 if resp.get('retcode') == 0 else -1
        if resp.get('retcode') == 0:
            resp.setdefault('data', {}).setdefault('list', [])
            for t in resp['data']['list']:
                new = {
                    '模板id': t.get('tpl_id'),
                    '模板名称': t.get('tpl_name'),
                    '模板状态': _STATUS_MAP.get(t.get('status'), '未通过'),
                    '模板类型': _TYPE_MAP.get(t.get('tpl_type'), '未知类型'),
                    '模板内容': t.get('text'),
                    '创建时间': self._fmt_ts(t.get('create_time')),
                }
                t.clear()
                t.update(new)
        return resp

    # ── 私信 ──

    async def get_private_messages(self, uin='', quid='', ticket=''):
        resp = await self._request(
            'POST',
            'https://q.qq.com/pb/AppFetchPrivateMsg',
            uin,
            quid,
            ticket,
            {'page_num': 0, 'page_size': 9999, 'receiver': quid, 'appType': 2},
        )
        if resp.get('code') == 500:
            return {'code': -1, 'messages': [], 'error': resp.get('msg')}
        if resp.get('code', 0) != 0:
            return {
                'code': -1,
                'messages': [],
                'error': resp.get('message', '未知错误'),
            }
        result = {'code': 0, 'messages': []}
        for msg in resp.get('data', {}).get('privateMsgs', []):
            content = _DETAIL_RE.sub('', _URL_RE.sub('', _HTML_RE.sub('', msg.get('content', ''))))
            result['messages'].append(
                {
                    'title': _HTML_RE.sub('', msg.get('title', '')),
                    'content': content,
                    'send_time': self._fmt_ts(msg.get('send_time')),
                }
            )
        return result

    # ── 数据报表 ──

    async def get_bot_data(self, uin='', quid='', ticket='', appid='', data_type=1):
        resp = await self._request(
            'GET',
            f'https://bot.q.qq.com/cgi-bin/datareport/read?bot_appid={appid}&data_type={data_type}&data_range=2&scene_id=1',
            uin,
            quid,
            ticket,
        )
        if resp.get('code') == 500:
            return {'retcode': -1, 'code': 500, 'msg': resp.get('msg'), 'data': {}}
        cfg = _DATA_TYPE_MAP.get(data_type)
        if resp.get('retcode') == 0 and cfg:
            key, fields = cfg
            for item in resp.get('data', {}).get(key, []):
                new = {cn: item.get(en) for en, cn in fields.items()}
                item.clear()
                item.update(new)
        return resp

    # ── 机器人列表 ──

    async def get_bot_list(self, uin='', quid='', ticket=''):
        resp = await self._request(
            'POST',
            'https://q.qq.com/homepagepb/GetAppListForLogin',
            uin,
            quid,
            ticket,
            {'uin': uin, 'developer_id': quid, 'ticket': ticket, 'app_type': [2]},
        )
        if resp.get('code') == 500:
            return {'retcode': -1, 'code': 500, 'message': resp.get('msg'), 'data': {}}
        return resp

    # ── 二维码登录 ──

    async def get_qr_login_info(self, qrcode='', return_cookies=False):
        return await self._request(
            'POST',
            'https://q.qq.com/qrcode/get',
            data={'qrcode': qrcode},
            extra_headers=_QQ_HEADERS,
            return_cookies=return_cookies,
        )

    async def create_login_qr(self):
        resp = await self._request(
            'POST',
            'https://q.qq.com/qrcode/create',
            data={'type': '777'},
            extra_headers=_QQ_HEADERS,
        )
        if resp.get('code') == 500:
            return {'status': 'error', 'message': resp.get('msg')}
        qr = resp.get('data', {}).get('QrCode')
        if qr:
            return {
                'status': 'success',
                'url': f'https://q.qq.com/login/applist?client=qq&code={qr}&ticket=null',
                'qr': qr,
            }
        return {'status': 'error', 'message': 'QrCode not found'}

    # ── 扫码快捷绑定机器人 ──

    async def create_bind_task(self, key=''):
        return await self._request(
            'POST',
            'https://q.qq.com/lite/create_bind_task',
            data={'key': key},
            extra_headers=_QQ_HEADERS,
        )

    async def poll_bind_result(self, task_id=''):
        return await self._request(
            'POST',
            'https://q.qq.com/lite/poll_bind_result',
            data={'task_id': task_id},
            extra_headers=_QQ_HEADERS,
        )

    # ── 白名单 ──

    async def get_white_list(self, appid='', uin='', uid='', ticket=''):
        if not all([appid, uin, uid, ticket]):
            return {'code': 400, 'msg': '参数不完整'}
        resp = await self._request(
            'GET',
            f'https://bot.q.qq.com/cgi-bin/dev_info/white_ip_config?bot_appid={appid}',
            uin,
            uid,
            ticket,
        )
        if resp.get('code') == 500:
            return {'code': 500, 'msg': resp.get('msg')}
        if resp.get('retcode') != 0:
            return {'code': -1, 'msg': '获取白名单失败'}
        try:
            ip_list = resp.get('data', {}).get('ip_white_infos', {}).get('prod', {}).get('ip_list', [])
            return {'code': 0, 'data': ip_list if isinstance(ip_list, list) else []}
        except Exception:
            return {'code': 0, 'data': []}

    async def create_white_login_qr(self, appid='', uin='', uid='', ticket='', qr_type=51):
        if not all([appid, uin, uid, ticket]):
            return {'code': 400, 'qrcode': None}
        resp = await self._request(
            'POST',
            'https://q.qq.com/qrcode/create',
            uin,
            uid,
            ticket,
            {'type': qr_type, 'miniAppId': appid},
        )
        qr = resp.get('data', {}).get('QrCode')
        if qr:
            return {
                'code': 0,
                'qrcode': qr,
                'url': f'https://q.qq.com/qrcode/check?client=qq&code={qr}&ticket={ticket}',
            }
        return {'code': -1, 'qrcode': None}

    async def update_white_list(self, appid='', uin='', uid='', ticket='', qrcode='', ip='', action=''):
        if not all([appid, uin, uid, ticket, qrcode, ip, action]):
            return {'code': 400, 'msg': '参数不完整'}
        current = await self.get_white_list(appid, uin, uid, ticket)
        if current.get('code') != 0:
            return {'code': 500, 'msg': '获取白名单失败'}
        cur_list = current.get('data', [])
        if action == 'add':
            final = cur_list + [ip] if ip not in cur_list else None
            if final is None:
                return {'code': 409, 'msg': 'IP 已存在'}
        elif action == 'del':
            final = [i for i in cur_list if i != ip]
        else:
            return {'code': 400, 'msg': '无效操作'}
        resp = await self._request(
            'POST',
            'https://bot.q.qq.com/cgi-bin/dev_info/update_white_ip_config',
            uin,
            uid,
            ticket,
            {
                'bot_appid': appid,
                'ip_white_infos': {'prod': {'ip_list': list(set(final)), 'use': True}},
                'qr_code': qrcode,
            },
        )
        if resp.get('retcode') != 0:
            return {'code': -1, 'msg': resp.get('msg', '操作失败')}
        return {'code': 0, 'msg': '操作成功'}

    async def verify_qr_auth(self, appid='', uin='', uid='', ticket='', qrcode=''):
        resp = await self._request('POST', 'https://q.qq.com/qrcode/get', uin, uid, ticket, {'qrcode': qrcode})
        return {'code': 0} if resp.get('code') == 0 else {'code': -1, 'msg': '未授权'}

    # ── 模板二维码 / 提审 / 删除 ──

    async def create_template_qr(self, uin='', quid='', ticket=''):
        resp = await self._request(
            'POST',
            'https://q.qq.com/qrcode/create',
            uin,
            quid,
            ticket,
            {'type': 40, 'miniAppId': ''},
            {**_QQ_HEADERS, 'Referer': 'https://q.qq.com/qqbot/'},
        )
        if resp.get('code') == 500:
            return {'code': 500, 'msg': resp.get('msg')}
        return resp

    async def preview_template(self, bot_appid='', template_data=None, uin='', uid='', ticket=''):
        if not bot_appid or not template_data:
            return {'retcode': 400, 'msg': '参数不完整'}
        resp = await self._request(
            'POST',
            'https://bot.q.qq.com/cgi-bin/msg_tpl/preview',
            uin,
            uid,
            ticket,
            {'bot_appid': bot_appid, 'info': template_data},
            _BOT_HEADERS,
        )
        if resp.get('code') == 500:
            return {'retcode': 500, 'msg': resp.get('msg')}
        return resp

    async def submit_template(self, bot_appid='', template_data=None, qrcode='', uin='', uid='', ticket=''):
        if not all([bot_appid, template_data, qrcode]):
            return {'retcode': 400, 'msg': '参数不完整'}
        return await self._tpl_action(
            'create',
            uin,
            uid,
            ticket,
            {'bot_appid': bot_appid, 'info': template_data, 'qrcode': qrcode},
        )

    async def audit_templates(self, bot_appid='', tpl_ids=None, qrcode='', uin='', uid='', ticket=''):
        return await self._tpl_batch_action('audit', bot_appid, tpl_ids, qrcode, uin, uid, ticket)

    async def delete_templates(self, bot_appid='', tpl_ids=None, qrcode='', uin='', uid='', ticket=''):
        return await self._tpl_batch_action('delete', bot_appid, tpl_ids, qrcode, uin, uid, ticket)

    async def _tpl_batch_action(self, action, bot_appid, tpl_ids, qrcode, uin, uid, ticket):
        if not all([bot_appid, tpl_ids, qrcode]):
            return {'retcode': 400, 'msg': '参数不完整'}
        appid_val = int(bot_appid) if isinstance(bot_appid, str) else bot_appid
        return await self._tpl_action(
            action,
            uin,
            uid,
            ticket,
            {'bot_appid': appid_val, 'tpl_id': tpl_ids, 'qrcode': qrcode},
        )

    async def _tpl_action(self, action, uin, uid, ticket, data):
        resp = await self._request(
            'POST',
            f'https://bot.q.qq.com/cgi-bin/msg_tpl/{action}',
            uin,
            uid,
            ticket,
            data,
            _BOT_HEADERS,
        )
        if resp.get('code') == 500:
            return {'retcode': 500, 'msg': resp.get('msg')}
        return resp

    # ── 事件订阅 ──

    async def get_event_list(self, appid='', uin='', uid='', ticket=''):
        if not all([appid, uin, uid, ticket]):
            return {'code': 400, 'msg': '参数不完整'}
        resp = await self._request(
            'POST',
            'https://bot.q.qq.com/cgi-bin/event_subscirption/list_event',
            uin,
            uid,
            ticket,
            {'bot_appid': str(appid)},
            _BOT_HEADERS,
        )
        if resp.get('code') == 500:
            return {'code': 500, 'msg': resp.get('msg')}
        if resp.get('retcode') != 0:
            return {'code': -1, 'msg': resp.get('msg', '获取事件列表失败')}
        return {'code': 0, 'data': resp.get('data', {}).get('events', [])}

    async def modify_event_subscription(self, appid='', event_ids=None, qrcode='', uin='', uid='', ticket=''):
        if not appid or not qrcode or event_ids is None:
            return {'code': 400, 'msg': '参数不完整'}
        resp = await self._request(
            'POST',
            'https://bot.q.qq.com/cgi-bin/event_subscirption/modify',
            uin,
            uid,
            ticket,
            {'bot_appid': str(appid), 'event_ids': list(event_ids), 'qr_code': qrcode},
            _BOT_HEADERS,
        )
        if resp.get('code') == 500:
            return {'code': 500, 'msg': resp.get('msg')}
        if resp.get('retcode') != 0:
            return {'code': -1, 'msg': resp.get('msg', '修改订阅失败')}
        return {'code': 0, 'msg': '操作成功'}

    # ── 回调地址 (Webhook) ──

    async def get_webhook(self, appid='', uin='', uid='', ticket=''):
        if not all([appid, uin, uid, ticket]):
            return {'code': 400, 'msg': '参数不完整'}
        resp = await self._request(
            'POST',
            'https://bot.q.qq.com/cgi-bin/callback/get_webhook',
            uin,
            uid,
            ticket,
            {'bot_appid': str(appid)},
            _BOT_HEADERS,
        )
        if resp.get('code') == 500:
            return {'code': 500, 'msg': resp.get('msg')}
        if resp.get('retcode') != 0:
            return {'code': -1, 'msg': resp.get('msg', '获取回调地址失败')}
        return {'code': 0, 'data': resp.get('data', {})}

    async def check_webhook(self, appid='', webhook_url='', uin='', uid='', ticket=''):
        if not all([appid, webhook_url]):
            return {'code': 400, 'msg': '参数不完整'}
        resp = await self._request(
            'POST',
            'https://bot.q.qq.com/cgi-bin/callback/check_webhook',
            uin,
            uid,
            ticket,
            {'bot_appid': str(appid), 'webhook_url': webhook_url},
            _BOT_HEADERS,
        )
        if resp.get('code') == 500:
            return {'code': 500, 'msg': resp.get('msg')}
        if resp.get('retcode') != 0:
            return {'code': resp.get('retcode', -1), 'msg': resp.get('msg', '地址校验未通过')}
        return {'code': 0, 'msg': '地址校验通过'}

    async def set_webhook(self, appid='', webhook_url='', qrcode='', uin='', uid='', ticket=''):
        if not all([appid, webhook_url, qrcode]):
            return {'code': 400, 'msg': '参数不完整'}
        resp = await self._request(
            'POST',
            'https://bot.q.qq.com/cgi-bin/callback/set_webhook',
            uin,
            uid,
            ticket,
            {'bot_appid': str(appid), 'webhook_url': webhook_url, 'qr_code': qrcode},
            _BOT_HEADERS,
        )
        if resp.get('code') == 500:
            return {'code': 500, 'msg': resp.get('msg')}
        if resp.get('retcode') != 0:
            return {'code': -1, 'msg': resp.get('msg', '设置回调地址失败')}
        return {'code': 0, 'msg': '操作成功'}


_api = None


def get_bot_api():
    global _api
    if _api is None:
        _api = QQBotAPI()
    return _api
