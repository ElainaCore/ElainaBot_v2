"""共享机器人查询工具"""

# WSClient 发布到连接状态服务的阶段；socket 打开期间的阶段视为已连接
_WS_CONNECTED = frozenset({'connected', 'identifying', 'ready', 'resuming', 'replay_complete'})
_WS_RECONNECTING = frozenset({'connecting', 'disconnected', 'reconnecting', 'resume_failed'})
_WS_STATUS_TEXT = {'connected': '已连接', 'reconnecting': '断线重连中', 'disconnected': '未连接'}

def connection_status_text(state):
    return _WS_STATUS_TEXT[state]


def iter_bots(bot_manager, appid_filter=''):
    if not bot_manager:
        return []
    if appid_filter and appid_filter in bot_manager._bots:
        return [(appid_filter, bot_manager._bots[appid_filter])]
    return list(bot_manager._bots.items())


def bot_avatar(robot_qq=''):
    return f'http://q1.qlogo.cn/g?b=qq&nk={robot_qq}&s=100' if robot_qq else ''


def ws_connection_state(inst):
    """WS 连接状态: connected / reconnecting / disconnected，取自 ctx.get_connection_status 的同一快照"""
    status = inst.connection_status.get(inst.appid, transport='websocket')
    if status is None or status.phase == 'stopped':
        return 'disconnected'
    if status.phase in _WS_CONNECTED:
        return 'connected'
    if status.phase in _WS_RECONNECTING:
        return 'reconnecting'
    # unknown: 已发起连接则为 socket 上的协议异常, 否则为尚未启动
    return 'connected' if status.context.connection_generation else 'disconnected'


def running_bot_payload(appid, inst):
    robot_qq = getattr(inst, 'robot_qq', '') or ''
    ws_state = ws_connection_state(inst) if inst.ws_client else 'disconnected'
    ws_connected = bool(inst.ws_client) and ws_state == 'connected'
    connection_status = '等待接收中' if not inst.ws_client else connection_status_text(ws_state)
    return {
        'appid': appid,
        'name': getattr(inst, 'name', '') or appid,
        'robot_qq': robot_qq,
        'bot_id': getattr(inst, 'bot_id', ''),
        'union_openid': getattr(inst, 'union_openid', ''),
        'avatar': getattr(inst, 'avatar_url', '') or bot_avatar(robot_qq),
        'connected': ws_connected,
        'connection_status': connection_status,
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
        'connection_status': '未连接',
        'connection_type': '-',
        'enabled': config.get('enabled', True),
    }
