"""QQ 开放平台 API (aiohttp 版) — 扫码登录/数据查询/模板管理/白名单"""

import os
import re
import json
import time
import logging
import asyncio

from aiohttp import web

log = logging.getLogger('ElainaBot.web.openapi')

_openapi_user_data = {}
_openapi_login_tasks = {}
_data_file = ''
_bot_api = None


def set_context(base_dir: str):
    global _data_file
    _data_file = os.path.join(base_dir, 'data', 'openapi.json')
    _load_data()


def _load_data():
    global _openapi_user_data
    try:
        if os.path.exists(_data_file):
            with open(_data_file, 'r', encoding='utf-8') as f:
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
            from web.tools.bot_api import get_bot_api
            _bot_api = get_bot_api()
        except ImportError:
            _bot_api = None
    return _bot_api


def _err(msg, status=400):
    return web.json_response({'success': False, 'message': msg}, status=status)


def _ok(**kwargs):
    return web.json_response({'success': True, **kwargs})


# ==================== 登录 ====================

async def handle_start_login(request: web.Request):
    api = _get_bot_api()
    if not api:
        return _err('bot_api 模块未加载')
    body = await request.json()
    user_id = body.get('user_id', 'web_user')
    login_data = await api.create_login_qr()
    if login_data.get('status') != 'success' or not login_data.get('url') or not login_data.get('qr'):
        return _err('获取二维码失败')
    _openapi_login_tasks[user_id] = (time.time(), {'qr': login_data['qr']})
    return _ok(login_url=login_data['url'], qr_code=login_data['qr'], message='请扫描二维码登录')


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
        return web.json_response({'success': True, 'status': 'logged_in',
                                  'data': {'uin': ld.get('uin'), 'appId': ld.get('appId')}})
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
    api = _get_bot_api()
    if not api:
        return _err('bot_api 模块未加载')
    body = await request.json()
    ud = _check_login(body.get('user_id', 'web_user'))
    if not ud:
        return _err('未登录')
    res = await api.get_bot_list(uin=ud.get('uin'), quid=ud.get('developerId'), ticket=ud.get('ticket'))
    if res.get('code') != 0:
        return _err('登录失效')
    return _ok(data={'uin': ud.get('uin'), 'apps': res.get('data', {}).get('apps', [])})


async def handle_get_botdata(request: web.Request):
    api = _get_bot_api()
    if not api:
        return _err('bot_api 模块未加载')
    body = await request.json()
    ud = _check_login(body.get('user_id', 'web_user'))
    if not ud:
        return _err('未登录')
    appid = body.get('appid') or ud.get('appId')
    try:
        d1 = await api.get_bot_data(uin=ud.get('uin'), quid=ud.get('developerId'), ticket=ud.get('ticket'), appid=appid, data_type=1)
        d2 = await api.get_bot_data(uin=ud.get('uin'), quid=ud.get('developerId'), ticket=ud.get('ticket'), appid=appid, data_type=2)
        d3 = await api.get_bot_data(uin=ud.get('uin'), quid=ud.get('developerId'), ticket=ud.get('ticket'), appid=appid, data_type=3)
        msg_data = d1.get('data', {}).get('msg_data', [])
        group_data = d2.get('data', {}).get('group_data', [])
        friend_data = d3.get('data', {}).get('friend_data', [])
        days = min(body.get('days', 30), min(len(msg_data), len(group_data), len(friend_data)))
        processed = []
        total_up = 0
        for i in range(days):
            m = msg_data[i] if i < len(msg_data) else {}
            g = group_data[i] if i < len(group_data) else {}
            fr = friend_data[i] if i < len(friend_data) else {}
            dd = {
                'date': m.get('报告日期', '0'), 'up_messages': m.get('上行消息量', '0'),
                'up_users': m.get('上行消息人数', '0'), 'down_messages': m.get('下行消息量', '0'),
                'current_groups': g.get('现有群组', '0'), 'new_groups': g.get('新增群组', '0'),
                'current_friends': fr.get('现有好友数', '0'), 'new_friends': fr.get('新增好友数', '0'),
            }
            processed.append(dd)
            total_up += int(dd['up_users'])
        avg_dau = round(total_up / 30, 2) if msg_data else 0
        return _ok(data={'uin': ud.get('uin'), 'appid': appid, 'avg_dau': avg_dau, 'days_data': processed})
    except Exception as e:
        return _err(str(e))


