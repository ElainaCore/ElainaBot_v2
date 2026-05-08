"""管理指令: dm调试、重启、黑名单管理"""

import os
import re
import sys
import json
import asyncio
import datetime
from core.plugin.decorators import handler, on_load
from core.base.logger import get_logger, PLUGIN
from core.base.config import cfg

log = get_logger(PLUGIN, "系统管理")

# ==================== 数据文件 ====================

# 插件 data 目录 (plugins/system/data/)
_PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA_DIR = os.path.join(_PLUGIN_DIR, 'data')
os.makedirs(_DATA_DIR, exist_ok=True)

# 项目根目录 data/
_ROOT_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(_PLUGIN_DIR)),
    'data')
os.makedirs(_ROOT_DATA_DIR, exist_ok=True)

_BLACKLIST_FILE = os.path.join(_ROOT_DATA_DIR, 'blacklist.json')
_GROUP_BLACKLIST_FILE = os.path.join(_ROOT_DATA_DIR, 'group_blacklist.json')
_RESTART_STATUS_FILE = os.path.join(_DATA_DIR, 'restart_status.json')

# 内存缓存
_blacklist = {}
_group_blacklist = {}


def _load_json(path, default=None):
    if default is None:
        default = {}
    if not os.path.isfile(path):
        return default
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@on_load
def _load_blacklists():
    global _blacklist, _group_blacklist
    _blacklist = _load_json(_BLACKLIST_FILE)
    _group_blacklist = _load_json(_GROUP_BLACKLIST_FILE)


def _mask_id(id_str, mask_char='*'):
    if not id_str or len(id_str) <= 6:
        return id_str
    return id_str[:3] + mask_char * 4 + id_str[-3:]


# ==================== DM 调试消息 ====================

@handler(r'^dm(.+)$', name='DM调试', desc='dm+内容 发送调试消息', owner_only=True)
async def send_dm(event, match):
    content = match.group(1).strip()
    if not content:
        await event.reply('❌ 消息内容不能为空\n💡 使用格式：dm+消息内容')
        return

    # 处理转义字符
    for old, new in [('\\n', '\n'), ('\\t', '\t'), ('\\r', '\r'), ('\\\\', '\\')]:
        content = content.replace(old, new)

    # 解析按钮配置: 按钮 [(text,data,type,enter,style)]
    button_pattern = r'按钮\s*\[([^\]]+)\]'
    button_matches = list(re.finditer(button_pattern, content))
    buttons = None

    if button_matches:
        message_content = content[:button_matches[0].start()].strip()
        button_rows = []
        for bm in button_matches:
            button_str = bm.group(1)
            row = []
            for item in re.findall(r'\(([^)]+)\)', button_str):
                parts = [p.strip() for p in item.split(',')]
                if len(parts) >= 2:
                    row.append({
                        'text': parts[0],
                        'data': parts[1],
                        'type': int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 2,
                        'enter': parts[3].lower() in ('true', '1', 'yes') if len(parts) > 3 else True,
                        'style': int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 1,
                    })
            if row:
                button_rows.append(row)
        if button_rows:
            from core.message.keyboard import build_keyboard
            buttons = build_keyboard(button_rows, event.appid)
        content = message_content

    await event.reply(content, buttons=buttons)


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

    await event.reply('🔄 正在重启...')
    await asyncio.sleep(0.5)

    # 重启进程
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
            from core.bot.manager import _bot_manager_ref
            if _bot_manager_ref:
                import random
                bots = list(_bot_manager_ref._bots.values()) if hasattr(_bot_manager_ref, '_bots') else []
                for bot in bots:
                    user_id = data.get('user_id')
                    group_id = data.get('group_id')
                    msg_id = data.get('message_id')
                    if not (user_id and msg_id):
                        continue
                    endpoint = f"/v2/groups/{group_id}/messages" if group_id != 'c2c' else f"/v2/users/{user_id}/messages"
                    payload = {
                        'msg_type': 0,
                        'msg_seq': random.randint(10000, 999999),
                        'content': f'✅ 重启完成！\n🕒 耗时: {duration_ms}ms',
                        'msg_id': msg_id,
                    }
                    asyncio.create_task(bot.sender.post_json(endpoint, payload))
                    break
        except Exception as e:
            log.warning(f"发送重启完成消息失败: {e}")
    except Exception as e:
        log.warning(f"检查重启状态失败: {e}")


