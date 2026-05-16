"""消息管理 — HTTP 请求处理器"""

import json
import time
import asyncio
from datetime import date as _date

from aiohttp import web

import web.tools._message.shared as _shared
from web.tools._message.shared import (
    _get_nickname, _batch_get_nicknames, _get_bot, _get_full_access_group_ids,
)
from web.tools._message.query import (
    _query_chat_messages_sync, _query_older_messages_sync, _aggregate_chats_sync,
)
from web.tools._message.log_utils import _build_display, _log_sent_message, _log_send_error
from web.tools._message.media import _send_media_url, _send_text_with_image, _send_ark

# 聊天列表短期缓存 (避免多次刷新同一详情重复诡汇总查询)
_chat_list_cache = {}  # {(chat_type, appid_filter): (timestamp, chats)}
_CHAT_LIST_TTL = 30  # 秒
_chat_list_lock = None  # asyncio.Lock, 延迟初始化


async def handle_get_nickname(request: web.Request):
    body = await request.json()
    uid = body.get('user_id', '')
    if not uid:
        return web.json_response({'success': False, 'message': '缺少用户ID'}, status=400)
    return web.json_response({'success': True, 'data': {'user_id': uid, 'nickname': _get_nickname(uid)}})


async def handle_get_nicknames_batch(request: web.Request):
    body = await request.json()
    uids = body.get('user_ids', [])
    if not uids or not isinstance(uids, list):
        return web.json_response({'success': False, 'message': '缺少用户ID列表'}, status=400)
    result = {uid: _get_nickname(uid) for uid in uids}
    return web.json_response({'success': True, 'data': {'nicknames': result}})


async def handle_get_chats(request: web.Request):
    """获取聊天列表 — SQL GROUP BY 聚合 + 批量昵称 + 短期缓存"""
    try:
        body = await request.json()
    except Exception:
        body = {}
    chat_type = body.get('type', 'group')
    search = body.get('search', '').lower()
    appid_filter = body.get('appid', '')
    page = max(int(body.get('page', 1)), 1)
    page_size = min(int(body.get('page_size', 50)), 100)
    days = max(1, min(3, int(body.get('days', 1))))

    global _chat_list_lock
    if _chat_list_lock is None:
        _chat_list_lock = asyncio.Lock()

    cache_key = (chat_type, appid_filter, days)
    now = time.time()
    cached = _chat_list_cache.get(cache_key)
    if cached and now - cached[0] < _CHAT_LIST_TTL:
        chats = cached[1]
    else:
        async with _chat_list_lock:
            cached = _chat_list_cache.get(cache_key)
            if cached and time.time() - cached[0] < _CHAT_LIST_TTL:
                chats = cached[1]
            else:
                loop = asyncio.get_event_loop()
                query_type = 'group' if chat_type == 'full_access' else chat_type
                chats = await loop.run_in_executor(None, _aggregate_chats_sync, query_type, appid_filter, days)
                if chat_type == 'user':
                    ids = [c['chat_id'] for c in chats]
                    nicks = await loop.run_in_executor(None, _batch_get_nicknames, ids)
                    for c in chats:
                        c['nickname'] = nicks.get(c['chat_id'], f"用户{c['chat_id'][-6:]}")
                else:
                    fa_ids = _get_full_access_group_ids()
                    for c in chats:
                        c['nickname'] = f"群{c['chat_id'][-6:]}"
                        c['is_full_access'] = c['chat_id'] in fa_ids
                    if chat_type == 'full_access':
                        chats = [c for c in chats if c['is_full_access']]
                _chat_list_cache[cache_key] = (time.time(), chats)

    if search:
        chats = [c for c in chats if search in c['chat_id'].lower() or search in c.get('nickname', '').lower()]

    total = len(chats)
    start = (page - 1) * page_size
    paged = chats[start:start + page_size]

    return web.json_response({'success': True, 'data': {
        'chats': paged, 'total': total, 'page': page, 'page_size': page_size,
    }})


