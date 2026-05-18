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

    @staticmethod
    def parse(message: str | list[dict] | Any) -> tuple[str, bytes | None]:
        """解析 OneBot message 字段, 提取文本和图片"""
        if isinstance(message, str):
            return message, None
        if not isinstance(message, list):
            return str(message), None

        texts: list[str] = []
        image_bytes: bytes | None = None

        for seg in message:
            if not isinstance(seg, dict):
                continue
            seg_type = seg.get('type', '')
            seg_data = seg.get('data', {})

            if seg_type == 'text':
                texts.append(seg_data.get('text', ''))
            elif seg_type == 'at':
                texts.append(f'@{seg_data.get("qq", "")}')
            elif seg_type == 'image' and image_bytes is None:
                image_bytes = ImageDecoder.decode(seg_data.get('file', ''))

        return ''.join(texts), image_bytes
