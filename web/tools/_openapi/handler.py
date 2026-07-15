"""QQ 开放平台 API — 扫码登录/数据查询/模板管理/白名单"""

import json
import logging
import os
import time
from urllib.parse import quote, urlparse

from aiohttp import ClientError, ClientSession, ClientTimeout, web
from aiohttp.web_request import FileField

log = logging.getLogger('ElainaBot.web.openapi')

# 旧开放平台 (机器人专用通道) 凭证: uin/developerId/ticket, 供系统插件与旧接口使用
_openapi_user_data: dict = {}
# 新开放平台 (q.qq.com) 凭证: b_token/qticket_lite 等, 与旧平台隔离存储
_openapi_v2_data: dict = {}
_openapi_login_tasks: dict = {}
_openapi_v2_login_tasks: dict = {}
_data_file = ''
_v2_dir = ''
_bot_api = None
_bot_manager = None

_WEBHOOK_ALLOWED_PORTS = {'80', '8080', '443', '8443'}
_AVATAR_MAX_SIZE = 2 * 1024 * 1024


def set_context(base_dir: str, bot_manager=None):
    global _data_file, _v2_dir, _bot_manager
    # 旧平台凭证 (机器人专用通道): web/open/openapi.json
    _data_file = os.path.join(base_dir, 'web', 'open', 'openapi.json')
    # 新平台凭证 (q.qq.com): web/new_open/<user_id>.json, 与旧平台隔离
    _v2_dir = os.path.join(base_dir, 'web', 'new_open')
    _bot_manager = bot_manager
    _load_data()
    _load_v2()


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


def _load_v2():
    """加载新开放平台凭证 (data/open/<user_id>.json)"""
    global _openapi_v2_data
    _openapi_v2_data = {}
    try:
        if not _v2_dir or not os.path.isdir(_v2_dir):
            return
        for name in os.listdir(_v2_dir):
            if not name.endswith('.json'):
                continue
            with open(os.path.join(_v2_dir, name), encoding='utf-8') as f:
                _openapi_v2_data[name[:-5]] = json.load(f)
    except Exception:
        _openapi_v2_data = {}


