#!/usr/bin/env python
"""解析器基类 — MessageUtils 静态工具类 + MessageParser 基类"""

import html
import re
from urllib.parse import unquote


class MessageUtils:
    """消息解析静态工具类 — 不可实例化，仅容纳无状态纯函数和正则模式"""

    _FACE_PATTERN = re.compile(r'<faceType=\d+,faceId="([^"]+)",ext="[^"]+">')
    _MSG_IDX_PATTERN = re.compile(r'(?:^|[?&])msg_idx=([^&]+)')
    _AT_PATTERN = re.compile(r'<@!?[A-Za-z0-9]+>')

    @staticmethod
    def sanitize_content(content, *, keep_at=False):
        """内容清洗: face 标签→[face id=X], @圈人信息剔除(keep_at=True 保留), 去首尾空白"""
        if not content:
            return ''
        text = str(content)
        if '<faceType' in text:
            text = MessageUtils._FACE_PATTERN.sub(r'[face id=\1]', text)
        if not keep_at and '<@' in text:
            text = MessageUtils._AT_PATTERN.sub('', text)
        return text.strip()

    @staticmethod
    def extract_image_from_attachments(attachments):
        """从附件列表提取第一张图片 URL"""
        if not isinstance(attachments, list):
            return None
        for att in attachments:
            if not isinstance(att, dict):
                continue
            if att.get('content_type', '').startswith('image/'):
                return html.unescape(att.get('url', '') or None)
        return None

    @staticmethod
    def extract_msg_idx(scene):
        """从 message_scene.ext 中提取可用于 message_reference 的 REFIDX"""
        if not isinstance(scene, dict):
            return ''
        ext = scene.get('ext', [])
        if isinstance(ext, str):
            ext = [ext]
        if not isinstance(ext, list):
            return ''
        for item in ext:
            if not isinstance(item, str):
                continue
            m = MessageUtils._MSG_IDX_PATTERN.search(item)
            if m:
                return unquote(m.group(1))
        return ''


# ==================== 向后兼容的模块级包装函数 ====================


def sanitize_content(content):
    """内容清洗"""
    return MessageUtils.sanitize_content(content)


def extract_image_from_attachments(attachments):
    """从附件列表提取第一张图片 URL"""
    return MessageUtils.extract_image_from_attachments(attachments)


def extract_msg_idx(scene):
    """从 message_scene.ext 中提取 REFIDX"""
    return MessageUtils.extract_msg_idx(scene)


def apply_message_scene(event, d):
    """填充 message_scene / message_reference_id / scene_source"""
    scene = d.get('message_scene', {})
    event.message_scene = scene if isinstance(scene, dict) else {}
    event.message_reference_id = MessageUtils.extract_msg_idx(scene)
    event.scene_source = scene.get('source', '') if isinstance(scene, dict) else ''


def _parse_common_fields(event, d):
    """提取公共字段 (id/author/content/group/image)"""
    event.message_id = d.get('id', '')
    event.raw_content = d.get('content', '')
    event.content = MessageUtils.sanitize_content(event.raw_content)
    event.timestamp = d.get('timestamp', '')
    event.message_type = d.get('message_type')
    event.msg_elements = d.get('msg_elements', [])
    event.attachments = d.get('attachments', [])
    event.image_url = MessageUtils.extract_image_from_attachments(event.attachments)

    author = d.get('author', {})
    event.user_id = author.get('member_openid') or author.get('id', '')
    event.raw_user_id = event.user_id
    event.username = author.get('username', '')
    event.member_openid = author.get('member_openid', '')
    event.member_role = author.get('member_role', '')
    event.union_openid = author.get('union_openid', '')
    event.is_bot = author.get('bot', False)

    event.group_id = d.get('group_openid') or d.get('group_id', '')
    event.group_openid = d.get('group_openid', '')
    event.guild_id = d.get('guild_id', '')
    event.channel_id = d.get('channel_id', '')

    if event.image_url and event.content:
        event.content = f'{event.content}<{event.image_url}>'
    elif event.image_url:
        event.content = f'<{event.image_url}>'


def parse_message_generic(event, d):
    """通用消息解析 — 提取公共字段并填充 scene"""
    _parse_common_fields(event, d)
    apply_message_scene(event, d)


# ==================== 解析器基类 ====================


class MessageParser:
    """消息解析器基类"""

    _FACE_PATTERN = MessageUtils._FACE_PATTERN
    _MSG_IDX_PATTERN = MessageUtils._MSG_IDX_PATTERN
    _AT_PATTERN = MessageUtils._AT_PATTERN

    sanitize_content = staticmethod(sanitize_content)
    extract_image_from_attachments = staticmethod(extract_image_from_attachments)
    parse_generic = staticmethod(_parse_common_fields)
    apply_message_scene = staticmethod(apply_message_scene)

    def parse(self, event, d):
        """通用消息解析 — 子类可继续处理 mentions / 交互回调等"""
        parse_message_generic(event, d)
