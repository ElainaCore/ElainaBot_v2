"""Elaina Event → OneBot 11 事件格式转换

只适配用户实际能发送的消息类型: 文本、图片、语音、视频。
"""

from __future__ import annotations

import html
import re
import time

_URL_IN_ANGLE = re.compile(r'<(https?://[^>]+)>')


def _build_segments(event) -> list[dict]:
    """从 Event 的 content + attachments 构建 OneBot 消息段

    用户只能发送: 文本、图片、语音、视频
    """
    segments = []

    # 1. 从 attachments 提取媒体 (图片/语音/视频)
    attachments = getattr(event, 'attachments', None) or []
    for att in attachments:
        if not isinstance(att, dict):
            continue
        ct = att.get('content_type', '')
        url = html.unescape(att.get('url', '') or '')
        if not url:
            continue
        if ct.startswith('image/'):
            segments.append({'type': 'image', 'data': {'file': url, 'url': url}})
        elif ct.startswith('audio/') or ct.startswith('voice/'):
            segments.append({'type': 'record', 'data': {'file': url, 'url': url}})
        elif ct.startswith('video/'):
            segments.append({'type': 'video', 'data': {'file': url, 'url': url}})

    # 2. 文本内容 (去掉被合并进 content 的 <url> 图片标记, 避免重复)
    text = getattr(event, 'content', '') or ''
    if text:
        text = _URL_IN_ANGLE.sub('', text).strip()
    if text:
        segments.insert(0, {'type': 'text', 'data': {'text': text}})

    # 3. 兜底: 完全无内容时补空文本
    if not segments:
        segments.append({'type': 'text', 'data': {'text': ''}})
    if mentions := getattr(event, 'mentions', None):
        for user in mentions:
            scope = user.get('scope') or 'single'
            uid = 0 if scope == 'all' else user.get('id') or user.get('member_openid')
            data = {'qq': uid} | user
            at_seg = {'type': 'at', 'data': data}
            segments.append(at_seg)
    return segments


def _segments_to_raw(segments: list[dict]) -> str:
    """OneBot 消息段 → raw_message CQ 码字符串"""
    parts = []
    for seg in segments:
        t = seg.get('type', '')
        d = seg.get('data', {})
        if t == 'text':
            parts.append(d.get('text', ''))
        elif t == 'image':
            parts.append(f'[CQ:image,file={d.get("file", "")}]')
        elif t == 'record':
            parts.append(f'[CQ:record,file={d.get("file", "")}]')
        elif t == 'video':
            parts.append(f'[CQ:video,file={d.get("file", "")}]')
        else:
            kv = ','.join(f'{k}={v}' for k, v in d.items())
            parts.append(f'[CQ:{t},{kv}]' if kv else f'[CQ:{t}]')
    return ''.join(parts)


async def convert_message_event(event, id_mapper, self_qq: int) -> dict | None:
    """将 Elaina Event 转换为 OneBot 11 message 事件"""
    et = event.event_type
    if et not in (
        'GROUP_AT_MESSAGE_CREATE',
        'GROUP_MESSAGE_CREATE',
        'C2C_MESSAGE_CREATE',
        'AT_MESSAGE_CREATE',
        'DIRECT_MESSAGE_CREATE',
        'MESSAGE_CREATE',
        'INTERACTION_CREATE',
    ):
        return None

    user_id = event.user_id or ''
    group_id = event.group_id or ''
    if not user_id:
        return None

    qq_user = await id_mapper.to_qq(user_id, 'user')
    is_group = event.is_group or bool(group_id and et != 'C2C_MESSAGE_CREATE')
    qq_group = await id_mapper.to_qq(group_id, 'group') if (is_group and group_id) else 0

    segments = _build_segments(event)

    # 按钮交互: 在消息段前插入 [CQ:button] 标识
    if et == 'INTERACTION_CREATE' and getattr(event, 'interaction_data', None):
        resolved = (event.interaction_data.get('data') or {}).get('resolved') or {}
        btn_data = resolved.get('button_data', '')
        btn_id = resolved.get('button_id', '')
        segments.insert(
            0,
            {
                'type': 'button',
                'data': {
                    'id': btn_id,
                    'data': btn_data,
                },
            },
        )

    raw_message = _segments_to_raw(segments)

    now = int(time.time())
    msg_id = hash(event.message_id or f'{now}{user_id}') & 0x7FFFFFFF

    ob_event = {
        'time': now,
        'self_id': self_qq,
        'post_type': 'message',
        'message_type': 'group' if is_group else 'private',
        'sub_type': 'normal',
        'message_id': msg_id,
        'raw_msg_id': event.message_id,
        'user_id': qq_user,
        'message': segments,
        'raw_message': raw_message,
        'font': 0,
        'sender': {
            'user_id': qq_user,
            'nickname': getattr(event, 'username', '') or str(qq_user),
            'sex': 'unknown',
            'age': 0,
        },
        'real_user_id': event.user_id,
        'real_group_id': event.group_id,
    }

    if is_group:
        ob_event['group_id'] = qq_group
        ob_event['sender']['card'] = ''
        ob_event['sender']['role'] = event.member_role
        ob_event['anonymous'] = None
    else:
        ob_event['sub_type'] = 'friend'

    return ob_event


# (notice_type, sub_type, group_mode)
# group_mode: 'robot' = 机器人进退群 (user_id=self), 'member' = 群成员进退群 (user_id=成员), False = 好友事件
_LIFECYCLE_MAP = {
    'GROUP_ADD_ROBOT': ('group_increase', 'invite', 'robot'),
    'GROUP_DEL_ROBOT': ('group_decrease', 'kick_me', 'robot'),
    'GROUP_MEMBER_ADD': ('group_increase', 'approve', 'member'),
    'GROUP_MEMBER_REMOVE': ('group_decrease', 'leave', 'member'),
    'FRIEND_ADD': ('friend_add', '', False),
    'FRIEND_DEL': ('friend_recall', '', False),
}


async def convert_lifecycle_event(event, id_mapper, self_qq: int) -> dict | None:
    """将 Elaina 生命周期事件转换为 OneBot 11 notice 事件"""
    entry = _LIFECYCLE_MAP.get(event.event_type)
    if not entry:
        return None

    notice_type, sub_type, group_mode = entry
    qq_user = await id_mapper.to_qq(event.user_id, 'user') if event.user_id else 0
    result = {
        'time': int(time.time()),
        'self_id': self_qq,
        'post_type': 'notice',
        'notice_type': notice_type,
        'real_user_id': event.user_id,
        'real_group_id': event.group_id,
        'user_id': self_qq if group_mode == 'robot' else qq_user,
    }
    if sub_type:
        result['sub_type'] = sub_type
    if group_mode:
        group_id = 0
        if event.group_id:
            group_id = await id_mapper.to_qq(event.group_id, 'group')
        result['group_id'] = group_id
        result['operator_id'] = qq_user
    return result
