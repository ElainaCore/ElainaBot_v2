#!/usr/bin/env python
"""事件数据容器 + 类型常量"""

import asyncio
import json
from functools import partial

from core.message.parsers.base import MessageParser
from core.message.parsers.channel import ChannelDirectMessageParser, ChannelMessageParser
from core.message.parsers.direct import DirectMessageParser
from core.message.parsers.group import GroupMessageParser
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
    SubscribeStatusParser,
)

# 交互回调 (op12 ACK) 默认状态码与默认等待插件超时 (秒)
_DEFAULT_CALLBACK_CODE = 0
_DEFAULT_ACK_TIMEOUT = 2.0

# ==================== 事件类型常量 ====================

# 群聊
GROUP_AT_MESSAGE_CREATE = 'GROUP_AT_MESSAGE_CREATE'
GROUP_MESSAGE_CREATE = 'GROUP_MESSAGE_CREATE'
C2C_MESSAGE_CREATE = 'C2C_MESSAGE_CREATE'

# 交互
INTERACTION_CREATE = 'INTERACTION_CREATE'

# 好友
FRIEND_ADD = 'FRIEND_ADD'
FRIEND_DEL = 'FRIEND_DEL'

# 群管理
GROUP_ADD_ROBOT = 'GROUP_ADD_ROBOT'
GROUP_DEL_ROBOT = 'GROUP_DEL_ROBOT'
GROUP_MSG_REJECT = 'GROUP_MSG_REJECT'
GROUP_MSG_RECEIVE = 'GROUP_MSG_RECEIVE'

# 群成员 (用户入群/退群)
GROUP_MEMBER_ADD = 'GROUP_MEMBER_ADD'
GROUP_MEMBER_REMOVE = 'GROUP_MEMBER_REMOVE'

# 订阅消息 (用户允许/拒绝订阅)
SUBSCRIBE_MESSAGE_STATUS = 'SUBSCRIBE_MESSAGE_STATUS'

# 表态
MESSAGE_REACTION_ADD = 'MESSAGE_REACTION_ADD'
MESSAGE_REACTION_REMOVE = 'MESSAGE_REACTION_REMOVE'

# 频道管理
GUILD_UPDATE = 'GUILD_UPDATE'

# 频道
AT_MESSAGE_CREATE = 'AT_MESSAGE_CREATE'
DIRECT_MESSAGE_CREATE = 'DIRECT_MESSAGE_CREATE'
MESSAGE_CREATE = 'MESSAGE_CREATE'
# 分类集合
MESSAGE_TYPES = frozenset(
    {
        GROUP_AT_MESSAGE_CREATE,
        GROUP_MESSAGE_CREATE,
        C2C_MESSAGE_CREATE,
        AT_MESSAGE_CREATE,
        DIRECT_MESSAGE_CREATE,
        MESSAGE_CREATE,
    }
)
GROUP_TYPES = frozenset({GROUP_AT_MESSAGE_CREATE, GROUP_MESSAGE_CREATE})
DIRECT_TYPES = frozenset({C2C_MESSAGE_CREATE, DIRECT_MESSAGE_CREATE})
CHANNEL_TYPES = frozenset({AT_MESSAGE_CREATE, DIRECT_MESSAGE_CREATE, MESSAGE_CREATE})
LIFECYCLE_TYPES = frozenset(
    {
        FRIEND_ADD,
        FRIEND_DEL,
        GROUP_ADD_ROBOT,
        GROUP_DEL_ROBOT,
        GROUP_MSG_REJECT,
        GROUP_MSG_RECEIVE,
        GROUP_MEMBER_ADD,
        GROUP_MEMBER_REMOVE,
        SUBSCRIBE_MESSAGE_STATUS,
    }
)
REACTION_TYPES = frozenset({MESSAGE_REACTION_ADD, MESSAGE_REACTION_REMOVE})
SILENT_TYPES = frozenset({MESSAGE_REACTION_ADD, MESSAGE_REACTION_REMOVE, GUILD_UPDATE})

# 需要 msg_id / event_id 回复的事件
_MSG_ID_TYPES = frozenset(
    {
        GROUP_AT_MESSAGE_CREATE,
        GROUP_MESSAGE_CREATE,
        C2C_MESSAGE_CREATE,
        AT_MESSAGE_CREATE,
        DIRECT_MESSAGE_CREATE,
    }
)
_EVENT_ID_TYPES = frozenset({INTERACTION_CREATE, GROUP_ADD_ROBOT, FRIEND_ADD, GROUP_MEMBER_ADD})