# ==================== 用户黑名单 ====================

@handler(r'^黑名单帮助$', name='黑名单帮助', desc='查看黑名单管理帮助', owner_only=True)
async def show_blacklist_help(event, match):
    lines = ['📖 黑名单管理']

    # 用户黑名单
    lines.append('\n━━━ 🚫 用户黑名单 ━━━')
    if not _blacklist:
        lines.append('✅ 空')
    else:
        for idx, (uid, reason) in enumerate(_blacklist.items(), 1):
            lines.append(f'{idx}. {_mask_id(uid)}\n   原因: {reason}')

    # 群黑名单
    lines.append('\n━━━ 🚫 群黑名单 ━━━')
    if not _group_blacklist:
        lines.append('✅ 空')
    else:
        for idx, (gid, reason) in enumerate(_group_blacklist.items(), 1):
            lines.append(f'{idx}. {_mask_id(gid)}\n   原因: {reason}')

    lines.append('\n>提示：黑名单数据保存在JSON文件中')
    await event.reply('\n'.join(lines))


@handler(r'^黑名单查看$', name='黑名单查看', desc='查看所有黑名单', owner_only=True)
async def view_blacklist(event, match):
    await show_blacklist_help(event, match)


@handler(r'^黑名单添加 *(.+?) *([a-zA-Z0-9]+)$', name='黑名单添加', desc='添加用户到黑名单', owner_only=True)
async def add_blacklist(event, match):
    reason = match.group(1) or '未指明原因'
    user_id = match.group(2)
    if not user_id:
        return await event.reply('请提供用户ID')

    # 检查是否是主人
    try:
        bot_cfg = cfg.get_bot_config(event.appid)
        owner_ids = bot_cfg.get('owner_ids', []) if bot_cfg else []
        if user_id in owner_ids:
            return await event.reply('无法将主人添加到黑名单')
    except Exception:
        pass

    _blacklist[user_id] = reason
    _save_json(_BLACKLIST_FILE, _blacklist)
    await event.reply(f'已添加用户 {user_id} 到黑名单\n原因: {reason}')


@handler(r'^黑名单删除 *([a-zA-Z0-9]+)$', name='黑名单删除', desc='从黑名单移除用户', owner_only=True)
async def remove_blacklist(event, match):
    user_id = match.group(1)
    if user_id not in _blacklist:
        return await event.reply(f'用户 {user_id} 不在黑名单中')
    reason = _blacklist.pop(user_id, '未知')
    _save_json(_BLACKLIST_FILE, _blacklist)
    await event.reply(f'已移除用户 {user_id}\n原因: {reason}')


# ==================== 群黑名单 ====================

@handler(r'^群黑名单添加 +(?:(.+?) +)?([A-Z0-9]{20,})$', name='群黑名单添加', desc='添加群到黑名单', owner_only=True)
async def add_group_blacklist(event, match):
    reason = match.group(1) or '未指明原因'
    group_id = match.group(2)
    if not group_id:
        return await event.reply('❌ 请提供群组ID\n💡 格式：群黑名单添加 [原因] [群ID]')
    _group_blacklist[group_id] = reason
    _save_json(_GROUP_BLACKLIST_FILE, _group_blacklist)
    await event.reply(f'已添加群组 {group_id} 到群黑名单\n原因: {reason}')


@handler(r'^群黑名单删除 *([a-zA-Z0-9]+)$', name='群黑名单删除', desc='从群黑名单移除群', owner_only=True)
async def remove_group_blacklist(event, match):
    group_id = match.group(1)
    if group_id not in _group_blacklist:
        return await event.reply(f'群组 {group_id} 不在群黑名单中')
    reason = _group_blacklist.pop(group_id, '未知')
    _save_json(_GROUP_BLACKLIST_FILE, _group_blacklist)
    await event.reply(f'已移除群组 {group_id}\n原因: {reason}')
