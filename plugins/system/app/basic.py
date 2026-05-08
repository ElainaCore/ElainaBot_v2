"""基础信息指令: 我的id、关于、消息信息、原始数据"""

import json
import platform
from core.plugin.decorators import handler
from core.base.config import cfg


# ==================== ping ====================

@handler(r'^ping$', name='Ping', desc='测试QQ消息接口延迟')
async def ping(event, match):
    import time, aiohttp
    t0 = time.time()
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get('https://api.sgroup.qq.com/gateway/bot', timeout=aiohttp.ClientTimeout(total=5)):
                api_ms = round((time.time() - t0) * 1000)
    except Exception:
        api_ms = -1
    api_text = f'{api_ms}ms' if api_ms >= 0 else '超时'
    msg_ms = round((time.time() - event.timestamp) * 1000) if event.timestamp else '未知'
    await event.reply(f"pong 🏓\nAPI延迟: {api_text}\n消息延迟: {msg_ms}ms" if msg_ms != '未知' else f"pong 🏓\nAPI延迟: {api_text}")


# ==================== 我的id ====================

@handler(r'^我的id$', name='我的ID', desc='查看自己的用户/群组ID')
async def getid(event, match):
    info = [
        f"<@{event.user_id}>",
        f"用户ID: {event.user_id}",
    ]
    if event.is_group and event.group_id:
        info.append(f"群组ID: {event.group_id}")
    elif event.is_direct:
        info.append("会话类型: 私聊")
    elif event.is_channel:
        if event.guild_id:
            info.append(f"频道ID: {event.guild_id}")
        if event.channel_id:
            info.append(f"子频道ID: {event.channel_id}")
    await event.reply('\n'.join(info))


# ==================== 关于 ====================

@handler(r'^关于$', name='关于', desc='查看机器人信息')
async def about_info(event, match):
    python_version = platform.python_version()

    # 获取当前 bot 信息
    bot_name = 'Elaina'
    robot_qq = ''
    appid = event.appid or ''
    try:
        bot_cfg = cfg.get_bot_config(event.appid)
        if bot_cfg:
            bot_name = bot_cfg.get('name', 'Elaina')
            robot_qq = str(bot_cfg.get('robot_qq', ''))
    except Exception:
        pass

    # 获取版本号
    kernel_version = '2.0'
    try:
        from core.bot.manager import _bot_manager_ref
        if _bot_manager_ref:
            pm = getattr(_bot_manager_ref, 'plugin_manager', None)
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
        f'🔌 连接方式: WebHook',
    ]
    if robot_qq:
        msg_parts.append(f'🤖 机器人QQ: {robot_qq}')
    if appid:
        msg_parts.append(f'🆔 APPID: {appid}')
    msg_parts.extend([
        f'🚀 内核版本: {kernel_version}',
        f'⚙️ Python: {python_version}',
        f'💫 已加载插件: {len(plugins)}',
        f'⚡ 已加载处理器: {handler_count}',
        '',
        f'>Tip: 只有艾特{bot_name}，{bot_name}才能接收到你的消息~！',
    ])
    await event.reply('\n'.join(msg_parts))


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
    await event.reply(f'原始事件:\n```json\n{raw_str}\n```')
