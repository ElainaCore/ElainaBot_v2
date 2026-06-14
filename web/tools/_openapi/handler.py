"""QQ 开放平台 API — 扫码登录/数据查询/模板管理/白名单"""

import json
import logging
import os
import time
from urllib.parse import quote

from aiohttp import web

log = logging.getLogger('ElainaBot.web.openapi')

_openapi_user_data: dict = {}
_openapi_login_tasks: dict = {}
_data_file = ''
_bot_api = None
_bot_manager = None

_WEBHOOK_ALLOWED_PORTS = {'80', '8080', '443', '8443'}


def set_context(base_dir: str, bot_manager=None):
    global _data_file, _bot_manager
    _data_file = os.path.join(base_dir, 'data', 'openapi.json')
    _bot_manager = bot_manager
    _load_data()


def _load_data():
    global _openapi_user_data
    try:
        if os.path.exists(_data_file):
            with open(_data_file, encoding='utf-8') as f:
                _openapi_user_data = json.load(f)
    except Exception:
        _openapi_user_data = {}


def _save_data():
    try:
        os.makedirs(os.path.dirname(_data_file), exist_ok=True)
        with open(_data_file, 'w', encoding='utf-8') as f:
            json.dump(_openapi_user_data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def _get_user_data(user_id):
    return _openapi_user_data.get(user_id)


def _check_login(user_id='web_user'):
    return _openapi_user_data.get(user_id)


def _get_bot_api():
    global _bot_api
    if _bot_api is None:
        try:
            from web.tools._bot.api import get_bot_api

            _bot_api = get_bot_api()
        except ImportError:
            _bot_api = None
    return _bot_api


def _err(msg, status=200):
    return web.json_response({'success': False, 'message': msg}, status=status)


def _ok(**kwargs):
    return web.json_response({'success': True, **kwargs})


def _require_api_and_login(body):
    """api + 登录状态校验, 返回 (api, ud, err_resp)"""
    api = _get_bot_api()
    if not api:
        return None, None, _err('bot_api 模块未加载')
    ud = _check_login(body.get('user_id', 'web_user'))
    if not ud:
        return api, None, _err('未登录')
    return api, ud, None


# ==================== 登录 ====================


async def handle_start_login(request: web.Request):
    api = _get_bot_api()
    if not api:
        return _err('bot_api 模块未加载')
    body = await request.json()
    user_id = body.get('user_id', 'web_user')
    login_data = await api.create_login_qr()
    log.info(f'[OpenAPI] create_login_qr 返回: {login_data}')
    if login_data.get('status') != 'success' or not login_data.get('url') or not login_data.get('qr'):
        return _err(f'获取二维码失败: {login_data.get("message", str(login_data))}')
    _openapi_login_tasks[user_id] = (time.time(), {'qr': login_data['qr']})
    return _ok(
        login_url=login_data['url'],
        qr_code=login_data['qr'],
        message='请扫描二维码登录',
    )


async def handle_check_login(request: web.Request):
    api = _get_bot_api()
    if not api:
        return _err('bot_api 模块未加载')
    body = await request.json()
    user_id = body.get('user_id', 'web_user')
    if user_id not in _openapi_login_tasks:
        return web.json_response({'success': False, 'status': 'not_started', 'message': '未找到登录任务'})
    qr = _openapi_login_tasks[user_id][1]['qr']
    res = await api.get_qr_login_info(qrcode=qr)
    if res.get('code') == 0:
        ld = res.get('data', {}).get('data', {})
        _openapi_user_data[user_id] = {'type': 'ok', **ld}
        _openapi_login_tasks.pop(user_id, None)
        _save_data()
        return web.json_response(
            {
                'success': True,
                'status': 'logged_in',
                'data': {'uin': ld.get('uin'), 'appId': ld.get('appId')},
            }
        )
    return web.json_response({'success': True, 'status': 'waiting', 'message': '等待扫码'})


async def handle_get_login_status(request: web.Request):
    body = await request.json() if request.can_read_body else {}
    user_id = body.get('user_id', 'web_user')
    ud = _check_login(user_id)
    if ud:
        return _ok(logged_in=True, uin=ud.get('uin', ''), appid=ud.get('appId', ''))
    return _ok(logged_in=False)


async def handle_logout(request: web.Request):
    body = await request.json()
    user_id = body.get('user_id', 'web_user')
    _openapi_user_data.pop(user_id, None)
    _save_data()
    return _ok(message='登出成功')


# ==================== 机器人数据 ====================


async def handle_get_botlist(request: web.Request):
    body = await request.json()
    api, ud, err = _require_api_and_login(body)
    if err:
        return err
    res = await api.get_bot_list(uin=ud.get('uin'), quid=ud.get('developerId'), ticket=ud.get('ticket'))
    if res.get('code') != 0:
        return _err('登录失效')
    apps = res.get('data', {}).get('apps', [])
    log.info(f'[OpenAPI] botlist apps 样本: {apps[:1] if apps else "空"}')
    return _ok(data={'uin': ud.get('uin'), 'apps': apps})


async def handle_get_botdata(request: web.Request):
    body = await request.json()
    api, ud, err = _require_api_and_login(body)
    if err:
        return err
    appid = body.get('appid') or ud.get('appId')
    try:
        cred = dict(
            uin=ud.get('uin'),
            quid=ud.get('developerId'),
            ticket=ud.get('ticket'),
            appid=appid,
        )
        d1 = await api.get_bot_data(**cred, data_type=1)
        d2 = await api.get_bot_data(**cred, data_type=2)
        d3 = await api.get_bot_data(**cred, data_type=3)
        if any(x.get('retcode', 0) != 0 for x in [d1, d2, d3]):
            return _err('登录失效或获取数据失败')
        msg_data = d1.get('data', {}).get('msg_data', [])
        group_data = d2.get('data', {}).get('group_data', [])
        friend_data = d3.get('data', {}).get('friend_data', [])
        max_days = min(len(msg_data), len(group_data), len(friend_data))
        days = min(body.get('days', 30), max_days)
        processed = []
        total_up = 0
        for i in range(days):
            m = msg_data[i] if i < len(msg_data) else {}
            g = group_data[i] if i < len(group_data) else {}
            fr = friend_data[i] if i < len(friend_data) else {}
            dd = {
                'date': m.get('报告日期', '0'),
                'up_messages': m.get('上行消息量', '0'),
                'up_users': m.get('上行消息人数', '0'),
                'down_messages': m.get('下行消息量', '0'),
                'total_messages': m.get('总消息量', '0'),
                'current_groups': g.get('现有群组', '0'),
                'used_groups': g.get('已使用群组', '0'),
                'new_groups': g.get('新增群组', '0'),
                'removed_groups': g.get('移除群组', '0'),
                'current_friends': fr.get('现有好友数', '0'),
                'used_friends': fr.get('已使用好友数', '0'),
                'new_friends': fr.get('新增好友数', '0'),
                'removed_friends': fr.get('移除好友数', '0'),
            }
            processed.append(dd)
            total_up += int(dd['up_users'])
        avg_dau = round(total_up / 30, 2) if msg_data else 0
        return _ok(
            data={
                'uin': ud.get('uin'),
                'appid': appid,
                'avg_dau': avg_dau,
                'days_data': processed,
            }
        )
    except Exception as e:
        return _err(str(e))


async def handle_get_notifications(request: web.Request):
    body = await request.json()
    api, ud, err = _require_api_and_login(body)
    if err:
        return err
    res = await api.get_private_messages(uin=ud.get('uin'), quid=ud.get('developerId'), ticket=ud.get('ticket'))
    if res.get('code', 0) != 0:
        return _err('获取通知失败')
    msgs = [
        {
            'content': m.get('content', ''),
            'send_time': m.get('send_time', ''),
            'type': m.get('type', ''),
            'title': m.get('title', ''),
        }
        for m in res.get('messages', [])[:20]
    ]
    return _ok(data={'messages': msgs})


# ==================== 验证登录 ====================


async def handle_verify_saved_login(request: web.Request):
    api = _get_bot_api()
    if not api:
        return _err('bot_api 模块未加载')
    body = await request.json()
    user_id = body.get('user_id', 'web_user')
    # 此处不用 _require_api_and_login, 因为需见未登录时也返回 valid=False
    ud = _openapi_user_data.get(user_id)
    if not ud:
        return _ok(valid=False, message='没有保存的登录信息')
    try:
        res = await api.get_bot_list(uin=ud.get('uin'), quid=ud.get('developerId'), ticket=ud.get('ticket'))
        if res.get('code') == 0:
            return _ok(
                valid=True,
                data={
                    'uin': ud.get('uin'),
                    'appId': ud.get('appId'),
                    'developerId': ud.get('developerId'),
                },
                message='登录状态有效',
            )
    except Exception:
        pass
    _openapi_user_data.pop(user_id, None)
    _save_data()
    return _ok(valid=False, message='登录状态已失效')


# ==================== 白名单 ====================


async def handle_get_whitelist(request: web.Request):
    body = await request.json()
    api, ud, err = _require_api_and_login(body)
    if err:
        return err
    appid = body.get('appid') or ud.get('appId')
    if not appid:
        return _err('缺少 AppID')
    res = await api.get_white_list(
        appid=appid,
        uin=ud.get('uin'),
        uid=ud.get('developerId'),
        ticket=ud.get('ticket'),
    )
    if res.get('code', 0) != 0:
        return _err('获取白名单失败')
    ips = [
        {
            'ip': ip.get('ip', '') if isinstance(ip, dict) else ip,
            'description': ip.get('desc', '') if isinstance(ip, dict) else '',
        }
        for ip in res.get('data', [])
    ]
    return _ok(data={'ip_list': ips, 'total': len(ips)})


async def _batch_whitelist_op(body, action='add'):
    """白名单批量操作公共逻辑"""
    api, ud, err = _require_api_and_login(body)
    if err:
        return err
    appid = body.get('appid') or ud.get('appId')
    qrcode, ip_list = body.get('qrcode', ''), body.get('ip_list', [])
    if not all([appid, qrcode, ip_list]):
        return _err('参数不完整')
    cred = dict(
        appid=appid,
        uin=ud.get('uin'),
        uid=ud.get('developerId'),
        ticket=ud.get('ticket'),
        qrcode=qrcode,
    )
    success_count, failed_ips = 0, []
    for ip in ip_list:
        res = await api.update_white_list(**cred, ip=ip, action=action)
        if res.get('code', 0) == 0:
            success_count += 1
        else:
            failed_ips.append(ip)
    return _ok(
        message=f'成功 {success_count} 个，失败 {len(failed_ips)} 个',
        data={
            'success_count': success_count,
            'failed_count': len(failed_ips),
            'failed_ips': failed_ips,
        },
    )


async def handle_update_whitelist(request: web.Request):
    return await _batch_whitelist_op(await request.json(), 'add')


async def handle_get_delete_qr(request: web.Request):
    body = await request.json()
    api, ud, err = _require_api_and_login(body)
    if err:
        return err
    appid = body.get('appid') or ud.get('appId')
    if not appid:
        return _err('缺少 AppID')
    qr_result = await api.create_white_login_qr(
        appid=appid,
        uin=ud.get('uin'),
        uid=ud.get('developerId'),
        ticket=ud.get('ticket'),
    )
    if qr_result.get('code', 0) != 0:
        return _err('创建授权二维码失败')
    qrcode, qr_url = qr_result.get('qrcode', ''), qr_result.get('url', '')
    if not qrcode or not qr_url:
        return _err('获取授权二维码失败')
    qr_img = f'https://api.2dcode.biz/v1/create-qr-code?data={quote(qr_url)}'
    return _ok(qrcode=qrcode, url=qr_img, message='获取授权二维码成功')


async def handle_check_delete_auth(request: web.Request):
    body = await request.json()
    api, ud, err = _require_api_and_login(body)
    if err:
        return err
    appid = body.get('appid') or ud.get('appId')
    qrcode = body.get('qrcode', '')
    if not appid or not qrcode:
        return _err('缺少必要参数')
    auth_result = await api.verify_qr_auth(
        appid=appid,
        uin=ud.get('uin'),
        uid=ud.get('developerId'),
        ticket=ud.get('ticket'),
        qrcode=qrcode,
    )
    authorized = auth_result.get('code', 0) == 0
    return _ok(authorized=authorized, message='授权成功' if authorized else '等待授权中')


async def handle_execute_delete_ip(request: web.Request):
    body = await request.json()
    api, ud, err = _require_api_and_login(body)
    if err:
        return err
    appid = body.get('appid') or ud.get('appId')
    ip, qrcode = body.get('ip', '').strip(), body.get('qrcode', '')
    if not all([appid, ip, qrcode]):
        return _err('缺少必要参数')
    res = await api.update_white_list(
        appid=appid,
        uin=ud.get('uin'),
        uid=ud.get('developerId'),
        ticket=ud.get('ticket'),
        qrcode=qrcode,
        ip=ip,
        action='del',
    )
    if res.get('code', 0) != 0:
        return _err(res.get('msg') or '删除 IP 失败')
    return _ok(message='IP 删除成功', data={'ip': ip, 'appid': appid})


async def handle_batch_add_whitelist(request: web.Request):
    return await _batch_whitelist_op(await request.json(), 'add')


# ==================== 事件订阅 ====================

# 开放平台列表接口不会返回的全量事件, 手动补充以便订阅
_EXTRA_EVENTS = [
    {
        'id': 'GROUP_MESSAGE_CREATE',
        'name': '群消息事件 (全量)',
        'description': '群消息事件',
        'type': '群事件',
        'is_subscribed': False,
        'weight': 200,
    },
]


async def handle_get_event_list(request: web.Request):
    body = await request.json()
    api, ud, err = _require_api_and_login(body)
    if err:
        return err
    appid = body.get('appid') or ud.get('appId')
    if not appid:
        return _err('缺少 AppID')
    res = await api.get_event_list(
        appid=appid,
        uin=ud.get('uin'),
        uid=ud.get('developerId'),
        ticket=ud.get('ticket'),
    )
    if res.get('code', 0) != 0:
        return _err(res.get('msg') or '获取事件列表失败')
    events = res.get('data', [])
    existing = {e.get('id') for e in events}
    for extra in _EXTRA_EVENTS:
        if extra['id'] not in existing:
            events.append(dict(extra))
    events.sort(key=lambda e: e.get('weight', 9999))
    return _ok(data={'events': events})


async def handle_get_event_auth_qr(request: web.Request):
    body = await request.json()
    api, ud, err = _require_api_and_login(body)
    if err:
        return err
    appid = body.get('appid') or ud.get('appId')
    if not appid:
        return _err('缺少 AppID')
    qr_result = await api.create_white_login_qr(
        appid=appid,
        uin=ud.get('uin'),
        uid=ud.get('developerId'),
        ticket=ud.get('ticket'),
        qr_type=50,
    )
    if qr_result.get('code', 0) != 0:
        return _err('创建授权二维码失败')
    qrcode, qr_url = qr_result.get('qrcode', ''), qr_result.get('url', '')
    if not qrcode or not qr_url:
        return _err('获取授权二维码失败')
    qr_img = f'https://api.2dcode.biz/v1/create-qr-code?data={quote(qr_url)}'
    return _ok(qrcode=qrcode, url=qr_img, message='获取授权二维码成功')


async def handle_modify_event_subscription(request: web.Request):
    body = await request.json()
    api, ud, err = _require_api_and_login(body)
    if err:
        return err
    appid = body.get('appid') or ud.get('appId')
    qrcode = body.get('qrcode', '')
    event_ids = body.get('event_ids')
    if not appid or not qrcode or event_ids is None:
        return _err('缺少必要参数')
    res = await api.modify_event_subscription(
        appid=appid,
        event_ids=event_ids,
        qrcode=qrcode,
        uin=ud.get('uin'),
        uid=ud.get('developerId'),
        ticket=ud.get('ticket'),
    )
    if res.get('code', 0) != 0:
        return _err(res.get('msg') or '修改订阅失败')
    return _ok(message='订阅更新成功')


# ==================== 回调地址 (Webhook) ====================


async def handle_get_webhook(request: web.Request):
    body = await request.json()
    api, ud, err = _require_api_and_login(body)
    if err:
        return err
    appid = body.get('appid') or ud.get('appId')
    if not appid:
        return _err('缺少 AppID')
    res = await api.get_webhook(
        appid=appid,
        uin=ud.get('uin'),
        uid=ud.get('developerId'),
        ticket=ud.get('ticket'),
    )
    if res.get('code', 0) != 0:
        return _err(res.get('msg') or '获取回调地址失败')
    return _ok(data={'webhook_url': res.get('data', {}).get('webhook_url', '')})


async def handle_webhook_suggest(request: web.Request):
    """当本面板端口为 80/8080/443/8443 且框架本地存在该 appid 机器人时, 返回可自动填入的本机回调地址"""
    body = await request.json()
    api, ud, err = _require_api_and_login(body)
    if err:
        return err
    appid = body.get('appid') or ud.get('appId')
    if not appid:
        return _err('缺少 AppID')
    has_bot = bool(_bot_manager and str(appid) in _bot_manager._bots)
    scheme = request.headers.get('X-Forwarded-Proto', request.scheme)
    host = request.headers.get('X-Forwarded-Host', request.host)
    port = host.split(':')[1] if ':' in host else ('443' if scheme == 'https' else '80')
    port_allowed = port in _WEBHOOK_ALLOWED_PORTS
    url = f'{scheme}://{host}/?appid={appid}'
    return _ok(
        available=has_bot and port_allowed,
        url=url,
        has_bot=has_bot,
        port_allowed=port_allowed,
    )


async def handle_check_webhook(request: web.Request):
    body = await request.json()
    api, ud, err = _require_api_and_login(body)
    if err:
        return err
    appid = body.get('appid') or ud.get('appId')
    webhook_url = body.get('webhook_url', '')
    if not appid or not webhook_url:
        return _err('缺少必要参数')
    res = await api.check_webhook(
        appid=appid,
        webhook_url=webhook_url,
        uin=ud.get('uin'),
        uid=ud.get('developerId'),
        ticket=ud.get('ticket'),
    )
    valid = res.get('code', 0) == 0
    return _ok(valid=valid, message=res.get('msg') or ('地址校验通过' if valid else '地址校验未通过'))


async def handle_get_webhook_auth_qr(request: web.Request):
    body = await request.json()
    api, ud, err = _require_api_and_login(body)
    if err:
        return err
    appid = body.get('appid') or ud.get('appId')
    if not appid:
        return _err('缺少 AppID')
    qr_result = await api.create_white_login_qr(
        appid=appid,
        uin=ud.get('uin'),
        uid=ud.get('developerId'),
        ticket=ud.get('ticket'),
        qr_type=50,
    )
    if qr_result.get('code', 0) != 0:
        return _err('创建授权二维码失败')
    qrcode, qr_url = qr_result.get('qrcode', ''), qr_result.get('url', '')
    if not qrcode or not qr_url:
        return _err('获取授权二维码失败')
    qr_img = f'https://api.2dcode.biz/v1/create-qr-code?data={quote(qr_url)}'
    return _ok(qrcode=qrcode, url=qr_img, message='获取授权二维码成功')


async def handle_set_webhook(request: web.Request):
    body = await request.json()
    api, ud, err = _require_api_and_login(body)
    if err:
        return err
    appid = body.get('appid') or ud.get('appId')
    qrcode = body.get('qrcode', '')
    webhook_url = body.get('webhook_url', '')
    if not appid or not qrcode or not webhook_url:
        return _err('缺少必要参数')
    res = await api.set_webhook(
        appid=appid,
        webhook_url=webhook_url,
        qrcode=qrcode,
        uin=ud.get('uin'),
        uid=ud.get('developerId'),
        ticket=ud.get('ticket'),
    )
    if res.get('code', 0) != 0:
        return _err(res.get('msg') or '设置回调地址失败')
    return _ok(message='回调地址设置成功')
