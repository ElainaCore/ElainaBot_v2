"""消息管理 — 聊天列表/历史/发送/昵称"""

import json
import time
import asyncio
import random
from datetime import datetime, timedelta, date as _date

from aiohttp import web
import base64
from core.base.logger import report_error, report_error_raw, FRAMEWORK

_nickname_cache = {}
_CACHE_TIMEOUT = 86400
_base_dir = ''
_bot_manager = None


def set_context(base_dir: str, bot_manager=None):
    global _base_dir, _bot_manager
    _base_dir = base_dir
    _bot_manager = bot_manager


def _get_nickname(user_id):
    if not user_id:
        return "未知用户"
    cached = _nickname_cache.get(user_id)
    if cached and time.time() - cached['ts'] < _CACHE_TIMEOUT:
        return cached['name']
    # 从 data.db 查 users.name
    if _bot_manager:
        for inst in _bot_manager._bots.values():
            try:
                r = inst.log_service.query_data(
                    "SELECT name FROM users WHERE user_id=?", (user_id,))
                if r and r[0].get('name'):
                    name = r[0]['name']
                    _nickname_cache[user_id] = {'name': name, 'ts': time.time()}
                    return name
            except Exception:
                pass
    return f"用户{user_id[-6:]}"


def _iter_bots(appid_filter=''):
    """按 appid 过滤机器人迭代器; 空字符串=全部"""
    if not _bot_manager:
        return []
    if appid_filter and appid_filter in _bot_manager._bots:
        return [(appid_filter, _bot_manager._bots[appid_filter])]
    return list(_bot_manager._bots.items())


