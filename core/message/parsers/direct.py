#!/usr/bin/env python
"""C2C 私聊消息解析器"""

from core.message.parsers.base import MessageParser


class DirectMessageParser(MessageParser):
    """C2C 私聊消息解析器"""

    def parse(self, event, d):
        super().parse(event, d)
        author = d.get('author')
        user_openid = author.get('user_openid') if isinstance(author, dict) else None
        if isinstance(user_openid, str) and user_openid:
            event.user_id = event.raw_user_id = user_openid
        event.user_openid = event.raw_user_id if isinstance(event.raw_user_id, str) else ''
        event.is_group = False
        event.is_direct = True
