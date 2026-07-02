#!/usr/bin/env python
"""事件解析器 — 本文件已废弃，所有内容迁移至 core/message/parsers/ 目录。

此文件仅作向后兼容用途，新代码请直接从 parsers 包导入。
"""

from core.message.parsers.base import (
    MessageParser,
    MessageUtils,
    apply_message_scene,
    extract_image_from_attachments,
    extract_msg_idx,
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
    'MessageParser',
    'MessageUtils',
    'apply_message_scene',
    'extract_msg_idx',
    'extract_image_from_attachments',
    'parse_message_generic',
    'sanitize_content',
    'GroupMessageParser',
    'DirectMessageParser',
    'ChannelMessageParser',
    'ChannelDirectMessageParser',
    'InteractionParser',
    'LifecycleParser',
    'GroupAddRobotParser',
    'GroupDelRobotParser',
    'GroupMemberAddParser',
    'GroupMemberRemoveParser',
    'GroupMsgRejectParser',
    'GroupMsgReceiveParser',
    'FriendAddParser',
    'FriendDelParser',
    'IdentityHelper',
]
