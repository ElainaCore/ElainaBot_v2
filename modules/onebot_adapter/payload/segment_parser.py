"""消息段解析 — Strategy 模式

将 OneBot 11 消息段 (text/at/image/record/video/file/markdown/reply/keyboard) 解析为 ParsedMessage。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from modules.onebot_adapter.payload.image_decoder import ImageDecoder


@dataclass
class ParsedMessage:
    """解析后的消息结构体，承载所有消息段类型的数据"""

    text_content: str = ''  # 合并后的纯文本
    image_data: bytes | str | None = None  # 图片 bytes 或 URL
    voice_data: bytes | str | None = None  # 语音 bytes 或 URL
    video_data: bytes | str | None = None  # 视频 bytes 或 URL
    file_data: bytes | str | None = None  # 文件 bytes 或 URL
    file_name: str | None = None  # 文件名
    buttons: list | None = None  # 键盘按钮 [{label, ...}, ...]
    message_reference: dict | None = None  # 消息引用 (reply)
    msg_type: str = 'text'  # text | markdown | record | video | file
    markdown_content: str = ''  # markdown 源码 (msg_type=markdown 时)
    error: str | None = None

    @property
    def has_media(self) -> bool:
        return bool(self.image_data or self.voice_data or self.video_data or self.file_data)

    @property
    def media_type(self) -> int | None:
        """返回 sender.py 的 file_type: 1=image, 2=video, 3=voice, 4=file"""
        if self.image_data:
            return 1
        if self.video_data:
            return 2
        if self.voice_data:
            return 3
        if self.file_data:
            return 4
        return None

    @property
    def media_data(self) -> bytes | str | None:
        """返回第一个非空媒体数据"""
        return self.image_data or self.video_data or self.voice_data or self.file_data


class SegmentParser:
    """OneBot 消息段解析器

    每种 segment type 对应一个解析策略, 将 JSON segment 转换为 ParsedMessage。
    """

    # ==================== 各类型解析策略 ====================

    @classmethod
    def _parse_text(cls, seg_data: dict, pm: ParsedMessage) -> None:
        pm.text_content += seg_data.get('text', '')
        pm.msg_type = 'text'

    @classmethod
    def _parse_at(cls, seg_data: dict, pm: ParsedMessage) -> None:
        pm.text_content += f'@{seg_data.get("qq", "")}'

    @classmethod
    def _parse_image(cls, seg_data: dict, pm: ParsedMessage) -> None:
        if pm.image_data is not None:
            return
        file = seg_data.get('file') or seg_data.get('url')
        if not file:
            return
        if isinstance(file, str) and file.startswith('http'):
            pm.image_data = file  # URL, 后续由 upload_media_via_url 处理
        else:
            pm.image_data = ImageDecoder.decode(file)

    @classmethod
    def _parse_record(cls, seg_data: dict, pm: ParsedMessage) -> None:
        """语音消息段"""
        pm.msg_type = 'record'
        file = seg_data.get('file') or seg_data.get('url')
        if not file:
            return
        if isinstance(file, str) and file.startswith('http'):
            pm.voice_data = file
        else:
            pm.voice_data = ImageDecoder.decode(file)

    @classmethod
    def _parse_video(cls, seg_data: dict, pm: ParsedMessage) -> None:
        """视频消息段"""
        pm.msg_type = 'video'
        file = seg_data.get('file') or seg_data.get('url')
        if not file:
            return
        if isinstance(file, str) and file.startswith('http'):
            pm.video_data = file
        else:
            pm.video_data = ImageDecoder.decode(file)

    @classmethod
    def _parse_file(cls, seg_data: dict, pm: ParsedMessage) -> None:
        """文件消息段"""
        pm.msg_type = 'file'
        file = seg_data.get('file') or seg_data.get('url')
        if not file:
            return
        if isinstance(file, str) and file.startswith('http'):
            pm.file_data = file
        else:
            pm.file_data = ImageDecoder.decode(file)
        pm.file_name = seg_data.get('name') or seg_data.get('file_name')

    @classmethod
    def _parse_markdown(cls, seg_data: dict, pm: ParsedMessage) -> None:
        """Markdown 消息段"""
        pm.msg_type = 'markdown'
        pm.markdown_content = seg_data.get('content', '') or seg_data.get('data', '')

    @classmethod
    def _parse_reply(cls, seg_data: dict, pm: ParsedMessage) -> None:
        """回复 (引用) 消息段"""
        mid = seg_data.get('message_id') or seg_data.get('id')
        if mid:
            pm.message_reference = {
                'message_id': str(mid),
                'ignore_get_message_error': True,
            }

    @classmethod
    def _parse_keyboard(cls, seg_data: dict, pm: ParsedMessage) -> None:
        """键盘 / 按钮消息段

        OneBot 扩展格式:
          {type: 'keyboard', data: {rows: [{buttons: [{label, ...}, ...]}, ...]}}
        或简化格式:
          {type: 'keyboard', data: {buttons: [{label, ...}, ...]}}
        """
        rows = seg_data.get('rows')
        if rows:
            pm.buttons = rows
            return
        btns = seg_data.get('buttons')
        if btns:
            # 包装为单行
            pm.buttons = [{'buttons': btns}]

    @classmethod
    def _parse_face(cls, seg_data: dict, pm: ParsedMessage) -> None:
        """QQ 表情 (转义为 [CQ:face,id=xxx])"""
        fid = seg_data.get('id', '')
        pm.text_content += f'[CQ:face,id={fid}]'

    # ==================== 聚合解析 ====================

    _SEGMENT_HANDLERS = {
        'text': _parse_text.__func__,
        'at': _parse_at.__func__,
        'image': _parse_image.__func__,
        'record': _parse_record.__func__,
        'video': _parse_video.__func__,
        'file': _parse_file.__func__,
        'markdown': _parse_markdown.__func__,
        'reply': _parse_reply.__func__,
        'keyboard': _parse_keyboard.__func__,
        'button': _parse_keyboard.__func__,  # 别名
        'face': _parse_face.__func__,
    }

    @classmethod
    def parse(
        cls,
        message: str | list[dict[str, Any]] | Any,
    ) -> ParsedMessage:
        """解析 OneBot message 字段, 返回 ParsedMessage"""
        pm = ParsedMessage()

        if isinstance(message, str):
            pm.text_content = message
            return pm

        if not isinstance(message, list):
            message = [message]

        # 单段消息: 解包后按 segment 处理
        if len(message) == 1:
            seg = message[0]
            if isinstance(seg, dict):
                cls._handle_single_segment(seg, pm)
                return pm

        # 多段消息: 逐段解析
        for seg in message:
            if not isinstance(seg, dict):
                continue
            seg_type = seg.get('type', '')
            seg_data = seg.get('data', {})
            handler = cls._SEGMENT_HANDLERS.get(seg_type)
            if handler:
                handler(cls, seg_data, pm)

        return pm

    @classmethod
    def _handle_single_segment(cls, seg: dict, pm: ParsedMessage) -> None:
        """处理单段消息 (可能为 markdown/record 等顶层 structure)"""
        seg_type = seg.get('type', '')
        seg_data = seg.get('data', seg)
        # 回退: 逐段处理
        handler = cls._SEGMENT_HANDLERS.get(seg_type)
        if handler:
            handler(cls, seg_data, pm)
            return
        pm.error = f'未支持的segment类型:{seg_type}'
