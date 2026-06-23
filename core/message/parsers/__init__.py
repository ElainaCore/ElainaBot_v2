#!/usr/bin/env python
"""事件解析器 — 重新导出所有解析器类及工具函数"""

from core.message.parsers.base import (
    MessageParser,
    MessageUtils,
    apply_message_scene,
    extract_msg_idx,
    extract_image_from_attachments,
    parse_message_generic,
    sanitize_content,
)
from core.message.parsers.channel import ChannelDirectMessageParser, ChannelMessageParser
from core.message.parsers.direct import DirectMessageParser
from core.message.parsers.group import GroupMessageParser
from core.message.parsers.identity import IdentityHelper
from core.message.parsers.interaction import InteractionParser
from core.message.parsers.lifecycle import (
    FriendAddParser,
    FriendDelParser,
    GroupAddRobotParser,
    GroupDelRobotParser,
    GroupMemberAddParser,
    GroupMemberRemoveParser,
    GroupMsgReceiveParser,
    GroupMsgRejectParser,
    LifecycleParser,
)

__all__ = [
    # base
    'MessageParser',
    'MessageUtils',
    'apply_message_scene',
    'extract_msg_idx',
    'extract_image_from_attachments',
    'parse_message_generic',
    'sanitize_content',
    # group
    'GroupMessageParser',
    # direct / channel
    'DirectMessageParser',
    'ChannelMessageParser',
    'ChannelDirectMessageParser',
    # interaction
    'InteractionParser',
    # lifecycle
    'LifecycleParser',
    'GroupAddRobotParser',
    'GroupDelRobotParser',
    'GroupMemberAddParser',
    'GroupMemberRemoveParser',
    'GroupMsgRejectParser',
    'GroupMsgReceiveParser',
    'FriendAddParser',
    'FriendDelParser',
    # identity
    'IdentityHelper',
]
