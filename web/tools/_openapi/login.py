"""QQ 开放平台 — 扫码登录 / 登录状态处理器"""

import time

from aiohttp import web

import web.tools._openapi.handler as h


async def handle_start_login(request: web.Request):
    """旧版面板登录: q.qq.com 机器人专用扫码通道 (qrcode type 777)"""
    api = h._get_bot_api()
    if not api:
        return h._err('bot_api 模块未加载')
    body = await request.json()
    user_id = body.get('user_id', 'web_user')
    login_data = await api.create_login_qr()
    if login_data.get('status') != 'success' or not login_data.get('url') or not login_data.get('qr'):
        return h._err(f'获取二维码失败: {login_data.get("message", str(login_data))}')
    h._openapi_login_tasks[user_id] = (time.time(), {'qr': login_data['qr']})
    return h._ok(
        login_url=login_data['url'],
        qr_code=login_data['qr'],
        message='请扫描二维码登录',
    )


async def handle_check_login(request: web.Request):
    api = h._get_bot_api()
    if not api:
        return h._err('bot_api 模块未加载')
    body = await request.json()
    user_id = body.get('user_id', 'web_user')
    task = h._openapi_login_tasks.get(user_id)
    if not task:
        return web.json_response({'success': False, 'status': 'not_started', 'message': '未找到登录任务'})
    qr = task[1]['qr']
    res, cookies = await api.get_qr_login_info(qrcode=qr, return_cookies=True)
    if res.get('code') == 0:
        ld = res.get('data', {}).get('data', {})
        old = h._openapi_user_data.get(user_id) or {}
        old.update({'type': 'ok', **ld})
        h._openapi_user_data[user_id] = old
        h._openapi_login_tasks.pop(user_id, None)
        h._save_data()
        from web.tools._bot.api import extract_login_creds

        v2_creds = extract_login_creds(cookies)
        if v2_creds.get('b_token'):
            v2 = h._openapi_v2_data.get(user_id) or {'type': 'ok'}
            v2.update(v2_creds)
            h._openapi_v2_data[user_id] = v2
            h._save_v2(user_id)
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
    api = h._get_bot_api()
    if not api:
        return h._err('bot_api 模块未加载')
    body = await request.json()
    user_id = body.get('user_id', 'web_user')
    from web.tools._bot.api import QQScanLogin

    login = QQScanLogin(auto_select=False)
    res = await login.start()
    if res['status'] == 'failed' or not res.get('qr_image'):
        return h._err(f'获取二维码失败: {res.get("error") or "未知错误"}')
    h._openapi_v2_login_tasks[user_id] = login
    return h._ok(qr_image=res['qr_image'], message='请扫描二维码登录')


async def handle_v2_check_login(request: web.Request):
    body = await request.json()
    user_id = body.get('user_id', 'web_user')
    login = h._openapi_v2_login_tasks.get(user_id)
    if login is None:
        return web.json_response({'success': False, 'status': 'not_started', 'message': '未找到登录任务'})
    developer_id = str(body.get('developer_id') or '')
    res = await login.select_developer(developer_id) if developer_id else await login.poll()
    status = res['status']
    if status == 'logged_in':
        h._apply_login(user_id, res['creds'] or {})
        h._openapi_v2_login_tasks.pop(user_id, None)
        ud = h._openapi_user_data.get(user_id, {})
        return web.json_response(
            {
                'success': True,
                'status': 'logged_in',
                'data': {'uin': ud.get('uin', ''), 'developerId': ud.get('developerId', '')},
            }
        )
    if status == 'failed':
        h._openapi_v2_login_tasks.pop(user_id, None)
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
    ud = h._check_login(user_id)
    if ud:
        return h._ok(logged_in=True, uin=ud.get('uin', ''), appid=ud.get('appId', ''))
    return h._ok(logged_in=False)


async def handle_logout(request: web.Request):
    body = await request.json()
    user_id = body.get('user_id', 'web_user')
    h._openapi_user_data.pop(user_id, None)
    h._save_data()
    h._remove_v2(user_id)
    h._openapi_login_tasks.pop(user_id, None)
    h._openapi_v2_login_tasks.pop(user_id, None)
    return h._ok(message='登出成功')


async def handle_verify_saved_login(request: web.Request):
    api = h._get_bot_api()
    if not api:
        return h._err('bot_api 模块未加载')
    body = await request.json()
    user_id = body.get('user_id', 'web_user')
    # 此处不用 _require_api_and_login, 因为需见未登录时也返回 valid=False
    ud = h._openapi_user_data.get(user_id)
    if not ud:
        return h._ok(valid=False, message='没有保存的登录信息')
    try:
        res = await api.get_bot_list(uin=ud.get('uin'), quid=ud.get('developerId'), ticket=ud.get('ticket'))
        if res.get('code') == 0:
            return h._ok(
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
    h._openapi_user_data.pop(user_id, None)
    h._save_data()
    return h._ok(valid=False, message='登录状态已失效')
