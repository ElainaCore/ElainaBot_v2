"""消息管理 — HTTP 请求处理器"""

import asyncio
import contextlib
import json
import logging
import os
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import date as _date
from datetime import timedelta
from typing import Any, cast
from urllib.parse import quote

from aiohttp import BodyPartReader, web

import web.tools._message.shared as _shared
from core.base.tasks import spawn
from web.tools._message.log_utils import (
    _build_display,
    _log_send_error,
    _log_sent_message,
)
from web.tools._message.media import _send_ark, _send_media_url, _send_text_with_image
from web.tools._message.query import (
    _aggregate_chats_sync,
    _query_chat_messages_sync,
    _query_lifecycle_events_sync,
    _query_older_messages_sync,
    _recent_dates,
)
from web.tools._message.shared import (
    _batch_get_nicknames,
    _get_bot,
    _get_full_access_group_ids,
    _get_nickname,
)

log = logging.getLogger('ElainaBot.web.message')

_chat_list_cache: dict[tuple[str, str], tuple[float, list[dict[str, Any]]]] = {}
_CHAT_LIST_TTL = 10
_chat_list_lock = None  # asyncio.Lock, 延迟初始化 (仅首次无缓存时使用)
_chat_refreshing: set[tuple[str, str]] = set()  # 后台刷新中的 cache_key, 防重复刷新
_chat_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='chat-agg')


def _lookup_reference_id(bot, chat_type, chat_id, message_id):
    """根据 message_id 查出 message 表单独记录的 REFIDX。"""
    if not bot or not message_id:
        return ''
    if str(message_id).startswith('REFIDX_'):
        return str(message_id)
    where = "message_id = ? AND reference_id != ''"
    params = (message_id,)
    if chat_type == 'group':
        where += ' AND group_id = ?'
        params += (chat_id,)
    else:
        where += ' AND user_id = ?'
        params += (chat_id,)
    sql = f'SELECT reference_id FROM log WHERE {where} ORDER BY id DESC LIMIT 1'
    today = _date.today()
    for offset in range(3):
        d = (today - timedelta(days=offset)).strftime('%Y-%m-%d')
        rows = bot.log_service.query('message', sql, params, date=d)
        if not rows:
            continue
        ref = rows[0].get('reference_id', '') or ''
        if ref:
            return str(ref)
    return ''


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


async def _build_chat_list(chat_type, appid_filter):
    """构建聊天列表 (阻塞聚合在专用线程池执行)"""
    loop = asyncio.get_running_loop()
    if chat_type in ('full_access', 'remark'):
        # 全量群/备注群直接从 data.db 获取
        fa_ids = _get_full_access_group_ids()
        bot = next(iter(_shared._bot_manager._bots.values()), None) if _shared._bot_manager else None
        appid_default = next(iter(_shared._bot_manager._bots), '') if _shared._bot_manager and _shared._bot_manager._bots else ''
        remarks = _load_remarks()
        source_ids = set(remarks.keys()) if chat_type == 'remark' else fa_ids
        return [{
            'chat_id': gid,
            'appid': appid_filter or appid_default,
            'bot_name': getattr(bot, 'name', appid_default) if bot else '',
            'nickname': _remark_name(remarks.get(gid)) or f'群{gid[-6:]}',
            'remark': _remark_name(remarks.get(gid)),
            'group_qq': _remark_qq(remarks.get(gid)),
            'is_full_access': gid in fa_ids,
        } for gid in source_ids]
    chats = await loop.run_in_executor(_chat_executor, _aggregate_chats_sync, chat_type, appid_filter)
    if chat_type == 'user':
        ids = [c['chat_id'] for c in chats]
        nicks = await loop.run_in_executor(_chat_executor, _batch_get_nicknames, ids)
        for c in chats:
            c['nickname'] = nicks.get(c['chat_id'], f'用户{c["chat_id"][-6:]}')
    else:
        fa_ids = _get_full_access_group_ids()
        remarks = _load_remarks()
        for c in chats:
            r_val = remarks.get(c['chat_id'])
            c['nickname'] = _remark_name(r_val) or f'群{c["chat_id"][-6:]}'
            c['remark'] = _remark_name(r_val)
            c['group_qq'] = _remark_qq(r_val)
            c['is_full_access'] = c['chat_id'] in fa_ids
    return chats


