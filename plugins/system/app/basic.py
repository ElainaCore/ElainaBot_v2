"""基础信息指令: 我的id、关于、消息信息、原始数据"""

import json
import platform

from core.base.config import cfg
from core.plugin.decorators import handler

from ._reply import reply

# ==================== ping ====================


def _parse_ts(ts):
    from datetime import datetime

    if ts is None:
        return None
    try:
        return float(ts)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(str(ts)).timestamp()
    except (TypeError, ValueError):
        return None


@handler(r'^ping$', name='Ping', desc='测试QQ消息接口延迟', owner_only=True)
async def ping(event, match):
    import time

    import aiohttp

    t0 = time.time()
    try:
        async with (
            aiohttp.ClientSession() as s,
            s.get(
                'https://api.sgroup.qq.com/gateway/bot',
                timeout=aiohttp.ClientTimeout(total=5),
            ),
        ):
            api_ms = round((time.time() - t0) * 1000)
    except Exception:
        api_ms = -1
    api_text = f'{api_ms}ms' if api_ms >= 0 else '超时'
    ts = _parse_ts(event.timestamp)
    msg_ms = round((time.time() - ts) * 1000) if ts else '未知'
    await reply(event, f'pong 🏓\nAPI延迟: {api_text}\n消息延迟: {msg_ms}ms' if msg_ms != '未知' else f'pong 🏓\nAPI延迟: {api_text}')


# ==================== 我的id ====================


@handler(r'^我的id$', name='我的ID', desc='查看自己的用户/群组ID')
async def getid(event, match):
    info = [
        f'<@{event.user_id}>',
        f'用户ID: {event.user_id}',
    ]
    if event.is_group and event.group_id:
        info.append(f'群组ID: {event.group_id}')
    elif event.is_direct:
        info.append('会话类型: 私聊')
    elif event.is_channel:
        if event.guild_id:
            info.append(f'频道ID: {event.guild_id}')
        if event.channel_id:
            info.append(f'子频道ID: {event.channel_id}')
    await reply(event, '\n'.join(info))


# ==================== 关于 ====================


@handler(r'^关于$', name='关于', desc='查看机器人信息')
async def about_info(event, match):
    python_version = platform.python_version()

    # 获取当前 bot 信息
    bot_name = 'Elaina'
    robot_qq = ''
    appid = event.appid or ''
    conn_mode = 'WebHook'
    try:
        bot_cfg = cfg.get_bot_config(event.appid)
        if bot_cfg:
            bot_name = bot_cfg.get('name', 'Elaina')
            robot_qq = str(bot_cfg.get('robot_qq', ''))
    except (AttributeError, TypeError):
        pass

    # 获取版本号
    kernel_version = '2.0'
    try:
        from core.application import get_app

        app = get_app()
        if app:
            bot = app.get_bot(event.appid)
            if bot:
                if bot.name:
                    bot_name = bot.name
                conn_mode = 'WebSocket' if bot.ws_client else 'WebHook'
            pm = getattr(app, 'plugin_manager', None)
            if pm:
                plugins = pm.get_plugin_list() if hasattr(pm, 'get_plugin_list') else []
                handler_count = pm.handler_count if hasattr(pm, 'handler_count') else 0
            else:
                plugins, handler_count = [], 0
        else:
            plugins, handler_count = [], 0
    except Exception:
        plugins, handler_count = [], 0

    msg_parts = [
        f'<@{event.user_id}> 关于{bot_name}',
        '───────────────',
        f'🔌 连接方式: {conn_mode}',
    ]
    if robot_qq:
        msg_parts.append(f'🤖 机器人QQ: {robot_qq}')
    if appid:
        msg_parts.append(f'🆔 APPID: {appid}')
    msg_parts.extend(
        [
            f'🚀 内核版本: {kernel_version}',
            f'⚙️ Python: {python_version}',
            f'💫 已加载插件: {len(plugins)}',
            f'⚡ 已加载处理器: {handler_count}',
            '',
            f'>Tip: 只有艾特{bot_name}，{bot_name}才能接收到你的消息~！',
        ]
    )
    await reply(event, '\n'.join(msg_parts))


# ==================== 原始数据 ====================


@handler(r'^原始数据$', name='原始数据', desc='查看原始事件JSON', owner_only=True)
async def get_raw_data(event, match):
    raw = event.raw
    if isinstance(raw, dict):
        raw_str = json.dumps(raw, ensure_ascii=False, indent=2)
    elif isinstance(raw, str):
        try:
            raw_str = json.dumps(json.loads(raw), ensure_ascii=False, indent=2)
        except Exception:
            raw_str = raw
    else:
        raw_str = '(无)'
    await reply(event, f'原始事件:\n```json\n{raw_str}\n```')
