"""新版开放平台扫码与手机 QQ 授权登录。"""

import asyncio
import base64
import json
import random
import re
import secrets
import socket
import time
from collections.abc import Sequence
from http.cookies import BaseCookie, CookieError, Morsel, SimpleCookie
from typing import Any, cast
from urllib.parse import quote

import aiohttp
from yarl import URL

from web.tools._bot.qq_http import _TIMEOUT, dashboard_credentials_ready, decode_body

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


def parse_ptuicb(text: str) -> dict:
    """解析 ptqrlogin 返回的 ptuiCB('code', ..., 'check_url', '', 'msg', ...)"""
    m = _PTUICB_RE.search(text or '')
    if not m:
        return {'code': '', 'url': '', 'msg': ''}
    args = [part.strip().strip("'").strip('"') for part in m.group(1).split(',')]
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
        return bool(self._quote_cookie)

    def filter_cookies(self, request_url: URL = _EMPTY_URL) -> BaseCookie[str]:
        cookies = super().filter_cookies(request_url)
        unquoted: BaseCookie[str] = BaseCookie()
        for name, cookie in cookies.items():
            morsel: Morsel[str] = Morsel()
            morsel.set(name, cookie.value, cookie.value)
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
    """一次性 Lite 登录会话，支持 ptlogin 扫码和手机 QQ 授权。"""

    APPID = _PT_APPID
    DAID = _PT_DAID
    TTL = 300

    def __init__(self, auto_select=True, *, mobile=False):
        self.jar = None
        self.login_id = secrets.token_urlsafe(16)
        self.mobile = mobile
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
        conn = aiohttp.TCPConnector(family=socket.AF_INET)
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

    async def _create_lite_session(self, s: aiohttp.ClientSession) -> bool:
        async with s.get(
            f'{_LITE_BASE}/lite/create_session',
            headers={'Referer': 'https://q.qq.com/'},
        ) as r:
            j = json.loads(decode_body(await r.read()))
        self.session_id = (j.get('data') or {}).get('session_id', '')
        if not self.session_id:
            self.status = 'failed'
            self.error = '创建登录会话失败'
            return False
        self.callback = str(URL(_PT_CALLBACK).with_query(session_id=self.session_id, _wv=16777218))
        self.created = time.time()
        return True

    async def _create_qr(self, s: aiohttp.ClientSession):
        """在给定会话内新建 /lite 会话并生成 ptlogin 二维码"""
        if not await self._create_lite_session(s):
            return
        self.u1 = quote(self.callback, safe='')
        xurl = (
            f'{_PT_XUI}/cgi-bin/xlogin?appid={self.APPID}&daid={self.DAID}&target=self&style=22'
            f'&hide_title_bar=1&hide_border=1&hide_title=1&theme=10&s_url={self.u1}'
        )
        async with s.get(xurl) as r:
            xlogin = decode_body(await r.read())
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
        """创建会话并返回对应的登录入口。"""
        try:
            async with self._client() as s:
                if self.mobile:
                    await self._create_lite_session(s)
                else:
                    await self._create_qr(s)
        except Exception:
            self.status = 'failed'
            self.error = '初始化登录会话失败，请稍后重试'
        return self.result()

    async def poll(self) -> dict:
        """轮询一次登录状态。串行化以避免并发轮询在确认阶段互相破坏会话。"""
        if not self.session_id or self.status in ('logged_in', 'failed', 'selecting', 'expired', 'rejected') or self._finishing:
            return self.result()
        lock = self._get_lock()
        if lock.locked():
            return self.result()
        async with lock:
            if self.status in ('logged_in', 'failed') or self._finishing:
                return self.result()
            if self.mobile:
                return await self._poll_mobile()
            return await self._poll_once()

    async def _poll_lite(self, s: aiohttp.ClientSession) -> dict:
        async with s.get(
            f'{_LITE_BASE}/lite/poll',
            params={'session_id': self.session_id},
            headers={'Referer': f'{_LITE_BASE}/'},
        ) as r:
            return cast(dict[Any, Any], json.loads(decode_body(await r.read())))

    async def _poll_mobile(self) -> dict:
        if time.time() - self.created >= self.TTL:
            self.status = 'expired'
            return self.result()
        try:
            async with self._client() as s:
                result = await self._poll_lite(s)
                data = result.get('data') or {}
                code = data.get('code')
                if result.get('retcode') == 0 and code == 0:
                    await self._finish_lite(s, data.get('redirect'), data.get('developer_id'))
                elif code in (2, 4):
                    self.status = 'expired' if code == 2 else 'rejected'
                elif result.get('retcode') == 0 and code in (1, 3):
                    self.status = 'waiting' if code == 1 else 'scanned'
                else:
                    self.status = 'failed'
                    self.error = '获取手机 QQ 授权状态失败，请重试'
        except (aiohttp.ClientError, TimeoutError):
            # 保留会话，下次轮询重试上游的临时网络故障。
            pass
        except Exception:
            self.status = 'failed'
            self.error = '完成手机 QQ 授权失败，请重试'
        return self.result()

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
                confirming = json.loads(decode_body(await r.read()))
            if confirming.get('retcode') != 0:
                self.status = 'failed'
                self.error = confirming.get('msg') or '更新登录状态失败'
                return False
            async with s.post(
                f'{_LITE_BASE}/lite/get_developers_for_login',
                json={},
                headers=qhdr,
            ) as r:
                dj = json.loads(decode_body(await r.read()))
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
                wr = json.loads(decode_body(await r.read()))
            if wr.get('retcode') != 0:
                self.status = 'failed'
                self.error = wr.get('msg') or '选择开发者主体失败'
                return False
            redirect = ''
            dev = developer_id
            for _ in range(20):
                pj = await self._poll_lite(s)
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
            return await self._finish_lite(s, redirect, dev)
        except Exception as e:
            self.status = 'failed'
            self.error = f'完成登录失败: {e}'
            return False

    async def _finish_lite(self, s: aiohttp.ClientSession, redirect: str | None, developer_id: str | None) -> bool:
        if redirect:
            target = URL(_LITE_BASE).join(URL(redirect))
            if self.mobile and (target.origin() != URL(_LITE_BASE) and target.origin() != URL('https://bot.q.qq.com')):
                self.status = 'failed'
                self.error = 'QQ 返回了无效的授权回调地址'
                return False
            if developer_id:
                self._set_dashboard_developer(developer_id)
            async with s.get(target, headers={'Referer': f'{_LITE_BASE}/'}) as r:
                await r.read()
        creds = extract_login_creds(self._jar_cookies())
        if developer_id:
            creds.setdefault('developer_id_lite', developer_id)
            creds.setdefault('developerId', developer_id)
        if dashboard_credentials_ready(creds):
            self.creds = creds
            self.status = 'logged_in'
            return True
        self.status = 'failed'
        self.error = '登录凭证不完整，请重新授权' if self.mobile else '登录凭证不完整，请重新扫码登录'
        return False

    def result(self) -> dict:
        return {
            'status': self.status,
            'qr_image': self.qr_image,
            'login_url': self.callback if self.mobile else '',
            'creds': self.creds,
            'error': self.error,
            'developers': self.developers,
        }