async def _refresh_chat_list(cache_key, chat_type, appid_filter):
    """后台刷新聊天列表缓存"""
    try:
        chats = await _build_chat_list(chat_type, appid_filter)
        _chat_list_cache[cache_key] = (time.time(), chats)
    except Exception as e:
        log.debug(f'聊天列表缓存刷新失败: {e}')
    finally:
        _chat_refreshing.discard(cache_key)


async def handle_get_chats(request: web.Request):
    """获取聊天列表 — SQL GROUP BY 聚合 + 批量昵称 + 缓存 (过期先返旧数据, 后台刷新)"""
    try:
        body = await request.json()
    except Exception:
        body = {}
    chat_type = body.get('type', 'group')
    search = body.get('search', '').lower()
    appid_filter = body.get('appid', '')
    page = max(int(body.get('page', 1)), 1)
    page_size = min(int(body.get('page_size', 50)), 100)

    global _chat_list_lock
    if _chat_list_lock is None:
        _chat_list_lock = asyncio.Lock()

    cache_key = (chat_type, appid_filter)
    cached = _chat_list_cache.get(cache_key)
    if cached:
        # 命中缓存 (即使过期) 立即返回, 过期时后台刷新, 避免请求在锁后排队超时
        chats = cached[1]
        if time.time() - cached[0] >= _CHAT_LIST_TTL and cache_key not in _chat_refreshing:
            _chat_refreshing.add(cache_key)
            spawn(_refresh_chat_list(cache_key, chat_type, appid_filter))
    else:
        # 首次无缓存, 同类请求合并等待一次构建
        async with _chat_list_lock:
            cached = _chat_list_cache.get(cache_key)
            if cached:
                chats = cached[1]
            else:
                chats = await _build_chat_list(chat_type, appid_filter)
                _chat_list_cache[cache_key] = (time.time(), chats)

    if search:
        chats = [c for c in chats if search in c['chat_id'].lower() or search in c.get('nickname', '').lower() or search in c.get('remark', '').lower()]

    total = len(chats)
    start = (page - 1) * page_size
    paged = chats[start : start + page_size]

    return web.json_response(
        {
            'success': True,
            'data': {
                'chats': paged,
                'total': total,
                'page': page,
                'page_size': page_size,
            },
        }
    )


