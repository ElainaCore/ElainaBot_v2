#!/usr/bin/env python
"""频道消息解析器"""

from core.message.parsers.base import MessageParser, MessageUtils


class ChannelMessageParser(MessageParser):
    """频道消息解析器 — 去除 @bot 前缀"""

    def parse(self, event, d):
        super().parse(event, d)
        mentions = d.get('mentions')
        if isinstance(mentions, list) and mentions:
            first_mention = mentions[0] if isinstance(mentions[0], dict) else {}
            bot_id = first_mention.get('id')
            if bot_id and event.raw_content:
                for prefix in [f'<@!{bot_id}>', f'<@{bot_id}>']:
                    if event.raw_content.startswith(prefix):
                        cleaned = event.raw_content[len(prefix):].lstrip()
                        event.content = MessageUtils.sanitize_content(cleaned)
                        break
        event.group_id = d.get('channel_id', '')


class ChannelDirectMessageParser(MessageParser):
    """频道私信解析器"""

    def parse(self, event, d):
        super().parse(event, d)
        event.guild_id = d.get('guild_id', '')
