#!/usr/bin/env python
"""生命周期事件解析器 — 入退群、入退好友等"""

import json

from core.message.parsers.base import MessageParser


class LifecycleParser(MessageParser):
    """生命周期事件解析器基类"""

    def _parse_base(self, event, d, uid_key='openid'):
        """生命周期事件公共字段"""
        event.user_id = event.raw_user_id = d.get(uid_key, '')
        event.group_id = d.get('group_openid', '')
        event.timestamp = d.get('timestamp', '')
        event.message_id = d.get('id', '') or event.event_id


class GroupAddRobotParser(LifecycleParser):
    """入群事件解析器"""

    def parse(self, event, d):
        self._parse_base(event, d, 'op_member_openid')
        event.content = f'机器人被邀请加入群聊 {event.group_id}'


class GroupDelRobotParser(LifecycleParser):
    """退群事件解析器"""

    def parse(self, event, d):
        self._parse_base(event, d, 'op_member_openid')
        event.content = f'机器人被移出群聊 {event.group_id}'


class GroupMemberAddParser(LifecycleParser):
    """用户入群事件解析器"""

    def parse(self, event, d):
        self._parse_base(event, d, 'member_openid')
        event.member_openid = event.user_id
        event.content = f'用户 {event.user_id} 加入群聊 {event.group_id}'


class GroupMemberRemoveParser(LifecycleParser):
    """用户退群事件解析器"""

    def parse(self, event, d):
        self._parse_base(event, d, 'member_openid')
        event.member_openid = event.user_id
        event.content = f'用户 {event.user_id} 退出群聊 {event.group_id}'


class GroupMsgRejectParser(LifecycleParser):
    """群消息拒绝事件解析器"""

    def parse(self, event, d):
        self._parse_base(event, d, 'op_member_openid')
        event.content = f'群 {event.group_id} 拒绝接收消息'


class GroupMsgReceiveParser(LifecycleParser):
    """群消息恢复接收事件解析器"""

    def parse(self, event, d):
        self._parse_base(event, d, 'op_member_openid')
        event.content = f'群 {event.group_id} 恢复接收消息'


class FriendAddParser(LifecycleParser):
    """好友添加事件解析器"""

    @staticmethod
    def _extract_sharer_id(scene_param):
        """从 scene_param 提取分享者 ID"""
        if not scene_param:
            return None
        try:
            sp = json.loads(scene_param) if isinstance(scene_param, str) else scene_param
            return sp.get('callbackData', '') if isinstance(sp, dict) else str(scene_param)
        except (json.JSONDecodeError, AttributeError):
            return str(scene_param)

    def parse(self, event, d):
        self._parse_base(event, d)
        event.group_id = ''
        try:
            event.scene = int(d.get('scene') or 0)
        except (ValueError, TypeError):
            event.scene = 0
        event.scene_param = d.get('scene_param')
        event.sharer_id = self._extract_sharer_id(event.scene_param)
        event.content = f'用户 {event.user_id} 添加机器人为好友'
        if event.sharer_id:
            event.content += f' (通过 {event.sharer_id} 的分享链接)'


class FriendDelParser(LifecycleParser):
    """好友删除事件解析器"""

    def parse(self, event, d):
        self._parse_base(event, d)
        event.group_id = ''
        event.content = f'用户 {event.user_id} 删除机器人好友'
