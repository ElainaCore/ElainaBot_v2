"""机器人信息查询"""

import ssl
import urllib.parse

import aiohttp as _aiohttp
from aiohttp import web

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

_API_URL = 'https://qun.qq.com/qunpro/robot/proxy/domain/qun.qq.com/cgi-bin/group_pro/robot/manager/share_info?bkn=508459323&robot_appid={}'
_QR_API = 'https://api.2dcode.biz/v1/create-qr-code?data={}'
_SHARE_URL = 'https://qun.qq.com/qunpro/robot/qunshare?robot_uin={}'
_CHANNEL_URL = 'https://qun.qq.com/qunpro/robot/share?robot_appid={}'
_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Linux; Android 15; wv) AppleWebKit/537.36 Chrome/135.0 Mobile Safari/537.36 QQ/9.1.75',
    'qname-service': '976321:131072',
    'qname-space': 'Production',
}

_bot_manager = None


def set_context(bot_manager):
    global _bot_manager
    _bot_manager = bot_manager


async def handle_get_robot_info(request: web.Request):
    appid = request.query.get('appid', '')
    if not appid and _bot_manager:
        appid = next(iter(_bot_manager._bots), '')

    bot = _bot_manager._bots.get(appid) if _bot_manager else None
    robot_qq = getattr(bot, 'robot_qq', '') if bot else ''
    share_url = _SHARE_URL.format(robot_qq)

    is_webhook = bool(bot and not bot.ws_client)
    ws_connected = bool(bot and bot.ws_client and getattr(bot.ws_client, '_connected', False))
    conn_type = 'Webhook' if is_webhook else 'WebSocket'
    conn_status = '已连接' if ws_connected else ('等待接收中' if is_webhook else '未连接')
    channel_url = _CHANNEL_URL.format(appid)

    def _qr(u):
        return '/api/robot/qrcode?url=' + urllib.parse.quote(u, safe='')

    webhook_url = ''
    if is_webhook:
        from core.base.config import cfg

        host = cfg.get('settings', 'server.host', '0.0.0.0')
        port = cfg.get('settings', 'server.port', 5200)
        req_host = request.host or ''
        # 从请求 Host 头提取地址 (兼容 IPv6 方括号格式 [::1]:port)
        display_host = req_host.rsplit(']', 1)[0] + ']' if req_host.startswith('[') else req_host.split(':')[0] or (host if isinstance(host, str) else host[0])
        webhook_url = f'http://{display_host}:{port}/?appid={appid}'
    base = {
        'appid': appid,
        'qq': robot_qq,
        'union_openid': getattr(bot, 'union_openid', '') if bot else '',
        'link': share_url,
        'connection_type': conn_type,
        'connection_status': conn_status,
        'webhook_url': webhook_url,
        'webhook_port': port if is_webhook else '',
        'qr_code_api': _qr(share_url),
        'channel_link': channel_url,
        'channel_qr_code_api': _qr(channel_url),
    }

    try:
        async with (
            _aiohttp.ClientSession() as session,
            session.get(
                _API_URL.format(appid),
                headers=_HEADERS,
                ssl=_SSL_CTX,
                timeout=_aiohttp.ClientTimeout(total=10),
            ) as resp,
        ):
            api_resp = await resp.json()

        if api_resp.get('retcode') != 0:
            raise Exception(api_resp.get('msg', 'API 错误'))

        robot = api_resp.get('data', {}).get('robot_data', {})
        commands = api_resp.get('data', {}).get('commands', [])
        avatar = robot.get('robot_avatar', '')
        if avatar and 'myqcloud.com' in avatar:
            avatar += ('&' if '?' in avatar else '?') + 'imageMogr2/format/png'

        return web.json_response(
            {
                **base,
                'success': True,
                'qq': robot.get('robot_uin', robot_qq),
                'name': robot.get('robot_name', '未知机器人'),
                'description': robot.get('robot_desc', '暂无描述'),
                'avatar': avatar,
                'appid': robot.get('appid', appid),
                'developer': robot.get('create_name', '未知'),
                'status': '正常' if robot.get('robot_offline', 1) == 0 else '离线',
                'data_source': 'api',
                'is_banned': robot.get('robot_ban', False),
                'commands_count': len(commands),
            }
        )
    except Exception as e:
        return web.json_response(
            {
                **base,
                'success': False,
                'error': str(e),
                'name': '加载失败',
                'data_source': 'fallback',
            }
        )


async def handle_get_robot_qrcode(request: web.Request):
    url = request.query.get('url', '')
    if not url:
        return web.json_response({'success': False, 'error': '缺少 URL'}, status=400)
    try:
        qr_url = _QR_API.format(urllib.parse.quote(url, safe=''))
        async with (
            _aiohttp.ClientSession() as session,
            session.get(qr_url, ssl=_SSL_CTX, timeout=_aiohttp.ClientTimeout(total=10)) as resp,
        ):
            data = await resp.read()
            return web.Response(
                body=data,
                content_type='image/png',
                headers={'Cache-Control': 'public, max-age=3600'},
            )
    except Exception as e:
        return web.json_response({'success': False, 'error': str(e)}, status=500)
