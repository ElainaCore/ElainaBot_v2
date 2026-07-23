"""新版开放平台 (q.qq.com v2) — 状态 / 主体 / 代理 / 上传处理器"""

from urllib.parse import urlparse

from aiohttp import ClientError, ClientSession, ClientTimeout, web
from aiohttp.web_request import FileField

import web.tools._openapi.handler as h

_V2_ALLOWED_PREFIXES = ('/cgi-bin/v2/', '/bopen/v2/', '/api/v3/login/', '/api/v1/logout')

_AVATAR_MAX_SIZE = 2 * 1024 * 1024


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
    ud = h._v2_get(body.get('user_id', 'web_user'))
    if not ud:
        return h._ok(logged_in=False, ready=False)
    return h._ok(
        logged_in=True,
        ready=_v2_ready(ud),
        uin=ud.get('uin', ''),
        developer_id=ud.get('developer_id_lite') or ud.get('developerId') or '',
    )


async def handle_v2_developers(request: web.Request):
    body = await request.json() if request.can_read_body else {}
    user_id = body.get('user_id', 'web_user')
    ud = h._v2_get(user_id)
    if not ud:
        return h._err('未登录')
    if not _v2_ready(ud):
        return h._err('新版开放平台未授权，请重新扫码登录')
    api = h._get_bot_api()
    if not api:
        return h._err('bot_api 模块未加载')
    result = await api.v2_request(
        'GET',
        '/api/v3/login/bopen/developer_list',
        cookie=_v2_cookie(ud),
        skey=ud.get('skey', ''),
    )
    relogin_message = _v2_relogin_message(result)
    if relogin_message:
        h._remove_v2(user_id)
        return h._err(relogin_message, relogin=True)
    response = result.get('res') if isinstance(result, dict) else None
    if isinstance(response, dict) and response.get('ret') not in (None, 0):
        return h._err(str(response.get('msg') or '获取主体列表失败'))
    from web.tools._bot.api import parse_login_developers

    return h._ok(
        developers=parse_login_developers(result),
        current_developer_id=ud.get('developer_id_lite') or ud.get('developerId') or '',
    )


async def handle_v2_switch_developer(request: web.Request):
    body = await request.json()
    user_id = body.get('user_id', 'web_user')
    developer_id = str(body.get('developer_id') or '')
    if not developer_id:
        return h._err('请选择开发者主体')
    ud = h._v2_get(user_id)
    if not ud:
        return h._err('未登录')
    if not _v2_ready(ud):
        return h._err('新版开放平台未授权，请重新扫码登录')
    api = h._get_bot_api()
    if not api:
        return h._err('bot_api 模块未加载')

    list_result = await api.v2_request(
        'GET',
        '/api/v3/login/bopen/developer_list',
        cookie=_v2_cookie(ud),
        skey=ud.get('skey', ''),
    )
    relogin_message = _v2_relogin_message(list_result)
    if relogin_message:
        h._remove_v2(user_id)
        return h._err(relogin_message, relogin=True)
    from web.tools._bot.api import extract_login_creds, parse_login_developers

    developers = parse_login_developers(list_result)
    if developer_id not in {item['id'] for item in developers}:
        return h._err('请选择有效的开发者主体')

    result, cookies = await api.v2_switch_developer(
        developer_id,
        cookie=_v2_cookie(ud),
        skey=ud.get('skey', ''),
    )
    if not isinstance(result, dict):
        return h._err('切换主体失败')
    ret = result.get('ret')
    if ret not in (None, 0):
        return h._err(str(result.get('msg') or '切换主体失败'))

    ud.update(extract_login_creds(cookies))
    ud['developer_id_lite'] = developer_id
    ud['developerId'] = developer_id
    h._save_v2(user_id)
    old = h._openapi_user_data.get(user_id) or {}
    old['developerId'] = developer_id
    if ud.get('qticket'):
        old['ticket'] = ud['qticket']
    h._openapi_user_data[user_id] = old
    h._save_data()
    return h._ok(developer_id=developer_id, message='主体切换成功')


async def handle_v2_proxy(request: web.Request):
    """新版开放平台接口通用代理: 转发前端与 q.qq.com 完全一致的请求"""
    body = await request.json()
    api = h._get_bot_api()
    if not api:
        return h._err('bot_api 模块未加载')
    user_id = body.get('user_id', 'web_user')
    ud = h._v2_get(user_id)
    if not ud:
        return h._err('未登录')
    if not _v2_ready(ud):
        return h._err('新版开放平台未授权，请重新扫码登录', status=200)
    path = body.get('path', '')
    if not any(path.startswith(p) for p in _V2_ALLOWED_PREFIXES):
        return h._err('非法的接口路径')
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
        h._remove_v2(user_id)
        return h._err(relogin_message, relogin=True)
    return web.json_response(result)


async def handle_v2_upload_avatar(request: web.Request):
    form = await request.post()
    user_id = str(form.get('user_id', 'web_user'))
    file = form.get('file')
    if not isinstance(file, FileField) or not file.content_type.startswith('image/'):
        return h._err('请选择图片文件')
    content = file.file.read(_AVATAR_MAX_SIZE + 1)
    if len(content) > _AVATAR_MAX_SIZE:
        return h._err('图片需小于 2MB')

    api = h._get_bot_api()
    if not api:
        return h._err('bot_api 模块未加载')
    ud = h._v2_get(user_id)
    if not ud:
        return h._err('未登录')
    if not _v2_ready(ud):
        return h._err('新版开放平台未授权，请重新扫码登录')
    try:
        bot_appid = int(str(form.get('bot_appid', 0)))
    except ValueError:
        return h._err('机器人 AppID 无效')

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
        return h._err('获取上传地址失败')
    relogin_message = _v2_relogin_message(result)
    if relogin_message:
        h._remove_v2(user_id)
        return h._err(relogin_message, relogin=True)
    if result.get('retcode') not in (None, 0) or result.get('code') not in (None, 0):
        return h._err(str(result.get('msg') or result.get('message') or '获取上传地址失败'))
    data = result.get('data')
    payload = data if isinstance(data, dict) else result
    upload_url = result.get('upload_url') or payload.get('upload_url')
    upload_id = result.get('upload_id') or payload.get('upload_id')
    parsed = urlparse(upload_url if isinstance(upload_url, str) else '')
    if parsed.scheme != 'https' or not (parsed.hostname or '').endswith('.myqcloud.com') or not upload_id:
        return h._err('获取上传地址失败')

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
                return h._err(f'图片上传失败: HTTP {response.status}')
    except (ClientError, TimeoutError):
        return h._err('图片上传失败')
    return h._ok(upload_id=upload_id)
