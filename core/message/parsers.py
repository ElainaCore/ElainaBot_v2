#!/usr/bin/env python
"""事件解析器 — 每种事件类型的专属解析函数"""

import html
import json
import re

# 内容清洗: QQ 表情标签
_FACE_PATTERN = re.compile(r'<faceType=\d+,faceId="[^"]+",ext="[^"]+">')


# ==================== 内容处理辅助 ====================


def sanitize_content(content):
    """内容清洗: 去除 face 标签, 去除首尾空白 (/ 前缀由 dispatch 层处理)"""
    if not content:
        return ''
    text = str(content).strip()
    if '<faceType' in text:
        text = _FACE_PATTERN.sub('', text).strip()
    return text


def extract_image_from_attachments(attachments):
    """从附件列表提取第一张图片 URL"""
    if not isinstance(attachments, list):
        return None
    for att in attachments:
        if not isinstance(att, dict):
            continue
        if att.get('content_type', '').startswith('image/'):
            return html.unescape(att.get('url', '')) or None
    return None


def swap_ids(uid, union_id, should_swap):
    """union_openid 与 openid 交换: 返回 (user_id, union_openid, raw_user_id)"""
    if should_swap and union_id:
        return union_id, uid, uid
    return uid, union_id or uid, uid


# ==================== 通用消息解析 ====================


def parse_message_generic(event, d):
    """通用消息解析 (兜底)"""
    event.message_id = d.get('id', '')
    event.raw_content = d.get('content', '')
    event.content = sanitize_content(event.raw_content)
    event.timestamp = d.get('timestamp', '')
    event.message_type = d.get('message_type')
    event.msg_elements = d.get('msg_elements', [])
    event.attachments = d.get('attachments', [])
    event.image_url = extract_image_from_attachments(event.attachments)

    author = d.get('author', {})
    event.user_id = author.get('member_openid') or author.get('id', '')
    event.raw_user_id = event.user_id
    event.username = author.get('username', '')
    event.member_openid = author.get('member_openid', '')
    event.union_openid = author.get('union_openid', '')
    event.is_bot = author.get('bot', False)

    event.group_id = d.get('group_openid') or d.get('group_id', '')
    event.group_openid = d.get('group_openid', '')
    event.guild_id = d.get('guild_id', '')
    event.channel_id = d.get('channel_id', '')

    scene = d.get('message_scene', {})
    event.scene_source = scene.get('source', '') if isinstance(scene, dict) else ''

    if event.image_url and event.content:
        event.content = f'{event.content}<{event.image_url}>'
    elif event.image_url:
        event.content = f'<{event.image_url}>'


# ==================== 专属解析器 ====================


def parse_group_message(event, d):
    """群聊消息解析"""
    parse_message_generic(event, d)
    mentions = d.get('mentions')
    if isinstance(mentions, list):
        event.mentions = mentions
        for mention in mentions:
            if isinstance(mention, dict) is False:
                continue
            is_you = mention.get('is_you')
            if is_you is True:
                event.is_at_self = True
            if mention.get('bot') is True and not is_you:
                event.is_at_other_bot = True
            if mention.get('scope') == 'all':
                event.is_at_all = True


def parse_direct_message(event, d):
    """C2C 私聊消息解析"""
    parse_message_generic(event, d)
    event.is_group = False
    event.is_direct = True


def parse_channel_message(event, d):
    """频道消息解析: 去除 @bot 前缀"""
    parse_message_generic(event, d)
    mentions = d.get('mentions')
    if isinstance(mentions, list) and mentions:
        bot_id = mentions[0].get('id')
        if bot_id and event.raw_content:
            for prefix in [f'<@!{bot_id}>', f'<@{bot_id}>']:
                if event.raw_content.startswith(prefix):
                    cleaned = event.raw_content[len(prefix) :].lstrip()
                    event.content = sanitize_content(cleaned)
                    break
    event.group_id = d.get('channel_id', '')


def parse_channel_direct_message(event, d):
    """频道私信解析"""
    parse_message_generic(event, d)
    event.guild_id = d.get('guild_id', '')


def parse_interaction(event, d):
    """交互事件解析 (按钮回调等)"""
    event.interaction_data = d
    event.message_id = d.get('id', '')

    if d.get('type') == 13:
        event.content = ''
        return
    event.timestamp = d.get('timestamp', '')

    chat_type = d.get('chat_type')
    scene = d.get('scene')
    event.chat_type_code = chat_type
    event.scene = scene

    if chat_type == 1 or scene == 'group':
        event.group_id = d.get('group_openid') or d.get('group_id', '')
        event.user_id = d.get('group_member_openid') or d.get('author', {}).get('id', '')
        event.is_group = True
        event.is_direct = False
    elif chat_type == 2 or scene == 'c2c':
        event.group_id = ''
        event.user_id = d.get('user_openid') or d.get('author', {}).get('id', '')
        event.is_group = False
        event.is_direct = True
    else:
        event.group_id = d.get('group_openid') or d.get('group_id', '')
        event.user_id = d.get('group_member_openid') or d.get('user_openid') or d.get('author', {}).get('id', '')
        event.is_group = bool(event.group_id)
        event.is_direct = not event.is_group

    event.raw_user_id = event.user_id
    event.union_openid = None
    event.guild_id = d.get('guild_id', '')
    event.channel_id = d.get('channel_id', '')

    resolved = d.get('data', {}).get('resolved', {})
    button_data = resolved.get('button_data', '') or resolved.get('button_id', '')
    event.content = sanitize_content(button_data)


def _parse_lifecycle_base(event, d, uid_key='openid'):
    """生命周期事件公共字段"""
    event.user_id = event.raw_user_id = d.get(uid_key, '')
    event.group_id = d.get('group_openid', '')
    event.timestamp = d.get('timestamp', '')
    event.message_id = d.get('id', '') or event.event_id


def parse_group_add_robot(event, d):
    """入群事件解析"""
    _parse_lifecycle_base(event, d, 'op_member_openid')
    event.content = f'机器人被邀请加入群聊 {event.group_id}'


def parse_group_del_robot(event, d):
    """退群事件解析"""
    _parse_lifecycle_base(event, d, 'op_member_openid')
    event.content = f'机器人被移出群聊 {event.group_id}'


def _extract_sharer_id(scene_param):
    """从 scene_param 提取分享者 ID"""
    if not scene_param:
        return None
    try:
        sp = json.loads(scene_param) if isinstance(scene_param, str) else scene_param
        return sp.get('callbackData', '') if isinstance(sp, dict) else str(scene_param)
    except (json.JSONDecodeError, AttributeError):
        return str(scene_param)


def parse_friend_add(event, d):
    """好友添加事件解析"""
    _parse_lifecycle_base(event, d)
    event.group_id = ''  # 好友事件无 group
    try:
        event.scene = int(d.get('scene') or 0)
    except (ValueError, TypeError):
        event.scene = 0
    event.scene_param = d.get('scene_param')
    event.sharer_id = _extract_sharer_id(event.scene_param)
    event.content = f'用户 {event.user_id} 添加机器人为好友'
    if event.sharer_id:
        event.content += f' (通过 {event.sharer_id} 的分享链接)'


def parse_friend_del(event, d):
    """好友删除事件解析"""
    _parse_lifecycle_base(event, d)
    event.group_id = ''  # 好友事件无 group
    event.content = f'用户 {event.user_id} 删除机器人好友'
