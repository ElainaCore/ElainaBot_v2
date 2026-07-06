#!/usr/bin/env python
"""群聊消息解析器"""

from core.message import bot_openid
from core.message.parsers.base import MessageParser


class GroupMessageParser(MessageParser):
    """群聊消息解析器"""

    def handle_mentions(self, event, d: dict, is_full: bool):
        mentions = d.get('mentions')
        if not isinstance(mentions, list):
            return
        event.mentions = mentions
        for mention in mentions:
            if isinstance(mention, dict) is False:
                continue
            is_you = mention.get('is_you')
            if is_you is True:
                event.is_at_self = True
                event.bot_member_role = mention.get('member_role', '')
                if is_full:
                    mid = mention.get('id')
                    if mid and event.appid:
                        bot_openid.add(event.appid, mid)
            if mention.get('bot') is True and not is_you:
                event.is_at_other_bot = True
            if not mention.get('bot') and not is_you and mention.get('scope') != 'all':
                event.is_at_other_user = True
            if mention.get('scope') == 'all':
                event.is_at_all = True

    def parse(self, event, d: dict):
        super().parse(event, d)
        is_full = event.event_type == 'GROUP_MESSAGE_CREATE'
        self.handle_mentions(event, d, is_full)
        if is_full and '<@' in event.content and event.appid:
            only_self_at = event.is_at_self and not event.is_at_other_bot and not event.is_at_other_user and not event.is_at_all
            if only_self_at and not bot_openid.is_done(event.appid):
                bot_openid.learn(event.appid, event.content)
            event.content = bot_openid.strip_self_at(event.appid, event.content)
