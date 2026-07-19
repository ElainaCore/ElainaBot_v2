"""共享机器人查询工具"""


def iter_bots(bot_manager, appid_filter=''):
    if not bot_manager:
        return []
    if appid_filter and appid_filter in bot_manager._bots:
        return [(appid_filter, bot_manager._bots[appid_filter])]
    return list(bot_manager._bots.items())


def bot_avatar(robot_qq=''):
    return f'http://q1.qlogo.cn/g?b=qq&nk={robot_qq}&s=100' if robot_qq else ''


def running_bot_payload(appid, inst):
    robot_qq = getattr(inst, 'robot_qq', '') or ''
    ws_connected = bool(inst.ws_client and getattr(inst.ws_client, '_session_id', None))
    return {
        'appid': appid,
        'name': getattr(inst, 'name', '') or appid,
        'robot_qq': robot_qq,
        'bot_id': getattr(inst, 'bot_id', ''),
        'union_openid': getattr(inst, 'union_openid', ''),
        'avatar': getattr(inst, 'avatar_url', '') or bot_avatar(robot_qq),
        'connected': ws_connected,
        'connection_type': 'WebSocket' if inst.ws_client else 'Webhook',
        'enabled': True,
    }


def configured_bot_payload(config):
    appid = str(config.get('appid', ''))
    robot_qq = str(config.get('robot_qq', ''))
    return {
        'appid': appid,
        'name': appid,
        'robot_qq': robot_qq,
        'bot_id': '',
        'union_openid': '',
        'avatar': bot_avatar(robot_qq),
        'connected': False,
        'connection_type': '-',
        'enabled': config.get('enabled', True),
    }