def _recent_dates(days=1):
    """返回最近 N 天的日期字符串列表 (含今天)"""
    today = _date.today()
    return [(today - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(days)]


def _query_messages(date=None, limit=500, appid_filter='', days=1):
    """从机器人的 SQLite 查询消息, 支持多天查询"""
    if not _bot_manager:
        return []
    dates = [date] if date else _recent_dates(days)
    results = []
    for appid, inst in _iter_bots(appid_filter):
        bot_qq = getattr(inst, 'robot_qq', '') or ''
        bot_name = getattr(inst, 'name', appid)
        for d in dates:
            try:
                rows = inst.log_service.query(
                    'message',
                    f"SELECT * FROM log ORDER BY timestamp DESC, id DESC LIMIT {limit}",
                    date=d,
                )
                for r in rows:
                    r['appid'] = appid
                    r['bot_name'] = bot_name
                    r['bot_qq'] = bot_qq
                    r['_date'] = d
                results.extend(rows)
            except Exception:
                pass
    results.sort(key=lambda r: (r.get('timestamp', ''), r.get('id', 0)))
    return results


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
    """获取聊天列表 — 从 SQLite 消息日志汇总, 支持翻页 (每页50)"""
    try:
        body = await request.json()
    except Exception:
        body = {}
    chat_type = body.get('type', 'group')
    search = body.get('search', '').lower()
    date = body.get('date')  # 可选, 默认查3天
    appid_filter = body.get('appid', '')
    page = max(int(body.get('page', 1)), 1)
    page_size = min(int(body.get('page_size', 50)), 100)

    loop = asyncio.get_event_loop()
    rows = await loop.run_in_executor(None, _query_messages, date, 5000, appid_filter, 3)

    chats_map = {}
    for r in rows:
        uid = r.get('user_id', '')
        gid = r.get('group_id', '')
        ts = r.get('timestamp', '')
        content = r.get('content', '')

        if chat_type == 'group' and gid:
            key = gid
        elif chat_type == 'user' and uid and not gid:
            key = uid
        else:
            continue

        if key not in chats_map:
            nick = f'群{key[-6:]}' if chat_type == 'group' else _get_nickname(key)
            chats_map[key] = {
                'chat_id': key,
                'appid': r.get('appid', ''),
                'bot_name': r.get('bot_name', ''),
                'nickname': nick,
                'avatar': key[0].upper() if key else '?',
                'last_time': ts,
                'last_content': content,
                'msg_count': 0,
            }
        chats_map[key]['last_time'] = ts
        chats_map[key]['last_content'] = content
        chats_map[key]['msg_count'] += 1

    chats = sorted(chats_map.values(), key=lambda c: c['last_time'], reverse=True)
    if search:
        chats = [c for c in chats if search in c['chat_id'].lower() or search in c['nickname'].lower()]

    total = len(chats)
    start = (page - 1) * page_size
    paged = chats[start:start + page_size]

    return web.json_response({'success': True, 'data': {
        'chats': paged, 'total': total, 'page': page, 'page_size': page_size,
    }})


async def handle_get_chat_history(request: web.Request):
    """获取聊天记录 — 从 SQLite 消息日志过滤"""
    try:
        body = await request.json()
    except Exception:
        body = {}
    chat_type = body.get('chat_type', 'group')
    chat_id = body.get('chat_id', '')
    date = body.get('date')
    appid_filter = body.get('appid', '')

    if not chat_id:
        return web.json_response({'success': True, 'data': {'messages': []}})

    loop = asyncio.get_event_loop()
    rows = await loop.run_in_executor(None, _query_messages, date, 5000, appid_filter, 3)

    messages = []
    for r in rows:
        uid = r.get('user_id', '')
        gid = r.get('group_id', '')
        content = r.get('content', '')
        msg_type = r.get('type', '')

        # 匹配聊天会话
        if chat_type == 'group':
            if gid != chat_id:
                continue
        elif uid != chat_id or gid:
            continue

        is_bot = msg_type in ('plugin', 'onebot_send') or (content.startswith(('[Bot回复]', '[Bot:')) if content else False)

        # 清理旧的 [Bot:xxx] 前缀
        if content.startswith('[Bot:'):
            idx = content.find('] ')
            if idx > 0:
                content = content[idx + 2:]

        plugin_name = r.get('plugin_name', '')
        source = 'web_panel' if plugin_name == 'WebPanel' else ('onebot' if msg_type in ('onebot_send', 'onebot_recv') else '')
        messages.append({
            'id': r.get('id', len(messages)),
            'user_id': uid,
            'appid': r.get('appid', ''),
            'bot_qq': r.get('bot_qq', '') if is_bot else '',
            'nickname': (r.get('bot_name', '') or 'Bot') if is_bot else _get_nickname(uid),
            'content': content,
            'timestamp': r.get('timestamp', ''),
            'is_self': is_bot,
            'source': source,
        })

    # 取最近一条非 bot 消息的 message_id 用于发送回复 (仅当天, 避免过期)
    last_msg_id = ''
    today_str = _date.today().strftime('%Y-%m-%d')
    for r in reversed(rows):
        if r.get('_date', '') != today_str:
            continue
        mid = r.get('message_id', '')
        if mid and r.get('type') != 'plugin':
            uid = r.get('user_id', '')
            gid = r.get('group_id', '')
            if (chat_type == 'group' and gid == chat_id) or \
               (chat_type == 'user' and uid == chat_id and not gid):
                last_msg_id = mid
                break

    return web.json_response({'success': True, 'data': {
        'messages': messages[-300:], 'last_msg_id': last_msg_id,
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
    if not _bot_manager:
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

        if not chat_type or not chat_id:
            return web.json_response({'success': False, 'message': '缺少 chat_type/chat_id'}, status=400)
        if not content and not image_data and msg_type != 'ark':
            return web.json_response({'success': False, 'message': '消息内容为空'}, status=400)

        # 找到对应 bot
        bot = None
        if appid and appid in _bot_manager._bots:
            bot = _bot_manager._bots[appid]
        elif _bot_manager._bots:
            bot = next(iter(_bot_manager._bots.values()))
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

        if msg_type == 'media' and content:
            ok, data = await _send_media_url(
                sender, content, file_type=media_file_type,
                group_id=gid, user_id=uid, msg_id=msg_id)

        elif msg_type == 'ark' and content:
            ok, data = await _send_ark(
                sender, ark_template_id, content,
                group_id=gid, user_id=uid, msg_id=msg_id)

        elif msg_type == 'text' and image_data:
            ok, data = await _send_text_with_image(
                sender, content, image_data,
                group_id=gid, user_id=uid, msg_id=msg_id)

        else:
            api_msg_type = 2 if msg_type == 'markdown' else 0
            if chat_type == 'group':
                ok, data, actual_payload = await sender.send_to_group(
                    chat_id, content, msg_id=msg_id, msg_type=api_msg_type)
            else:
                ok, data, actual_payload = await sender.send_to_user(
                    chat_id, content, msg_id=msg_id, msg_type=api_msg_type)

        # 保存图片到本地缓存 (data/media/)
        media_label = sender._save_media(image_data, 1) if image_data else ''

        # 构建显示内容
        display = _build_display(msg_type, content, image_data, media_file_type, ark_template_id, media_label)

        send_payload = locals().get('actual_payload') or {'msg_type': msg_type, 'content': content}
        if ok:
            _log_sent_message(bot, chat_type, chat_id, display, bot_appid, bot_name, bot_qq, send_payload)
            return web.json_response({'success': True, 'message': '发送成功'})
        err_msg = data.get('message', '发送失败') if isinstance(data, dict) else str(data)
        _log_send_error(bot, msg_type, chat_type, chat_id, send_payload, data, bot_appid, msg_id)
        return web.json_response({'success': False, 'message': err_msg})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return web.json_response({'success': False, 'message': str(e)}, status=500)


# ==================== 日志记录 ====================

def _build_display(msg_type, content, image_data, media_file_type, ark_template_id, media_label=''):
    """构建日志显示内容"""
    if msg_type == 'media':
        type_names = {1: '图片', 2: '视频', 3: '语音', 4: '文件'}
        return f"[富媒体:{type_names.get(media_file_type, '?')}] {content[:200]}"
    if msg_type == 'ark':
        return f"[ARK:{ark_template_id}] {content[:200]}"
    if msg_type == 'markdown':
        return f"[Markdown] {content[:200]}"
    if image_data and media_label:
        return f"{content[:200]}\n{media_label}" if content else media_label
    return content[:200]


def _log_sent_message(bot, chat_type, chat_id, display, bot_appid, bot_name, bot_qq='', payload=None):
    """成功发送 → 写消息数据库 + 推送到面板"""
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    group_id = chat_id if chat_type == 'group' else ''
    user_id = chat_id if chat_type != 'group' else ''
    raw = json.dumps(payload, ensure_ascii=False, default=str) if payload else display

    # 写 message.db
    try:
        log_service = getattr(bot, 'log_service', None)
        if log_service:
            asyncio.ensure_future(log_service.add('message', {
                'type': 'plugin',
                'user_id': user_id,
                'group_id': group_id,
                'content': display,
                'plugin_name': 'WebPanel',
                'raw_message': raw,
            }))
    except Exception:
        pass

    # 推送到面板实时日志
    try:
        import web.ws as _ws
        _ws.push_log('message', {
            'appid': bot_appid,
            'bot_name': bot_name,
            'bot_qq': bot_qq,
            'user_id': user_id,
            'group_id': group_id,
            'content': display,
            'is_bot': True,
            'direction': 'send',
            'source': 'web_panel',
            'plugin_name': 'WebPanel',
        })
    except Exception:
        pass


def _log_send_error(bot, msg_type, chat_type, chat_id, send_payload, api_resp, bot_appid, msg_id=''):
    """发送失败 → 写报错数据库
    content=接收原始消息(来源信息), traceback=API报错响应, context=发送载荷(完整)
    """
    report_error_raw(
        FRAMEWORK, 'Web消息发送',
        content=f"[WebPanel] chat_type={chat_type} chat_id={chat_id} msg_id={msg_id}",
        tb=json.dumps(api_resp, ensure_ascii=False, default=str)[:2000] if api_resp else '',
        context=json.dumps(send_payload, ensure_ascii=False, default=str)[:2000] if send_payload else '',
        appid=bot_appid,
    )


def _log_upload_error(sender, endpoint, resp_data, detail=''):
    """上传失败 → 写报错数据库"""
    err_msg = resp_data.get('message', '') if isinstance(resp_data, dict) else str(resp_data)
    err_code = resp_data.get('code', '') if isinstance(resp_data, dict) else ''
    report_error(FRAMEWORK, "Web媒体上传",
                 f"上传失败 {detail} → {err_code}: {err_msg}",
                 context={
                     'appid': getattr(sender, '_appid', ''),
                     'endpoint': endpoint,
                     'api_response': json.dumps(resp_data, ensure_ascii=False, default=str)[:1000]
                         if resp_data else '',
                 })


# ==================== 辅助: 富媒体 ====================

def _media_endpoints(group_id, user_id):
    """返回 (upload_ep, send_ep)"""
    if group_id:
        return f"/v2/groups/{group_id}/files", f"/v2/groups/{group_id}/messages"
    return f"/v2/users/{user_id}/files", f"/v2/users/{user_id}/messages"


async def _web_send_media(sender, *, file_info, content='', group_id=None, user_id=None, msg_id=''):
    """file_info 已就绪, 直接发送富媒体消息"""
    _, send_ep = _media_endpoints(group_id, user_id)
    payload = {'msg_type': 7, 'msg_seq': random.randint(10000, 999999),
               'content': content or '', 'media': {'file_info': file_info}}
    if msg_id:
        payload['msg_id'] = msg_id
    return await sender.post_json(send_ep, payload)


async def _send_media_url(sender, url, *, file_type=1, group_id=None, user_id=None, msg_id=''):
    """通过 URL 上传并发送富媒体"""
    upload_ep, _ = _media_endpoints(group_id, user_id)
    ok, resp = await sender.post_json(upload_ep,
                                       {'srv_send_msg': False, 'file_type': file_type, 'url': url})
    if not ok:
        _log_upload_error(sender, upload_ep, resp, f'URL上传 file_type={file_type}')
        return False, resp
    file_info = resp.get('file_info')
    if not file_info:
        _log_upload_error(sender, upload_ep, resp, 'URL上传返回无file_info')
        return False, {'message': '上传失败: 无 file_info'}
    return await _web_send_media(sender, file_info=file_info,
                                  group_id=group_id, user_id=user_id, msg_id=msg_id)


async def _send_text_with_image(sender, content, image_bytes, *, group_id=None, user_id=None, msg_id=''):
    """上传图片 bytes 并发送"""
    if not image_bytes:
        return False, {'message': '图片数据为空'}
    upload_ep, _ = _media_endpoints(group_id, user_id)
    ok, resp = await sender.post_json(upload_ep, {
        'srv_send_msg': False, 'file_type': 1,
        'file_data': base64.b64encode(image_bytes).decode(),
    })
    if not ok:
        return False, resp
    file_info = resp.get('file_info')
    if not file_info:
        return False, {'message': '上传失败: 无 file_info'}
    return await _web_send_media(sender, file_info=file_info, content=content,
                                  group_id=group_id, user_id=user_id, msg_id=msg_id)


# ==================== 辅助: ARK ====================

async def _send_ark(sender, template_id, kv_json_str, *, group_id=None, user_id=None, msg_id=''):
    """发送 ARK 消息

    template_id: ARK 模板 ID (23, 24, 37 等)
    kv_json_str: kv 数据的 JSON 字符串 (数组)
    """
    try:
        kv_data = json.loads(kv_json_str)
    except json.JSONDecodeError as e:
        return False, {'message': f'ARK kv JSON 解析失败: {e}'}

    if not isinstance(kv_data, list):
        return False, {'message': 'ARK kv 必须是 JSON 数组'}

    payload = {
        'msg_type': 3, 'msg_seq': random.randint(10000, 999999),
        'content': '',
        'ark': {'template_id': template_id, 'kv': kv_data},
    }
    if msg_id:
        payload['msg_id'] = msg_id

    if group_id:
        return await sender.post_json(f"/v2/groups/{group_id}/messages", payload)
    return await sender.post_json(f"/v2/users/{user_id}/messages", payload)
