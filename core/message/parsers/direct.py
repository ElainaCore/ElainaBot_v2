#!/usr/bin/env python
"""C2C 私聊消息解析器"""

from core.message.parsers.base import MessageParser


class DirectMessageParser(MessageParser):
    """C2C 私聊消息解析器"""

    def parse(self, event, d):
        super().parse(event, d)
        event.is_group = False
        event.is_direct = True