async def handle_get_notifications(request: web.Request):
    api = _get_bot_api()
    if not api:
        return _err('bot_api 模块未加载')
    body = await request.json()
    ud = _check_login(body.get('user_id', 'web_user'))
    if not ud:
        return _err('未登录')
    res = await api.get_private_messages(uin=ud.get('uin'), quid=ud.get('developerId'), ticket=ud.get('ticket'))
    if res.get('code', 0) != 0:
        return _err('获取通知失败')
    msgs = [{'content': m.get('content', ''), 'send_time': m.get('send_time', ''),
             'type': m.get('type', ''), 'title': m.get('title', '')}
            for m in res.get('messages', [])[:20]]
    return _ok(data={'messages': msgs})


# ==================== 模板管理 ====================

async def handle_get_templates(request: web.Request):
    api = _get_bot_api()
    if not api:
        return _err('bot_api 模块未加载')
    body = await request.json()
    ud = _check_login(body.get('user_id', 'web_user'))
    if not ud:
        return _err('未登录')
    appid = body.get('appid') or ud.get('appId')
    res = await api.get_message_templates(uin=ud.get('uin'), quid=ud.get('developerId'),
                                          ticket=ud.get('ticket'), appid=appid)
    if res.get('retcode', 0) != 0 and res.get('code', 0) not in (0, 200):
        return _err('获取模板失败')
    templates = [{'id': t.get('模板id', ''), 'name': t.get('模板名称', ''),
                  'type': t.get('模板类型', ''), 'status': t.get('模板状态', ''),
                  'content': t.get('模板内容', '')}
                 for t in res.get('data', {}).get('list', [])]
    return _ok(data={'templates': templates, 'total': len(templates)})


async def handle_import_templates(request: web.Request):
    return _err('模板导入功能迁移中')


# ==================== 白名单 ====================

async def handle_get_whitelist(request: web.Request):
    api = _get_bot_api()
    if not api:
        return _err('bot_api 模块未加载')
    body = await request.json()
    ud = _check_login(body.get('user_id', 'web_user'))
    if not ud:
        return _err('未登录')
    appid = body.get('appid') or ud.get('appId')
    if not appid:
        return _err('缺少 AppID')
    res = await api.get_white_list(appid=appid, uin=ud.get('uin'), uid=ud.get('developerId'), ticket=ud.get('ticket'))
    if res.get('code', 0) != 0:
        return _err('获取白名单失败')
    ips = [{'ip': ip.get('ip', '') if isinstance(ip, dict) else ip,
            'description': ip.get('desc', '') if isinstance(ip, dict) else ''}
           for ip in res.get('data', [])]
    return _ok(data={'ip_list': ips, 'total': len(ips)})


async def handle_update_whitelist(request: web.Request):
    api = _get_bot_api()
    if not api:
        return _err('bot_api 模块未加载')
    body = await request.json()
    ud = _check_login(body.get('user_id', 'web_user'))
    if not ud:
        return _err('未登录')
    appid = body.get('appid') or ud.get('appId')
    ip = body.get('ip', '').strip()
    action = body.get('action', '')
    if not appid or not ip or action not in ('add', 'del'):
        return _err('参数不完整')
    if not re.match(r'^(\d{1,3}\.){3}\d{1,3}$', ip):
        return _err('IP 格式无效')
    qr = await api.create_white_login_qr(appid=appid, uin=ud.get('uin'), uid=ud.get('developerId'), ticket=ud.get('ticket'))
    if qr.get('code', 0) != 0 or not qr.get('qrcode'):
        return _err('创建授权失败')
    res = await api.update_white_list(appid=appid, uin=ud.get('uin'), uid=ud.get('developerId'),
                                      ticket=ud.get('ticket'), qrcode=qr['qrcode'], ip=ip, action=action)
    if res.get('code', 0) != 0:
        return _err(res.get('msg') or '操作失败')
    return _ok(message=f'IP {"添加" if action == "add" else "删除"}成功')
