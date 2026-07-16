"""管理指令: dm调试、重启、黑名单管理"""

import asyncio
import datetime
import json
import os
import re
import sys

from core.base.config import cfg
from core.base.logger import PLUGIN, get_logger
from core.plugin.decorators import handler, on_load

from ._reply import reply

log = get_logger(PLUGIN, '系统管理')

# ==================== 数据文件 ====================

# 插件 data 目录 (plugins/system/data/)
_PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA_DIR = os.path.join(_PLUGIN_DIR, 'data')
os.makedirs(_DATA_DIR, exist_ok=True)

_RESTART_STATUS_FILE = os.path.join(_DATA_DIR, 'restart_status.json')


def _load_json(path, default=None):
    if default is None:
        default = {}
    if not os.path.isfile(path):
        return default
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _mask_id(id_str, mask_char='*'):
    if not id_str or len(id_str) <= 6:
        return id_str
    return id_str[:3] + mask_char * 4 + id_str[-3:]


# ==================== DM 调试消息 ====================


@handler(r'^dm(.+)$', name='DM调试', desc='dm+内容 发送调试消息', owner_only=True)
async def send_dm(event, match):
    content = match.group(1).strip()
    if not content:
        await reply(event, '❌ 消息内容不能为空\n💡 使用格式：dm+消息内容')
        return

    # 处理转义字符
    for old, new in [('\\n', '\n'), ('\\t', '\t'), ('\\r', '\r'), ('\\\\', '\\')]:
        content = content.replace(old, new)

    # 解析按钮配置: 按钮 [(text,data,type,enter,style)]
    button_pattern = r'按钮\s*\[([^\]]+)\]'
    button_matches = list(re.finditer(button_pattern, content))
    buttons = None

    if button_matches:
        message_content = content[: button_matches[0].start()].strip()
        button_rows = []
        for bm in button_matches:
            button_str = bm.group(1)
            row = []
            for item in re.findall(r'\(([^)]+)\)', button_str):
                parts = [p.strip() for p in item.split(',')]
                if len(parts) >= 2:
                    row.append(
                        {
                            'text': parts[0],
                            'data': parts[1],
                            'type': int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 2,
                            'enter': parts[3].lower() in ('true', '1', 'yes') if len(parts) > 3 else True,
                            'style': int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 1,
                        }
                    )
            if row:
                button_rows.append(row)
        if button_rows:
            from core.message.keyboard import build_keyboard

            buttons = build_keyboard(button_rows, event.appid)
        content = message_content

    await reply(event, content, buttons=buttons)


# ==================== 重启 ====================


@handler(r'^重启$', name='重启', desc='重启机器人进程', owner_only=True)
async def restart_bot(event, match):
    restart_data = {
        'restart_time': datetime.datetime.now().isoformat(),
        'completed': False,
        'message_id': event.message_id,
        'user_id': event.user_id,
        'group_id': event.group_id if event.is_group else 'c2c',
    }
    _save_json(_RESTART_STATUS_FILE, restart_data)

    await reply(event, '🔄 正在重启...')
    await asyncio.sleep(0.5)

    # 优雅重启: 走 Application 流程, 刷写 SQLite 缓冲再 os.execv
    try:
        from core.application import get_app
        app = get_app()
        if app:
            app._restart_requested = True
            if app._stop_event:
                app._stop_event.set()
            return
    except Exception:
        pass
    # 兜底: Application 不可用时直接重启
    python = sys.executable
    os.execv(python, [python] + sys.argv)


# ==================== 重启完成检测 ====================


@on_load
def _check_restart_status():
    """启动时检查是否有未完成的重启状态"""
    if not os.path.isfile(_RESTART_STATUS_FILE):
        return
    try:
        data = _load_json(_RESTART_STATUS_FILE)
        if data.get('completed', True):
            return

        start_time = datetime.datetime.fromisoformat(data['restart_time'])
        duration_ms = int((datetime.datetime.now() - start_time).total_seconds() * 1000)

        # 标记完成
        data['completed'] = True
        _save_json(_RESTART_STATUS_FILE, data)

        # 发送重启完成消息 (通过底层 API)
        try:
            from core.application import get_app

            app = get_app()
            if app:
                import random

                bots = list(app._bots.values()) if hasattr(app, '_bots') else []
                for bot in bots:
                    user_id = data.get('user_id')
                    group_id = data.get('group_id')
                    msg_id = data.get('message_id')
                    if not (user_id and msg_id):
                        continue
                    endpoint = f'/v2/groups/{group_id}/messages' if group_id != 'c2c' else f'/v2/users/{user_id}/messages'
                    payload = {
                        'msg_type': 0,
                        'msg_seq': random.randint(10000, 999999),
                        'content': f'✅ 重启完成！\n🕒 耗时: {duration_ms}ms',
                        'msg_id': msg_id,
                    }
                    asyncio.create_task(bot.sender.post_json(endpoint, payload))
                    break
        except Exception as e:
            log.warning(f'发送重启完成消息失败: {e}')
    except Exception as e:
        log.warning(f'检查重启状态失败: {e}')