async def handle_get_chat_history(request: web.Request):
    """获取聊天记录"""
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

    loop = asyncio.get_running_loop()

    if before_date:
        rows, oldest_date, has_more = await loop.run_in_executor(
            None,
            _query_older_messages_sync,
            chat_type,
            chat_id,
            appid_filter,
            before_date,
            300,
            30,
        )
    else:
        rows = await loop.run_in_executor(None, _query_chat_messages_sync, chat_type, chat_id, appid_filter, 1, 300)
        oldest_date = _date.today().strftime('%Y-%m-%d')
        has_more = True

    # 查询群成员加入/退出事件
    lifecycle_rows = []
    if chat_type == 'group':
        lc_dates = [before_date] if before_date else _recent_dates(1)
        lifecycle_rows = await loop.run_in_executor(
            None, _query_lifecycle_events_sync, chat_type, chat_id, appid_filter, lc_dates
        )

    # 收集需要查询的 user_id (仅非bot消息), 批量取昵称
    uid_set = set()
    for r in rows:
        if r.get('direction') != 'send':
            uid = r.get('user_id', '')
            if uid:
                uid_set.add(uid)
    for r in lifecycle_rows:
        uid = r.get('user_id', '')
        if uid:
            uid_set.add(uid)
    nicks = await loop.run_in_executor(None, _batch_get_nicknames, list(uid_set)) if uid_set else {}

    messages: list[dict[str, object]] = []
    for r in rows:
        uid = r.get('user_id', '')
        content = r.get('content', '')
        is_bot = r.get('direction') == 'send'

        if content.startswith('[Bot:'):
            idx = content.find('] ')
            if idx > 0:
                content = content[idx + 2 :]

        plugin_name = r.get('plugin_name', '')
        source = 'web_panel' if plugin_name == 'WebPanel' else ('onebot' if plugin_name == 'onebot_adapter' else '')
        raw = r.get('raw_message', '')
        recalled = raw == '[recalled]'
        messages.append(
            {
                'id': r.get('id', len(messages)),
                'message_id': r.get('message_id', ''),
                'reference_id': r.get('reference_id', ''),
                'user_id': uid,
                'appid': r.get('appid', ''),
                'bot_qq': r.get('bot_qq', '') if is_bot else '',
                'nickname': (r.get('bot_name', '') or 'Bot') if is_bot else nicks.get(uid, f'用户{uid[-6:]}' if uid else '未知用户'),
                'content': content,
                'timestamp': r.get('timestamp', ''),
                'is_self': is_bot,
                'source': source,
                'raw_message': raw if not recalled else '',
                'recalled': recalled,
            }
        )

    # 将成员加入/退出事件混入消息列表
    for r in lifecycle_rows:
        uid = r.get('user_id', '')
        evt_type = r.get('type', '')
        messages.append(
            {
                'id': f"lc_{r.get('id', 0)}_{r.get('_date', '')}",
                'message_id': '',
                'reference_id': '',
                'user_id': uid,
                'appid': r.get('appid', ''),
                'bot_qq': '',
                'nickname': nicks.get(uid, f'用户{uid[-6:]}' if uid else '未知用户'),
                'content': '',
                'timestamp': r.get('timestamp', ''),
                'is_self': False,
                'source': '',
                'raw_message': '',
                'recalled': False,
                'event_type': 'member_add' if evt_type == 'group_member_add' else 'member_remove',
            }
        )

    # 按 timestamp 排序, 将消息和事件混合
    messages.sort(key=lambda m: str(m.get('timestamp', '')))

    # 取最近一条非 bot 消息的 message_id 用于发送回复 (仅初始加载)
    last_msg_id = ''
    if not before_date:
        today_str = _date.today().strftime('%Y-%m-%d')
        for r in reversed(rows):
            if r.get('_date', '') != today_str:
                continue
            mid = r.get('message_id', '')
            if mid and r.get('direction') != 'send':
                last_msg_id = mid
                break

    return web.json_response(
        {
            'success': True,
            'data': {
                'messages': messages,
                'last_msg_id': last_msg_id,
                'oldest_date': oldest_date,
                'has_more': has_more,
            },
        }
    )


