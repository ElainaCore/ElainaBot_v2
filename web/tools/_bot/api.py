"""QQ 开放平台 HTTP API 封装"""

import json
import re
import socket
from datetime import datetime
from http.cookies import CookieError, SimpleCookie
from urllib.parse import urlencode

import aiohttp

from web.tools._bot.qq_http import (
    _BASE_HEADERS,
    _BOT_HEADERS,
    _QQ_HEADERS,
    _TIMEOUT,
    decode_body,
    qq_bkn,
)
from web.tools._bot.scan_login import (
    QQScanLogin,
    extract_login_creds,
    parse_login_developers,
    parse_ptlogin_versions,
    parse_ptuicb,
)

__all__ = [
    'QQBotAPI',
    'QQScanLogin',
    'extract_login_creds',
    'get_bot_api',
    'parse_login_developers',
    'parse_ptlogin_versions',
    'parse_ptuicb',
    'qq_bkn',
]

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

    _decode = staticmethod(decode_body)

    async def _request(self, method, url, uin='', quid='', ticket='', data=None, extra_headers=None, return_cookies=False):
        try:
            headers = _BASE_HEADERS.copy()
            if uin or quid or ticket:
                headers['Cookie'] = self._build_cookie(uin, quid, ticket)
            if extra_headers:
                headers.update(extra_headers)
            conn = aiohttp.TCPConnector(family=socket.AF_INET)
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
            conn = aiohttp.TCPConnector(family=socket.AF_INET)
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
            conn = aiohttp.TCPConnector(family=socket.AF_INET)
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