async def handle_get_chat_history(request: web.Request):
    """获取聊天记录 — 支持按日期分页加载

    before_date: 可选, YYYY-MM-DD, 加载该日期之前的消息 (往前搜索到有数据为止)
    不传则加载今天的消息
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    chat_type = body.get('chat_type', 'group')
    chat_id = body.get('chat_id', '')
    appid_filter = body.get('appid', '')
    before_date = body.get('before_date', '')

    if not chat_id:
        return web.json_response({'success': True, 'data': {'messages': [], 'has_more': False}})

    loop = asyncio.get_event_loop()

    if before_date:
        rows, oldest_date, has_more = await loop.run_in_executor(
            None, _query_older_messages_sync, chat_type, chat_id, appid_filter, before_date, 300, 14)
    else:
        rows = await loop.run_in_executor(
            None, _query_chat_messages_sync, chat_type, chat_id, appid_filter, 1, 300)
        oldest_date = _date.today().strftime('%Y-%m-%d')
        has_more = True

    # 收集需要查询的 user_id (仅非bot消息), 批量取昵称
    uid_set = set()
    for r in rows:
        if r.get('direction') != 'send':
            uid = r.get('user_id', '')
            if uid:
                uid_set.add(uid)
    nicks = await loop.run_in_executor(None, _batch_get_nicknames, list(uid_set)) if uid_set else {}

    messages = []
    for r in rows:
        uid = r.get('user_id', '')
        content = r.get('content', '')
        msg_type = r.get('type', '')
        is_bot = r.get('direction') == 'send'

        if content.startswith('[Bot:'):
            idx = content.find('] ')
            if idx > 0:
                content = content[idx + 2:]

        plugin_name = r.get('plugin_name', '')
        source = 'web_panel' if plugin_name == 'WebPanel' else (
            'onebot' if msg_type in ('onebot_send', 'onebot_recv') else '')
        raw = r.get('raw_message', '')
        recalled = raw == '[recalled]'
        messages.append({
            'id': r.get('id', len(messages)),
            'message_id': r.get('message_id', ''),
            'user_id': uid,
            'appid': r.get('appid', ''),
            'bot_qq': r.get('bot_qq', '') if is_bot else '',
            'nickname': (r.get('bot_name', '') or 'Bot') if is_bot else nicks.get(uid, f"用户{uid[-6:]}" if uid else '未知用户'),
            'content': content,
            'timestamp': r.get('timestamp', ''),
            'is_self': is_bot,
            'source': source,
            'raw_message': raw if not recalled else '',
            'recalled': recalled,
        })

    # 取最近一条非 bot 消息的 message_id 用于发送回复 (仅初始加载)
    last_msg_id = ''
    if not before_date:
        today_str = _date.today().strftime('%Y-%m-%d')
        for r in reversed(rows):
            if r.get('_date', '') != today_str:
                continue
            mid = r.get('message_id', '')
            if mid and r.get('type') != 'plugin' and r.get('direction') != 'send':
                last_msg_id = mid
                break

    return web.json_response({'success': True, 'data': {
        'messages': messages, 'last_msg_id': last_msg_id,
        'oldest_date': oldest_date, 'has_more': has_more,
    }})


async def handle_send_message(request: web.Request):
    """发送消息 (支持 multipart/form-data)

    参数:
        chat_type:       group | user
        chat_id:         群/用户 openid
        appid:           机器人 appid
        msg_type:        text | markdown | media | ark
        content:         文本内容 / 资源URL (media) / ARK kv JSON (ark)
        msg_id:          回复消息 ID (被动回复需要)
        image:           图片文件 (仅 text 模式, 与 content 一起发送)
        media_file_type: 富媒体文件类型 1=图片 2=视频 3=语音 4=文件 (仅 media)
        ark_template_id: ARK 模板 ID (仅 ark)
    """
    if not _shared._bot_manager:
        return web.json_response({'success': False, 'message': '机器人管理器未初始化'}, status=500)

    try:
        # 支持 multipart/form-data 和 JSON
        if request.content_type and 'multipart' in request.content_type:
            reader = await request.multipart()
            fields = {}
            image_data = None
            while True:
                part = await reader.next()
                if part is None:
                    break
                name = part.name
                if name == 'image':
                    image_data = await part.read()
                else:
                    fields[name] = (await part.read()).decode('utf-8', errors='replace')
        else:
            fields = await request.json()
            image_data = None

        chat_type = fields.get('chat_type', '')
        chat_id = fields.get('chat_id', '')
        appid = fields.get('appid', '')
        msg_type = fields.get('msg_type', 'text')
        content = fields.get('content', '').strip()
        msg_id = fields.get('msg_id', '')
        media_file_type = int(fields.get('media_file_type', '1'))
        ark_template_id = int(fields.get('ark_template_id', '23'))

        # 全量群只用主动消息, 不需要被动消息
        if chat_type == 'group' and chat_id in _get_full_access_group_ids():
            msg_id = ''

        if not chat_type or not chat_id:
            return web.json_response({'success': False, 'message': '缺少 chat_type/chat_id'}, status=400)
        if not content and not image_data and msg_type != 'ark':
            return web.json_response({'success': False, 'message': '消息内容为空'}, status=400)

        bot = _get_bot(appid)
        if not bot:
            return web.json_response({'success': False, 'message': '无可用机器人'}, status=400)

        sender = bot.sender
        bot_appid = getattr(bot, 'appid', '') or appid
        bot_name = getattr(bot, 'name', '') or bot_appid
        bot_qq = getattr(bot, 'robot_qq', '') or ''

        # 根据消息类型发送
        is_group = chat_type == 'group'
        gid = chat_id if is_group else None
        uid = chat_id if not is_group else None

        # 发送 — sender.send_to_* 内部已记录日志, 其余路径需手动记录
        need_log = True
        if msg_type == 'media' and content:
            ok, data = await _send_media_url(sender, content, file_type=media_file_type, group_id=gid, user_id=uid, msg_id=msg_id)
        elif msg_type == 'ark' and content:
            ok, data = await _send_ark(sender, ark_template_id, content, group_id=gid, user_id=uid, msg_id=msg_id)
        elif msg_type == 'text' and image_data:
            ok, data = await _send_text_with_image(sender, content, image_data, group_id=gid, user_id=uid, msg_id=msg_id)
        else:
            need_log = False
            api_msg_type = 2 if msg_type == 'markdown' else 0
            send_fn = sender.send_to_group if is_group else sender.send_to_user
            ok, data, _ = await send_fn(chat_id, content, msg_id=msg_id, msg_type=api_msg_type, skip_suffix=True)

        if ok:
            if need_log:
                media_label = sender._save_media(image_data, 1) if image_data else ''
                display = _build_display(msg_type, content, image_data, media_file_type, ark_template_id, media_label)
                _log_sent_message(bot, chat_type, chat_id, display, bot_appid, bot_name, bot_qq)
            return web.json_response({'success': True, 'message': '发送成功'})
        err_msg = data.get('message', '发送失败') if isinstance(data, dict) else str(data)
        _log_send_error(bot, msg_type, chat_type, chat_id, {}, data, bot_appid, msg_id)
        return web.json_response({'success': False, 'message': err_msg})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return web.json_response({'success': False, 'message': str(e)}, status=500)


async def handle_recall_message(request: web.Request):
    """撤回消息

    参数: chat_type, chat_id, appid, message_id
    """
    if not _shared._bot_manager:
        return web.json_response({'success': False, 'message': '机器人管理器未初始化'}, status=500)
    try:
        body = await request.json()
    except Exception:
        body = {}
    chat_type = body.get('chat_type', '')
    chat_id = body.get('chat_id', '')
    appid = body.get('appid', '')
    message_id = body.get('message_id', '')

    if not message_id or not chat_id or chat_type not in ('group', 'user'):
        return web.json_response({'success': False, 'message': '参数缺失'}, status=400)

    bot = _get_bot(appid)
    if not bot:
        return web.json_response({'success': False, 'message': '无可用机器人'}, status=400)

    from urllib.parse import quote
    endpoint = f"/v2/{'groups' if chat_type == 'group' else 'users'}/{chat_id}/messages/{quote(message_id, safe='')}"

    try:
        ok, data = await bot.sender.delete(endpoint)
    except Exception as e:
        return web.json_response({'success': False, 'message': str(e)}, status=500)

    if ok:
        # 标记消息为已撤回 (raw_message 设为 [recalled])
        try:
            _mark_recalled(bot, message_id)
        except Exception:
            pass
        return web.json_response({'success': True})
    err = data.get('message', '撤回失败') if isinstance(data, dict) else str(data)
    return web.json_response({'success': False, 'message': err})


def _mark_recalled(bot, message_id):
    """在数据库中标记消息为已撤回"""
    from datetime import date as _d, timedelta
    today = _d.today()
    dates = [(today - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(3)]
    sql = "UPDATE log SET raw_message='[recalled]' WHERE message_id=?"
    for d in dates:
        try:
            bot.log_service.query('message', sql, (message_id,), date=d)
        except Exception:
            pass