async def handle_send_message(request: web.Request):
    """发送消息"""
    if not _shared._bot_manager:
        return web.json_response({'success': False, 'message': '机器人管理器未初始化'}, status=500)

    try:
        # 支持 multipart/form-data 和 JSON
        if request.content_type and 'multipart' in request.content_type:
            reader = await request.multipart()
            fields = {}
            image_data = None
            while True:
                part = cast(BodyPartReader, await reader.next())
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
        send_mode = fields.get('send_mode', 'default') or 'default'
        custom_id = fields.get('custom_id', '').strip()
        message_reference_id = fields.get('message_reference_id', '').strip()
        quote_message_id = (fields.get('quote_message_id') or fields.get('message_reference_message_id') or '').strip()
        media_file_type = int(fields.get('media_file_type', '1'))
        ark_template_id = int(fields.get('ark_template_id', '23'))

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

        if not message_reference_id and quote_message_id:
            message_reference_id = _lookup_reference_id(bot, chat_type, chat_id, quote_message_id)

        # 发送方式: default=全量群主动/普通群被动, active=主动, passive=被动, custom_msg_id/custom_event_id=手动指定 ID
        event_id = ''
        if send_mode == 'active':
            msg_id = ''
        elif send_mode == 'custom_msg_id':
            msg_id = custom_id
        elif send_mode == 'custom_event_id':
            msg_id = ''
            event_id = custom_id
        elif send_mode != 'passive' and chat_type == 'group' and chat_id in _get_full_access_group_ids():
            # 全量群默认只用主动消息, 不需要被动消息 msg_id, 引用走 message_reference
            msg_id = ''

        # 根据消息类型发送
        is_group = chat_type == 'group'
        gid = chat_id if is_group else None
        uid = chat_id if not is_group else None

        # 发送 — sender.send_to_* 内部已记录日志, 其余路径需手动记录
        need_log = True
        send_payload = {}
        if msg_type == 'media' and content:
            ok, data, send_payload = await _send_media_url(
                sender,
                content,
                file_type=media_file_type,
                group_id=gid,
                user_id=uid,
                msg_id=msg_id,
                event_id=event_id,
                message_reference_id=message_reference_id,
            )
        elif msg_type == 'ark' and content:
            ok, data, send_payload = await _send_ark(
                sender,
                ark_template_id,
                content,
                group_id=gid,
                user_id=uid,
                msg_id=msg_id,
                event_id=event_id,
                message_reference_id=message_reference_id,
            )
        elif msg_type == 'text' and image_data:
            ok, data, send_payload = await _send_text_with_image(
                sender,
                content,
                image_data,
                group_id=gid,
                user_id=uid,
                msg_id=msg_id,
                event_id=event_id,
                message_reference_id=message_reference_id,
            )
        else:
            need_log = False
            api_msg_type = 2 if msg_type == 'markdown' else 0
            send_fn = sender.send_to_group if is_group else sender.send_to_user
            ok, data, send_payload = await send_fn(
                chat_id,
                content,
                msg_id=msg_id,
                event_id=event_id,
                msg_type=api_msg_type,
                skip_suffix=True,
                message_reference_id=message_reference_id,
            )

        if ok:
            if need_log:
                media_label = await sender._save_media(image_data, 1) if image_data else ''
                display = _build_display(
                    msg_type,
                    content,
                    image_data,
                    media_file_type,
                    ark_template_id,
                    media_label,
                )
                _log_sent_message(bot, chat_type, chat_id, display, bot_appid, bot_name, bot_qq, send_payload, data)
            return web.json_response({'success': True, 'message': '发送成功'})
        err_msg = data.get('message', '发送失败') if isinstance(data, dict) else str(data)
        _log_send_error(bot, msg_type, chat_type, chat_id, send_payload, data, bot_appid, msg_id)
        return web.json_response({'success': False, 'message': err_msg})

    except Exception as e:
        traceback.print_exc()
        return web.json_response({'success': False, 'message': str(e)}, status=500)


async def handle_recall_message(request: web.Request):
    """撤回消息"""
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

    endpoint = f'/v2/{"groups" if chat_type == "group" else "users"}/{chat_id}/messages/{quote(message_id, safe="")}'

    try:
        ok, data = await bot.sender.delete(endpoint)
    except Exception as e:
        return web.json_response({'success': False, 'message': str(e)}, status=500)

    if ok:
        # 标记消息为已撤回 (raw_message 设为 [recalled])
        with contextlib.suppress(Exception):
            _mark_recalled(bot, message_id)
        return web.json_response({'success': True})
    err = data.get('message', '撤回失败') if isinstance(data, dict) else str(data)
    return web.json_response({'success': False, 'message': err})