# 回复端点模板 (event_type -> lambda event: endpoint_str)
_REPLY_ENDPOINTS = {
    GROUP_AT_MESSAGE_CREATE: lambda e: f'/v2/groups/{e.group_id}/messages',
    GROUP_MESSAGE_CREATE: lambda e: f'/v2/groups/{e.group_id}/messages',
    C2C_MESSAGE_CREATE: lambda e: f'/v2/users/{e.raw_user_id or e.user_id}/messages',
    AT_MESSAGE_CREATE: lambda e: f'/channels/{e.channel_id}/messages',
    DIRECT_MESSAGE_CREATE: lambda e: f'/dms/{e.guild_id}/messages',
    MESSAGE_CREATE: lambda e: f'/channels/{e.channel_id}/messages',
}

# 解析器实例（无状态，可复用）
_GROUP_PARSER = GroupMessageParser()
_DIRECT_PARSER = DirectMessageParser()
_CHANNEL_PARSER = ChannelMessageParser()
_CHANNEL_DM_PARSER = ChannelDirectMessageParser()
_INTERACTION_PARSER = InteractionParser()
_GROUP_ADD_PARSER = GroupAddRobotParser()
_GROUP_DEL_PARSER = GroupDelRobotParser()
_FRIEND_ADD_PARSER = FriendAddParser()
_FRIEND_DEL_PARSER = FriendDelParser()
_GROUP_MEMBER_ADD_PARSER = GroupMemberAddParser()
_GROUP_MEMBER_REMOVE_PARSER = GroupMemberRemoveParser()
_MESSAGE_PARSER = MessageParser()
_GROUP_MSG_REJECT_PARSER = GroupMsgRejectParser()
_GROUP_MSG_RECEIVE_PARSER = GroupMsgReceiveParser()
_SUBSCRIBE_STATUS_PARSER = SubscribeStatusParser()

# 解析器映射表
_PARSERS = {
    GROUP_AT_MESSAGE_CREATE: _GROUP_PARSER,
    GROUP_MESSAGE_CREATE: _GROUP_PARSER,
    C2C_MESSAGE_CREATE: _DIRECT_PARSER,
    AT_MESSAGE_CREATE: _CHANNEL_PARSER,
    DIRECT_MESSAGE_CREATE: _CHANNEL_DM_PARSER,
    MESSAGE_CREATE: _CHANNEL_PARSER,
    INTERACTION_CREATE: _INTERACTION_PARSER,
    GROUP_ADD_ROBOT: _GROUP_ADD_PARSER,
    GROUP_DEL_ROBOT: _GROUP_DEL_PARSER,
    GROUP_MEMBER_ADD: _GROUP_MEMBER_ADD_PARSER,
    GROUP_MEMBER_REMOVE: _GROUP_MEMBER_REMOVE_PARSER,
    FRIEND_ADD: _FRIEND_ADD_PARSER,
    FRIEND_DEL: _FRIEND_DEL_PARSER,
    GROUP_MSG_REJECT: _GROUP_MSG_REJECT_PARSER,
    GROUP_MSG_RECEIVE: _GROUP_MSG_RECEIVE_PARSER,
    SUBSCRIBE_MESSAGE_STATUS: _SUBSCRIBE_STATUS_PARSER,
}


# sender 方法代理表: True = 自动注入 event 作为第一参数, False = 直接透传
_PROXY_METHODS = {
    'reply': True,
    'reply_image': True,
    'reply_voice': True,
    'reply_video': True,
    'reply_file': True,
    'reply_ark': True,
    'reply_tuwen': True,
    'recall': True,
    'ack_interaction': True,
    'send_to_group': False,
    'send_to_user': False,
    'send_to_channel': False,
    'send_image': False,
    'send_wakeup': False,
}


