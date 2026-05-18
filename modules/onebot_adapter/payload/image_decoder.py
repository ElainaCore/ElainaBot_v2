"""图片解码 — Strategy 模式

支持 base64:// 和 data:image 两种格式的图片解码。
"""

from __future__ import annotations

import base64


class ImageDecoder:
    """图片解码策略: 支持 base64:// 和 data:image 两种格式"""

    @staticmethod
    def decode(file_str: str) -> bytes | None:
        """尝试解码图片字符串, 失败返回 None"""
        if not file_str:
            return None
        if file_str.startswith('base64://'):
            return ImageDecoder._decode_base64(file_str[9:])
        if file_str.startswith('data:image'):
            idx = file_str.find(',') + 1
            if idx:
                return ImageDecoder._decode_base64(file_str[idx:])
        return None

    @staticmethod
    def _decode_base64(data: str) -> bytes | None:
        try:
            return base64.b64decode(data)
        except Exception:
            return None