def _save_v2(user_id):
    try:
        os.makedirs(_v2_dir, exist_ok=True)
        path = os.path.join(_v2_dir, f'{user_id}.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(_openapi_v2_data.get(user_id, {}), f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def _remove_v2(user_id):
    _openapi_v2_data.pop(user_id, None)
    try:
        path = os.path.join(_v2_dir, f'{user_id}.json')
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def _v2_get(user_id):
    return _openapi_v2_data.get(user_id)


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


def _err(msg, status=200, **kwargs):
    return web.json_response({'success': False, 'message': msg, **kwargs}, status=status)


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


def _apply_login(user_id, creds):
    """把一次扫码得到的凭证隔离写入新/旧两个存储。

    新平台 (data/open/): b_token/qticket_lite/qticket/developer_id_lite/uin/skey
    旧平台 (data/openapi.json): uin/developerId/ticket, 供系统插件与旧接口使用
    """
    quid = creds.get('developer_id_lite') or creds.get('developerId') or ''
    uin = creds.get('uin') or ''
    # 新平台隔离存储
    v2 = {'type': 'ok'}
    for k in ('b_token', 'qticket_lite', 'qticket', 'developer_id_lite', 'uin', 'skey', 'p_skey'):
        if creds.get(k):
            v2[k] = creds[k]
    if quid:
        v2['developerId'] = quid
    _openapi_v2_data[user_id] = v2
    _save_v2(user_id)
    # 旧平台存储 (保留已选 appId)
    old = _openapi_user_data.get(user_id) or {}
    old.update(
        {
            'type': 'ok',
            'uin': uin,
            'developerId': quid,
            'ticket': creds.get('qticket') or old.get('ticket', ''),
        }
    )
    _openapi_user_data[user_id] = old
    _save_data()


async def handle_start_login(request: web.Request):
    """旧版面板登录: q.qq.com 机器人专用扫码通道 (qrcode type 777)"""
    api = _get_bot_api()
    if not api:
        return _err('bot_api 模块未加载')
    body = await request.json()
    user_id = body.get('user_id', 'web_user')
    login_data = await api.create_login_qr()
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
    task = _openapi_login_tasks.get(user_id)
    if not task:
        return web.json_response({'success': False, 'status': 'not_started', 'message': '未找到登录任务'})
    qr = task[1]['qr']
    res, cookies = await api.get_qr_login_info(qrcode=qr, return_cookies=True)
    if res.get('code') == 0:
        ld = res.get('data', {}).get('data', {})
        old = _openapi_user_data.get(user_id) or {}
        old.update({'type': 'ok', **ld})
        _openapi_user_data[user_id] = old
        _openapi_login_tasks.pop(user_id, None)
        _save_data()
        from web.tools._bot.api import extract_login_creds

        v2_creds = extract_login_creds(cookies)
        if v2_creds.get('b_token'):
            v2 = _openapi_v2_data.get(user_id) or {'type': 'ok'}
            v2.update(v2_creds)
            _openapi_v2_data[user_id] = v2
            _save_v2(user_id)
        return web.json_response(
            {
                'success': True,
                'status': 'logged_in',
                'data': {'uin': ld.get('uin'), 'appId': ld.get('appId')},
            }
        )
    return web.json_response({'success': True, 'status': 'waiting', 'message': '等待扫码'})


async def handle_v2_start_login(request: web.Request):
    """新版面板登录: q.qq.com 官方 ptlogin 扫码流程"""
    api = _get_bot_api()
    if not api:
        return _err('bot_api 模块未加载')
    body = await request.json()
    user_id = body.get('user_id', 'web_user')
    from web.tools._bot.api import QQScanLogin

    login = QQScanLogin(auto_select=False)
    res = await login.start()
    if res['status'] == 'failed' or not res.get('qr_image'):
        return _err(f'获取二维码失败: {res.get("error") or "未知错误"}')
    _openapi_v2_login_tasks[user_id] = login
    return _ok(qr_image=res['qr_image'], message='请扫描二维码登录')


async def handle_v2_check_login(request: web.Request):
    body = await request.json()
    user_id = body.get('user_id', 'web_user')
    login = _openapi_v2_login_tasks.get(user_id)
    if login is None:
        return web.json_response({'success': False, 'status': 'not_started', 'message': '未找到登录任务'})
    developer_id = str(body.get('developer_id') or '')
    res = await login.select_developer(developer_id) if developer_id else await login.poll()
    status = res['status']
    if status == 'logged_in':
        _apply_login(user_id, res['creds'] or {})
        _openapi_v2_login_tasks.pop(user_id, None)
        ud = _openapi_user_data.get(user_id, {})
        return web.json_response(
            {
                'success': True,
                'status': 'logged_in',
                'data': {'uin': ud.get('uin', ''), 'developerId': ud.get('developerId', '')},
            }
        )
    if status == 'failed':
        _openapi_v2_login_tasks.pop(user_id, None)
        return web.json_response({'success': False, 'status': 'failed', 'message': res.get('error') or '登录失败'})
    payload = {'success': True, 'status': status}
    if status == 'waiting' and res.get('qr_image'):
        payload['qr_image'] = res['qr_image']
    if status == 'selecting':
        payload['developers'] = res.get('developers') or []
        if res.get('error'):
            payload['message'] = res['error']
    return web.json_response(payload)


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
    _remove_v2(user_id)
    _openapi_login_tasks.pop(user_id, None)
    _openapi_v2_login_tasks.pop(user_id, None)
    return _ok(message='登出成功')


# ==================== 机器人数据 ====================


async def handle_get_botlist(request: web.Request):
    body = await request.json()
    api, ud, err = _require_api_and_login(body)
    if err is not None:
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
    if err is not None:
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
    if err is not None:
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
    if err is not None:
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
    if err is not None:
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
    if err is not None:
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
    if err is not None:
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
    if err is not None:
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


handle_batch_add_whitelist = handle_update_whitelist


# ==================== 事件订阅 ====================


async def handle_get_event_list(request: web.Request):
    body = await request.json()
    api, ud, err = _require_api_and_login(body)
    if err is not None:
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
    events.sort(key=lambda e: e.get('weight', 9999))
    return _ok(data={'events': events})


async def handle_get_event_auth_qr(request: web.Request):
    body = await request.json()
    api, ud, err = _require_api_and_login(body)
    if err is not None:
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
    if err is not None:
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
    if err is not None:
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
    if err is not None:
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
    if err is not None:
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


handle_get_webhook_auth_qr = handle_get_event_auth_qr


async def handle_set_webhook(request: web.Request):
    body = await request.json()
    api, ud, err = _require_api_and_login(body)
    if err is not None:
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


# ==================== 新版开放平台 (v2) ====================

_V2_ALLOWED_PREFIXES = ('/cgi-bin/v2/', '/bopen/v2/', '/api/v3/login/', '/api/v1/logout')


def _v2_cookie(ud):
    quid = ud.get('developer_id_lite') or ud.get('developerId') or ''
    parts = []
    if ud.get('uin'):
        parts.append(f'quin={ud["uin"]}')
    if quid:
        parts.extend([f'quid={quid}', f'developerId={quid}', f'developer_id_lite={quid}'])
    if ud.get('b_token'):
        parts.append(f'b-token={ud["b_token"]}')
    if ud.get('qticket_lite'):
        parts.append(f'qticket_lite={ud["qticket_lite"]}')
    if ud.get('qticket'):
        parts.append(f'qticket={ud["qticket"]}')
    if ud.get('skey'):
        parts.append(f'skey={ud["skey"]}')
    if ud.get('p_skey'):
        parts.append(f'p_skey={ud["p_skey"]}')
    return '; '.join(parts)


def _v2_ready(ud):
    required = ('b_token', 'qticket_lite', 'developer_id_lite', 'uin', 'skey')
    return bool(ud and all(ud.get(key) for key in required))


def _v2_relogin_message(result):
    if not isinstance(result, dict):
        return ''
    containers = [result]
    for key in ('data', 'common', 'res'):
        value = result.get(key)
        if isinstance(value, dict):
            containers.append(value)
    codes = {
        value
        for container in containers
        for key in ('code', 'retcode', 'ret')
        if isinstance((value := container.get(key)), int)
    }
    messages = [
        str(value)
        for container in containers
        for key in ('message', 'msg')
        if (value := container.get(key))
    ]
    message = next(
        (text for text in messages if '重新登录' in text or '登录已过期' in text),
        '',
    )
    failed = result.get('success') is False or any(code != 0 for code in codes)
    if failed and (message or codes.intersection({10004, 30018})):
        return message or '登录已过期，请重新登录'
    return ''


async def handle_v2_status(request: web.Request):
    body = await request.json() if request.can_read_body else {}
    ud = _v2_get(body.get('user_id', 'web_user'))
    if not ud:
        return _ok(logged_in=False, ready=False)
    return _ok(
        logged_in=True,
        ready=_v2_ready(ud),
        uin=ud.get('uin', ''),
        developer_id=ud.get('developer_id_lite') or ud.get('developerId') or '',
    )


async def handle_v2_developers(request: web.Request):
    body = await request.json() if request.can_read_body else {}
    user_id = body.get('user_id', 'web_user')
    ud = _v2_get(user_id)
    if not ud:
        return _err('未登录')
    if not _v2_ready(ud):
        return _err('新版开放平台未授权，请重新扫码登录')
    api = _get_bot_api()
    if not api:
        return _err('bot_api 模块未加载')
    result = await api.v2_request(
        'GET',
        '/api/v3/login/bopen/developer_list',
        cookie=_v2_cookie(ud),
        skey=ud.get('skey', ''),
    )
    relogin_message = _v2_relogin_message(result)
    if relogin_message:
        _remove_v2(user_id)
        return _err(relogin_message, relogin=True)
    response = result.get('res') if isinstance(result, dict) else None
    if isinstance(response, dict) and response.get('ret') not in (None, 0):
        return _err(str(response.get('msg') or '获取主体列表失败'))
    from web.tools._bot.api import parse_login_developers

    return _ok(
        developers=parse_login_developers(result),
        current_developer_id=ud.get('developer_id_lite') or ud.get('developerId') or '',
    )


async def handle_v2_switch_developer(request: web.Request):
    body = await request.json()
    user_id = body.get('user_id', 'web_user')
    developer_id = str(body.get('developer_id') or '')
    if not developer_id:
        return _err('请选择开发者主体')
    ud = _v2_get(user_id)
    if not ud:
        return _err('未登录')
    if not _v2_ready(ud):
        return _err('新版开放平台未授权，请重新扫码登录')
    api = _get_bot_api()
    if not api:
        return _err('bot_api 模块未加载')

    list_result = await api.v2_request(
        'GET',
        '/api/v3/login/bopen/developer_list',
        cookie=_v2_cookie(ud),
        skey=ud.get('skey', ''),
    )
    relogin_message = _v2_relogin_message(list_result)
    if relogin_message:
        _remove_v2(user_id)
        return _err(relogin_message, relogin=True)
    from web.tools._bot.api import extract_login_creds, parse_login_developers

    developers = parse_login_developers(list_result)
    if developer_id not in {item['id'] for item in developers}:
        return _err('请选择有效的开发者主体')

    result, cookies = await api.v2_switch_developer(
        developer_id,
        cookie=_v2_cookie(ud),
        skey=ud.get('skey', ''),
    )
    if not isinstance(result, dict):
        return _err('切换主体失败')
    ret = result.get('ret')
    if ret not in (None, 0):
        return _err(str(result.get('msg') or '切换主体失败'))

    ud.update(extract_login_creds(cookies))
    ud['developer_id_lite'] = developer_id
    ud['developerId'] = developer_id
    _save_v2(user_id)
    old = _openapi_user_data.get(user_id) or {}
    old['developerId'] = developer_id
    if ud.get('qticket'):
        old['ticket'] = ud['qticket']
    _openapi_user_data[user_id] = old
    _save_data()
    return _ok(developer_id=developer_id, message='主体切换成功')


async def handle_v2_proxy(request: web.Request):
    """新版开放平台接口通用代理: 转发前端与 q.qq.com 完全一致的请求"""
    body = await request.json()
    api = _get_bot_api()
    if not api:
        return _err('bot_api 模块未加载')
    user_id = body.get('user_id', 'web_user')
    ud = _v2_get(user_id)
    if not ud:
        return _err('未登录')
    if not _v2_ready(ud):
        return _err('新版开放平台未授权，请重新扫码登录', status=200)
    path = body.get('path', '')
    if not any(path.startswith(p) for p in _V2_ALLOWED_PREFIXES):
        return _err('非法的接口路径')
    method = body.get('method', 'POST')
    result = await api.v2_request(
        method,
        path,
        cookie=_v2_cookie(ud),
        skey=ud.get('skey', ''),
        data=body.get('payload'),
        params=body.get('params'),
    )
    if path == '/cgi-bin/v2/datareport/export' and isinstance(result, dict):
        content = result.get('data')
        if result.get('retcode') == 0 and isinstance(content, str):
            if not content.startswith('\ufeff'):
                content = f'\ufeff{content}'
            return web.Response(
                body=content.encode(),
                headers={
                    'Content-Type': 'text/csv; charset=utf-8',
                    'Content-Disposition': 'attachment; filename=report.csv',
                },
            )
    if isinstance(result, dict) and result.get('_binary'):
        return web.Response(
            body=result['content'],
            headers={
                'Content-Type': result['content_type'],
                'Content-Disposition': result.get('disposition') or 'attachment',
            },
        )
    relogin_message = _v2_relogin_message(result)
    if relogin_message:
        _remove_v2(user_id)
        return _err(relogin_message, relogin=True)
    return web.json_response(result)


async def handle_v2_upload_avatar(request: web.Request):
    form = await request.post()
    user_id = str(form.get('user_id', 'web_user'))
    file = form.get('file')
    if not isinstance(file, FileField) or not file.content_type.startswith('image/'):
        return _err('请选择图片文件')
    content = file.file.read(_AVATAR_MAX_SIZE + 1)
    if len(content) > _AVATAR_MAX_SIZE:
        return _err('图片需小于 2MB')

    api = _get_bot_api()
    if not api:
        return _err('bot_api 模块未加载')
    ud = _v2_get(user_id)
    if not ud:
        return _err('未登录')
    if not _v2_ready(ud):
        return _err('新版开放平台未授权，请重新扫码登录')
    try:
        bot_appid = int(str(form.get('bot_appid', 0)))
    except ValueError:
        return _err('机器人 AppID 无效')

    result = await api.v2_request(
        'POST',
        '/cgi-bin/v2/resource/pre_upload',
        cookie=_v2_cookie(ud),
        skey=ud.get('skey', ''),
        data={
            'type': 2,
            'bot_appid': bot_appid,
        },
    )
    if not isinstance(result, dict):
        return _err('获取上传地址失败')
    relogin_message = _v2_relogin_message(result)
    if relogin_message:
        _remove_v2(user_id)
        return _err(relogin_message, relogin=True)
    if result.get('retcode') not in (None, 0) or result.get('code') not in (None, 0):
        return _err(str(result.get('msg') or result.get('message') or '获取上传地址失败'))
    data = result.get('data')
    payload = data if isinstance(data, dict) else result
    upload_url = result.get('upload_url') or payload.get('upload_url')
    upload_id = result.get('upload_id') or payload.get('upload_id')
    parsed = urlparse(upload_url if isinstance(upload_url, str) else '')
    if parsed.scheme != 'https' or not (parsed.hostname or '').endswith('.myqcloud.com') or not upload_id:
        return _err('获取上传地址失败')

    try:
        async with (
            ClientSession(timeout=ClientTimeout(total=30)) as session,
            session.put(
                upload_url,
                data=content,
                headers={
                    'Content-Type': file.content_type,
                    'x-cos-forbid-overwrite': 'true',
                },
            ) as response,
        ):
            if response.status < 200 or response.status >= 300:
                return _err(f'图片上传失败: HTTP {response.status}')
    except (ClientError, TimeoutError):
        return _err('图片上传失败')
    return _ok(upload_id=upload_id)