def _mark_recalled(bot, message_id):
    """在数据库中标记消息为已撤回"""
    today = _date.today()
    dates = [(today - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(3)]
    sql = "UPDATE log SET raw_message='[recalled]' WHERE message_id=?"
    svc = bot.log_service
    for d in dates:
        db_path = svc._resolve_db_path('message', date=d)
        if not os.path.isfile(db_path):
            continue
        try:
            conn = svc._get_conn(db_path, 'message')
            lock = svc._conn_locks.get(db_path)
            if lock:
                with lock:
                    conn.execute(sql, (message_id,))
                    conn.commit()
        except Exception as e:
            log.debug(f'标记撤回失败 {d}: {e}')


# ==================== 群备注 (带内存缓存) ====================

_remarks_cache: dict | None = None
_remarks_cache_ts: float = 0
_REMARKS_CACHE_TTL = 60


def _remarks_path():
    return os.path.join(_shared._base_dir, 'data', 'group_remarks.json')


def _remark_name(val):
    if isinstance(val, dict):
        return val.get('name', '')
    return str(val) if val else ''


def _remark_qq(val):
    """获取备注中的群号"""
    if isinstance(val, dict):
        return val.get('qq', '')
    return ''


def _load_remarks() -> dict:
    global _remarks_cache, _remarks_cache_ts
    now = time.time()
    if _remarks_cache is not None and now - _remarks_cache_ts < _REMARKS_CACHE_TTL:
        return _remarks_cache
    path = _remarks_path()
    if not os.path.isfile(path):
        _remarks_cache = {}
        _remarks_cache_ts = now
        return _remarks_cache
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        _remarks_cache = data if isinstance(data, dict) else {}
    except Exception:
        _remarks_cache = {}
    _remarks_cache_ts = now
    return _remarks_cache


def _save_remarks(remarks: dict):
    global _remarks_cache, _remarks_cache_ts
    path = _remarks_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(remarks, f, ensure_ascii=False, indent=2)
    _remarks_cache = remarks
    _remarks_cache_ts = time.time()


def _invalidate_remark_caches():
    for key in list(_chat_list_cache):
        if key[0] in ('remark', 'full_access'):
            _chat_list_cache.pop(key, None)


async def handle_get_remarks(request: web.Request):
    """获取所有群备注 — 返回统一格式 {gid: {name, qq}}"""
    raw = _load_remarks()
    out = {}
    for gid, val in raw.items():
        out[gid] = {'name': _remark_name(val), 'qq': _remark_qq(val)}
    return web.json_response({'success': True, 'data': out})


async def handle_set_remark(request: web.Request):
    """设置群备注 (支持 name + qq)"""
    body = await request.json()
    group_id = body.get('group_id', '')
    remark = body.get('remark', '').strip()
    group_qq = body.get('group_qq', '').strip()
    if not group_id:
        return web.json_response({'success': False, 'message': '缺少 group_id'}, status=400)
    remarks = dict(_load_remarks())
    if remark or group_qq:
        remarks[group_id] = {'name': remark, 'qq': group_qq}
    else:
        remarks.pop(group_id, None)
    _save_remarks(remarks)
    _invalidate_remark_caches()
    return web.json_response({'success': True, 'message': '备注已保存'})


async def handle_delete_remark(request: web.Request):
    """删除群备注"""
    body = await request.json()
    group_id = body.get('group_id', '')
    if not group_id:
        return web.json_response({'success': False, 'message': '缺少 group_id'}, status=400)
    remarks = dict(_load_remarks())
    remarks.pop(group_id, None)
    _save_remarks(remarks)
    _invalidate_remark_caches()
    return web.json_response({'success': True, 'message': '备注已删除'})


# ==================== 群成员权限 (带缓存) ====================

_roles_cache: dict[str, tuple[float, dict[str, str]]] = {}
_ROLES_CACHE_TTL = 120


def _prune_roles_cache(now):
    for gid in [gid for gid, (ts, _) in _roles_cache.items() if now - ts >= _ROLES_CACHE_TTL]:
        _roles_cache.pop(gid, None)


def _get_group_members_sync(group_id):
    """从 groups_users 表读取群成员信息 {user_id: {role, is_bot}}"""
    now = time.time()
    cached = _roles_cache.get(group_id)
    if cached and now - cached[0] < _ROLES_CACHE_TTL:
        return cached[1]
    _prune_roles_cache(now)
    if not _shared._bot_manager:
        return {}
    members: dict[str, dict] = {}
    for inst in _shared._bot_manager._bots.values():
        try:
            rows = inst.log_service.query_data('SELECT users FROM groups_users WHERE group_id = ?', (group_id,))
            if rows and rows[0].get('users'):
                users = json.loads(rows[0]['users'])
                for u in users:
                    uid = u.get('userid', '')
                    if not uid:
                        continue
                    info = {}
                    role = u.get('member_role', '')
                    if role:
                        info['role'] = str(role)
                    if u.get('is_bot'):
                        info['is_bot'] = True
                    if info:
                        members[uid] = info
                break
        except Exception as e:
            log.debug(f'获取群成员角色失败: {e}')
    _roles_cache[group_id] = (now, members)
    return members


async def handle_get_group_roles(request: web.Request):
    """获取群成员角色与 bot 标记"""
    body = await request.json()
    group_id = body.get('group_id', '')
    if not group_id:
        return web.json_response({'success': False, 'message': '缺少 group_id'}, status=400)
    loop = asyncio.get_running_loop()
    members = await loop.run_in_executor(None, _get_group_members_sync, group_id)
    return web.json_response({'success': True, 'data': members})