# ==================== 用户黑名单 ====================


def _get_blacklist(appid, key):
    return [str(x) for x in (cfg.get_bot_setting(appid, f'blacklist.{key}', []) or [])]


def _set_blacklist(appid, key, values):
    return cfg.set_bot_setting(appid, f'blacklist.{key}_list', sorted(values))


@handler(r'^黑名单帮助$', name='黑名单帮助', desc='查看黑名单管理帮助', owner_only=True)
async def show_blacklist_help(event, match):
    lines = ['📖 黑名单管理']

    # 用户黑名单
    lines.append('\n━━━ 🚫 用户黑名单 ━━━')
    user_list = _get_blacklist(event.appid, 'user_list')
    if not user_list:
        lines.append('✅ 空')
    else:
        for idx, uid in enumerate(sorted(user_list), 1):
            lines.append(f'{idx}. {_mask_id(uid)}')

    # 群黑名单
    lines.append('\n━━━ 🚫 群黑名单 ━━━')
    group_list = _get_blacklist(event.appid, 'group_list')
    if not group_list:
        lines.append('✅ 空')
    else:
        for idx, gid in enumerate(sorted(group_list), 1):
            lines.append(f'{idx}. {_mask_id(gid)}')

    await reply(event, '\n'.join(lines))


@handler(r'^黑名单查看$', name='黑名单查看', desc='查看所有黑名单', owner_only=True)
async def view_blacklist(event, match):
    await show_blacklist_help(event, match)


@handler(
    r'^黑名单添加 *(.+?) *([a-zA-Z0-9]+)$',
    name='黑名单添加',
    desc='添加用户到黑名单',
    owner_only=True,
)
async def add_blacklist(event, match):
    user_id = match.group(2)
    if not user_id:
        return await reply(event, '请提供用户ID')

    # 检查是否是主人
    try:
        bot_cfg = cfg.get_bot_config(event.appid)
        owner_ids = bot_cfg.get('owner_ids', []) if bot_cfg else []
        if user_id in owner_ids:
            return await reply(event, '无法将主人添加到黑名单')
    except Exception:
        pass

    user_list = _get_blacklist(event.appid, 'user_list')
    if user_id in user_list:
        return await reply(event, f'用户 {user_id} 已在黑名单中')
    user_list.append(user_id)
    if not _set_blacklist(event.appid, 'user', user_list):
        return await reply(event, '写入配置失败，无法操作黑名单')
    await reply(event, f'已添加用户 {user_id} 到黑名单')


@handler(
    r'^黑名单删除 *([a-zA-Z0-9]+)$',
    name='黑名单删除',
    desc='从黑名单移除用户',
    owner_only=True,
)
async def remove_blacklist(event, match):
    user_id = match.group(1)
    user_list = _get_blacklist(event.appid, 'user_list')
    if user_id not in user_list:
        return await reply(event, f'用户 {user_id} 不在黑名单中')
    user_list.remove(user_id)
    if not _set_blacklist(event.appid, 'user', user_list):
        return await reply(event, '写入配置失败，无法操作黑名单')
    await reply(event, f'已移除用户 {user_id}')


# ==================== 群黑名单 ====================


@handler(
    r'^群黑名单添加 +(?:(.+?) +)?([A-Z0-9]{20,})$',
    name='群黑名单添加',
    desc='添加群到黑名单',
    owner_only=True,
)
async def add_group_blacklist(event, match):
    group_id = match.group(2)
    if not group_id:
        return await reply(event, '❌ 请提供群组ID\n💡 格式：群黑名单添加 [群ID]')
    group_list = _get_blacklist(event.appid, 'group_list')
    if group_id in group_list:
        return await reply(event, f'群组 {group_id} 已在群黑名单中')
    group_list.append(group_id)
    if not _set_blacklist(event.appid, 'group', group_list):
        return await reply(event, '写入配置失败，无法操作黑名单')
    await reply(event, f'已添加群组 {group_id} 到群黑名单')


@handler(
    r'^群黑名单删除 *([a-zA-Z0-9]+)$',
    name='群黑名单删除',
    desc='从群黑名单移除群',
    owner_only=True,
)
async def remove_group_blacklist(event, match):
    group_id = match.group(1)
    group_list = _get_blacklist(event.appid, 'group_list')
    if group_id not in group_list:
        return await reply(event, f'群组 {group_id} 不在群黑名单中')
    group_list.remove(group_id)
    if not _set_blacklist(event.appid, 'group', group_list):
        return await reply(event, '写入配置失败，无法操作黑名单')
    await reply(event, f'已移除群组 {group_id}')