class Event:
    """事件数据容器"""

    __slots__ = (
        'appid',
        'event_id',
        'event_type',
        'raw',
        'message_id',
        'content',
        'raw_content',
        'timestamp',
        'user_id',
        'raw_user_id',
        'username',
        'member_role',
        'union_openid',
        'is_bot',
        'group_id',
        'guild_id',
        'channel_id',
        'message_scene',
        'message_reference_id',
        'msg_elements',
        'attachments',
        'image_url',
        'is_group',
        'is_direct',
        'is_channel',
        'is_interaction',
        'is_lifecycle',
        'interaction_data',
        'scene',
        'sharer_id',
        'subscribe_results',
        'mentions',
        'bot_member_role',
        'is_at_self',
        'is_at_other_bot',
        'is_at_other_user',
        'is_at_all',
        '_sender',
        '_reply_log_cb',
        '_reply_plugin_name',
        'callback_code',
        'error',
        '_ack_future',
        '_ack_timer',
        '_ack_timeout',
    )

    def __init__(self):
        self.appid = None
        self.event_id = None
        self.event_type = None
        self.raw = None
        self.message_id = None
        self.content = ''
        self.raw_content = ''
        self.timestamp = None
        self.user_id = None
        self.raw_user_id = None
        self.username = None
        self.member_role = ''
        self.union_openid = None
        self.is_bot = None
        self.group_id = None
        self.guild_id = None
        self.channel_id = None
        self.message_scene = {}
        self.message_reference_id = ''
        self.msg_elements = []
        self.attachments = []
        self.image_url = None
        self.is_group = False
        self.is_direct = False
        self.is_channel = False
        self.is_interaction = False
        self.is_lifecycle = False
        self.interaction_data = None
        self.scene = None
        self.sharer_id = None
        self.subscribe_results = []
        self.mentions = []
        self.bot_member_role = ''
        self.is_at_self = False
        self.is_at_other_bot = False
        self.is_at_other_user = False
        self.is_at_all = False
        self._sender = None
        self._reply_log_cb = None
        self._reply_plugin_name = ''
        # 交互回调 (op12) 状态码: 插件可通过 set_callback_code() 自定义
        self.callback_code = None
        # 最近一次媒体上传失败的响应 (供插件排查错误)
        self.error = None
        self._ack_future = None
        self._ack_timer = None
        self._ack_timeout = _DEFAULT_ACK_TIMEOUT

    # ==================== 构造 ====================

    @classmethod
    def from_webhook(cls, headers, body):
        appid = headers.get('X-Bot-Appid', headers.get('x-bot-appid', ''))
        payload = body if isinstance(body, dict) else json.loads(body)
        event = cls()
        event.appid = str(appid)
        event._parse_payload(payload)
        return event

    @classmethod
    def from_websocket(cls, appid, payload):
        event = cls()
        event.appid = str(appid)
        if isinstance(payload, str):
            payload = json.loads(payload)
        event._parse_payload(payload)
        return event

    # ==================== 解析 ====================

    def _parse_payload(self, payload):
        self.event_id = payload.get('id', '')
        self.event_type = payload.get('t', '')
        self.raw = payload

        d = payload.get('d')
        if not d or not isinstance(d, dict):
            return

        et = self.event_type
        self.is_group = et in GROUP_TYPES
        self.is_direct = et in DIRECT_TYPES
        self.is_channel = et in CHANNEL_TYPES
        self.is_interaction = et == INTERACTION_CREATE
        self.is_lifecycle = et in LIFECYCLE_TYPES

        parser = _PARSERS.get(et)
        if parser:
            parser.parse(self, d)
        elif et in MESSAGE_TYPES:
            _MESSAGE_PARSER.parse(self, d)

    # ==================== 属性 ====================

    def get(self, path):
        """JSON 路径取值: get('d/author/id')"""
        data = self.raw
        try:
            for key in path.split('/'):
                data = data[key]
            return data
        except (KeyError, TypeError):
            return None

    @property
    def chat_type(self):
        if self.is_group:
            return 'group'
        if self.is_direct:
            return 'direct'
        if self.is_channel:
            return 'channel'
        return 'unknown'

    @property
    def chat_id(self):
        if self.is_group:
            return self.group_id
        if self.is_direct:
            return self.user_id
        if self.is_channel:
            return self.channel_id
        return ''

    @property
    def reply_endpoint(self):
        fn = _REPLY_ENDPOINTS.get(self.event_type)
        if fn:
            return fn(self)
        et = self.event_type
        if et == INTERACTION_CREATE:
            return self._fallback_msg_ep(strict=True) or f'/interactions/{self.message_id}'
        if et in (GROUP_ADD_ROBOT, FRIEND_ADD, GROUP_MEMBER_ADD):
            return self._fallback_msg_ep()
        return ''

    def _fallback_msg_ep(self, strict=False):
        """group/user 消息端点 (strict: 仅在 is_group/is_direct 时返回)"""
        gid = self.group_id
        uid = self.raw_user_id or self.user_id
        if gid and (not strict or self.is_group):
            return f'/v2/groups/{gid}/messages'
        if uid and (not strict or self.is_direct):
            return f'/v2/users/{uid}/messages'
        return ''

    @property
    def recall_endpoint(self):
        gid = self.group_id
        uid = self.raw_user_id or self.user_id
        if self.is_group and gid:
            return f'/v2/groups/{gid}/messages/{{message_id}}'
        if self.is_direct and uid:
            return f'/v2/users/{uid}/messages/{{message_id}}'
        if self.channel_id:
            return f'/channels/{self.channel_id}/messages/{{message_id}}?hidetip=true'
        return ''

    @property
    def media_upload_endpoint(self):
        gid = self.group_id
        uid = self.raw_user_id or self.user_id
        if self.is_group and gid:
            return f'/v2/groups/{gid}/files'
        if uid:
            return f'/v2/users/{uid}/files'
        return ''

    @property
    def needs_msg_id(self):
        return self.event_type in _MSG_ID_TYPES

    @property
    def needs_event_id(self):
        return self.event_type in _EVENT_ID_TYPES

    # ==================== 交互回调 (op12 ACK) — 插件可用 set_callback_code 自定义状态码 ====================

    def start_ack_countdown(self):
        """创建 ACK future 并启动超时定时器 (由传输层在分发前调用)"""
        loop = asyncio.get_running_loop()
        if self._ack_future is None:
            self._ack_future = loop.create_future()
        self._reschedule_ack_timer()

    def _reschedule_ack_timer(self):
        loop = asyncio.get_running_loop()
        if self._ack_timer is not None:
            self._ack_timer.cancel()
        self._ack_timer = loop.call_later(self._ack_timeout, self._fire_default_ack)

    def set_ack_timeout(self, seconds):
        """插件自定义等待时长 (秒), 用于需要持续处理后再返回 code 的插件。"""
        self._ack_timeout = float(seconds)
        if self._ack_timer is not None and self._ack_future is not None and not self._ack_future.done():
            self._reschedule_ack_timer()

    def set_callback_code(self, code):
        """插件设置交互回调状态码 (随 op12 ACK 立即返回, 插件可继续运行)。"""
        self.callback_code = int(code)
        self._resolve_ack(self.callback_code)

    def _resolve_ack(self, code):
        if self._ack_timer is not None:
            self._ack_timer.cancel()
            self._ack_timer = None
        if self._ack_future is not None and not self._ack_future.done():
            self._ack_future.set_result(code)

    def _fire_default_ack(self):
        """超时兜底: 插件迟迟未设置 code, 用默认 code 返回。"""
        self._ack_timer = None
        if self._ack_future is not None and not self._ack_future.done():
            self._ack_future.set_result(_DEFAULT_CALLBACK_CODE)

    def finish_dispatch(self):
        """插件分发结束: 若插件未自定义 code, 立即用默认 code 返回 (不等满超时)。"""
        if self._ack_future is not None and not self._ack_future.done():
            self._resolve_ack(_DEFAULT_CALLBACK_CODE)

    async def wait_ack_code(self):
        """等待交互 code: 插件设置 / 分发结束 / 超时 三者任一触发后返回。"""
        if self._ack_future is None:
            return _DEFAULT_CALLBACK_CODE
        return await self._ack_future

    # ==================== 发送代理 (event.reply → sender.reply) ====================

    @property
    def sender(self):
        """底层 MessageSender 实例 (高级用法)"""
        return self._sender

    def __getattr__(self, name):
        inject = _PROXY_METHODS.get(name)
        if inject is not None and self._sender is not None:
            method = getattr(self._sender, name)
            return partial(method, self) if inject else method
        raise AttributeError(f"'{type(self).__name__}' has no attribute '{name}'")

    def __repr__(self):
        parts = [f'Event({self.event_type}']
        if self.appid:
            parts.append(f'bot={self.appid}')
        if self.user_id:
            parts.append(f'user={self.user_id[:8]}...')
        if self.group_id:
            parts.append(f'group={self.group_id[:8]}...')
        if self.content:
            preview = self.content[:30] + ('...' if len(self.content) > 30 else '')
            parts.append(f'content={preview!r}')
        return ' '.join(parts) + ')'


# ==================== 签名验证辅助 ====================


def extract_sign_headers(headers):
    get = headers.get
    appid = get('X-Bot-Appid') or get('x-bot-appid')
    ts = get('X-Signature-Timestamp') or get('x-signature-timestamp')
    sig = get('X-Signature-Ed25519') or get('x-signature-ed25519')
    method = get('X-Signature-Method') or get('x-signature-method', 'Ed25519')
    if not all((appid, ts)):
        return None
    return appid, ts, sig, method
