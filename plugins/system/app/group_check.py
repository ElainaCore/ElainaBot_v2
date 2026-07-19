"""群检测: 检测机器人是否还在群内, 记录被踢群, 发送「删除被踢群」才从 groups_users 删除"""

import asyncio
import json
import os
import random
import string
from datetime import datetime

from core.message import bot_openid
from core.plugin.decorators import handler

from ._reply import reply

# 机器人非群成员的错误码 (两个都命中才删除)
_NOT_IN_GROUP_CODE = 11293
_NOT_IN_GROUP_ERR_CODE = 40011026

_DIR = os.path.dirname(os.path.abspath(__file__))
_REMOVED_LOG = os.path.join(_DIR, '群检测_删除记录.txt')
_PARTIAL_LOG = os.path.join(_DIR, '群检测_疑似记录.txt')
_PENDING_FILE = os.path.join(_DIR, '群检测_待删除.json')

_running = False
_pending_lock = asyncio.Lock()


def _load_pending():
    try:
        with open(_PENDING_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_pending(data):
    tmp = _PENDING_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _PENDING_FILE)


def _get_bot(appid):
    from core.bot.manager import _bot_manager_ref
    if not _bot_manager_ref:
        return None
    return _bot_manager_ref.get_bot(appid)


def _append_log(path, appid, gid, data):
    line = json.dumps({
        'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'appid': appid,
        'group_id': gid,
        'response': data,
    }, ensure_ascii=False)
    with open(path, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


# 每秒并发请求数
_RATE_PER_SEC = 50


async def _check_one(sender, ls, appid, member_id, gid, stats):
    success, data = await sender.get_json(f'/v2/groups/{gid}/members/{member_id}')
    if not success and isinstance(data, dict):
        code_hit = data.get('code') == _NOT_IN_GROUP_CODE
        err_hit = data.get('err_code') == _NOT_IN_GROUP_ERR_CODE
        if code_hit and err_hit:
            ls.db_queue('UPDATE groups_users SET in_group=0 WHERE group_id=?', (gid,))
            async with _pending_lock:
                pending = await asyncio.to_thread(_load_pending)
                pending.setdefault(appid, {})[gid] = {
                    'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'response': data,
                }
                await asyncio.to_thread(_save_pending, pending)
            stats['kicked'] += 1
        elif code_hit or err_hit:
            await asyncio.to_thread(_append_log, _PARTIAL_LOG, appid, gid, data)
            stats['partial'] += 1
    elif not success:
        stats['failed'] += 1


async def _check_bot(bot, stats):
    """检测单个机器人的所有群, 每秒 _RATE_PER_SEC 个并发"""
    ls = getattr(bot, 'log_service', None)
    sender = getattr(bot, 'sender', None)
    appid = getattr(bot, 'appid', '')
    if ls is None or sender is None:
        return

    # 从 data/bot_openids.json 取自身机器人的第一个 openid 作为探测用户 id;
    # 机器人不在群时会优先返回非群成员错误, 故无 openid 时用随机 32 位大写字母+数字探测
    member_id = bot_openid.first_id(appid)
    if not member_id:
        member_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=32))
        stats['no_openid'].append(appid)

    rows = await ls.db_fetch_all('SELECT group_id FROM groups_users')
    gids = [gid for r in rows or [] if (gid := (r.get('group_id') or '').strip())]
    stats['scanned'] += len(gids)

    for i in range(0, len(gids), _RATE_PER_SEC):
        batch = gids[i:i + _RATE_PER_SEC]
        start = asyncio.get_event_loop().time()
        await asyncio.gather(
            *(_check_one(sender, ls, appid, member_id, gid, stats) for gid in batch),
            return_exceptions=True)
        elapsed = asyncio.get_event_loop().time() - start
        if i + _RATE_PER_SEC < len(gids) and elapsed < 1:
            await asyncio.sleep(1 - elapsed)


async def _run_check(event, bot):
    """后台独立任务: 检测当前机器人并汇报结果"""
    global _running
    stats = {'scanned': 0, 'kicked': 0, 'partial': 0, 'failed': 0, 'no_openid': []}
    try:
        await _check_bot(bot, stats)
    finally:
        _running = False

    await reply(
        event,
        f"✅ 群检测完成\n"
        f"扫描群数: {stats['scanned']}\n"
        f"被踢群(已记录待删除): {stats['kicked']}\n"
        f"疑似(仅命中一个错误码, 已记录未删除): {stats['partial']}\n"
        f"其他失败(未处理): {stats['failed']}\n"
        + (f"无 openid, 已用随机 id 探测的机器人: {', '.join(stats['no_openid'])}\n" if stats['no_openid'] else '')
        + "\n发送「删除被踢群」执行删除, 待删除列表: 群检测_待删除.json")


@handler(r'^群检测$', name='群检测', desc='检测机器人是否还在群内, 不在则删除群记录', owner_only=True)
async def group_check(event, match):
    global _running
    if _running:
        return await reply(event, '⏳ 群检测正在进行中, 请稍后再试')

    bot = _get_bot(event.appid)
    if bot is None:
        return await reply(event, '❌ 未找到当前机器人实例')

    _running = True
    asyncio.create_task(_run_check(event, bot))
    await reply(event, f'🔍 检测已在后台启动 (每秒 {_RATE_PER_SEC} 并发), 完成后将汇报结果')


@handler(r'^删除被踢群$', name='删除被踢群', desc='删除群检测记录的被踢群数据', owner_only=True)
async def delete_kicked_groups(event, match):
    bot = _get_bot(event.appid)
    if bot is None:
        return await reply(event, '❌ 未找到当前机器人实例')
    ls = getattr(bot, 'log_service', None)
    if ls is None:
        return await reply(event, '❌ 服务不可用')

    async with _pending_lock:
        pending = await asyncio.to_thread(_load_pending)
        groups = pending.pop(event.appid, {})
        if not groups:
            return await reply(event, '📋 没有待删除的被踢群, 请先发送「群检测」')
        for gid, info in groups.items():
            ls.db_queue('DELETE FROM groups_users WHERE group_id=?', (gid,))
            await asyncio.to_thread(
                _append_log, _REMOVED_LOG, event.appid, gid, info.get('response'))
        await asyncio.to_thread(_save_pending, pending)

    await reply(
        event,
        f"✅ 已删除 {len(groups)} 个被踢群的记录\n"
        f"响应已记录到: 群检测_删除记录.txt")
