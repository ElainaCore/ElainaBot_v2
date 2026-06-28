"""载荷模块 — Strategy 模式消息段解析/发送

重导出:
  - ImageDecoder: 图片解码策略
  - SegmentParser: 消息段解析
  - PayloadConverter: 载荷格式转换
  - MessageSenderService: 统一消息发送
"""

from modules.onebot_adapter.payload.image_decoder import ImageDecoder
from modules.onebot_adapter.payload.message_sender_service import MessageSenderService
from modules.onebot_adapter.payload.payload_converter import PayloadConverter
from modules.onebot_adapter.payload.segment_parser import ParsedMessage, SegmentParser

__all__ = [
    'ImageDecoder',
    'MessageSenderService',
    'ParsedMessage',
    'PayloadConverter',
    'SegmentParser',
]
