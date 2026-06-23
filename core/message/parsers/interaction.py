#!/usr/bin/env python
"""交互事件解析器 (按钮回调等)"""

from core.message.parsers.base import MessageParser, MessageUtils


class InteractionParser(MessageParser):
    """交互事件解析器 (按钮回调等)"""

    def parse(self, event, d):
        event.interaction_data = d
        event.message_id = d.get('id', '')

        if d.get('type') == 13:
            event.content = ''
            return
        event.timestamp = d.get('timestamp', '')

        chat_type = d.get('chat_type')
        scene = d.get('scene')
        event.chat_type_code = chat_type
        event.scene = scene

        if chat_type == 1 or scene == 'group':
            event.group_id = d.get('group_openid') or d.get('group_id', '')
            event.user_id = d.get('group_member_openid') or d.get('author', {}).get('id', '')
            event.is_group = True
            event.is_direct = False
        elif chat_type == 2 or scene == 'c2c':
            event.group_id = ''
            event.user_id = d.get('user_openid') or d.get('author', {}).get('id', '')
            event.is_group = False
            event.is_direct = True
        else:
            event.group_id = d.get('group_openid') or d.get('group_id', '')
            event.user_id = d.get('group_member_openid') or d.get('user_openid') or d.get('author', {}).get('id', '')
            event.is_group = bool(event.group_id)
            event.is_direct = not event.group_id

        event.raw_user_id = event.user_id
        event.union_openid = None
        event.guild_id = d.get('guild_id', '')
        event.channel_id = d.get('channel_id', '')
        self.apply_message_scene(event, d)

        resolved = d.get('data', {}).get('resolved', {})
        button_data = resolved.get('button_data', '') or resolved.get('button_id', '')
        event.content = MessageUtils.sanitize_content(button_data)
