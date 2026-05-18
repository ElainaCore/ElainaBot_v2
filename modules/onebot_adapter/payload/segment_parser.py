"""消息段解析 — Strategy 模式

将 OneBot 11 消息段 (text/at/image) 解析为 (text_content, image_bytes)。
"""

from __future__ import annotations

from typing import Any

from modules.onebot_adapter.payload.image_decoder import ImageDecoder


class SegmentParser:
    """OneBot 消息段解析器

    每种 segment type 对应一个解析策略, 将 JSON segment 转换为文本片段或图片字节。
    返回 (text_content: str, image_bytes: bytes | None)
    """

    @classmethod
    def parse_markdown(cls, message: dict) -> str:
        if data := message.get('data'):
            message = data
        return message, None  # TODO 进行解析

    @classmethod
    def parse(
        cls,
        message: str | list[dict[str, Any]] | Any,
    ) -> tuple[str | bytes, bytes | None]:
        """解析 OneBot message 字段, 提取文本和图片"""
        if isinstance(message, str):
            return message, None
        if isinstance(message, list) and len(message) > 1:
            return cls.handle_normal_msg(message)
        if isinstance(message, list) and len(message) == 1:
            message = message[0]
        msg_type = message.get('type')
        if msg_type == 'markdown':
            msg_data = message.get('data', {})
            return cls.parse_markdown(msg_data)
        return cls.handle_normal_msg([message])

    @classmethod
    def handle_normal_msg(cls, message: list[dict[str, Any]]) -> tuple[str | bytes, bytes | None]:
        "兼容传统消息"
        texts: list[str] = []
        image_bytes: bytes | None = None

        for seg in message:
            if not isinstance(seg, dict):
                continue
            seg_type = seg.get('type', '')
            seg_data = seg.get('data', {})
            if seg_type == 'text':
                texts.append(seg_data.get('text', ''))
                continue
            if seg_type == 'at':
                texts.append(f'@{seg_data.get("qq", "")}')
                continue
            if seg_type == 'image' and not image_bytes:
                if file := seg_data.get('file', ''):
                    image_bytes = ImageDecoder.decode(file)
                continue
        return ''.join(texts), image_bytes
