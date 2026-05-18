#!/usr/bin/env python
"""事件数据容器 + 类型常量"""

import json
from functools import partial

from core.message.parsers import (
    parse_channel_direct_message,
    parse_channel_message,
    parse_direct_message,
    parse_friend_add,
    parse_friend_del,
    parse_group_add_robot,
    parse_group_del_robot,
    parse_group_message,
    parse_interaction,
    parse_message_generic,
)

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

# 表态
MESSAGE_REACTION_ADD = 'MESSAGE_REACTION_ADD'
MESSAGE_REACTION_REMOVE = 'MESSAGE_REACTION_REMOVE'

# 频道管理
GUILD_UPDATE = 'GUILD_UPDATE'

# 频道
AT_MESSAGE_CREATE = 'AT_MESSAGE_CREATE'
DIRECT_MESSAGE_CREATE = 'DIRECT_MESSAGE_CREATE'
MESSAGE_CREATE = 'MESSAGE_CREATE'
MESSAGE_AUDIT_PASS = 'MESSAGE_AUDIT_PASS'
MESSAGE_AUDIT_REJECT = 'MESSAGE_AUDIT_REJECT'

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
_EVENT_ID_TYPES = frozenset({INTERACTION_CREATE, GROUP_ADD_ROBOT, FRIEND_ADD})

# 回复端点模板 (event_type -> lambda event: endpoint_str)
_REPLY_ENDPOINTS = {
    GROUP_AT_MESSAGE_CREATE: lambda e: (f'/v2/groups/{e.group_openid or e.group_id}/messages'),
    GROUP_MESSAGE_CREATE: lambda e: (f'/v2/groups/{e.group_openid or e.group_id}/messages'),
    C2C_MESSAGE_CREATE: lambda e: f'/v2/users/{e.raw_user_id or e.user_id}/messages',
    AT_MESSAGE_CREATE: lambda e: f'/channels/{e.channel_id}/messages',
    DIRECT_MESSAGE_CREATE: lambda e: f'/dms/{e.guild_id}/messages',
    MESSAGE_CREATE: lambda e: f'/channels/{e.channel_id}/messages',
}

# 解析器映射表
_PARSERS = {
    GROUP_AT_MESSAGE_CREATE: parse_group_message,
    GROUP_MESSAGE_CREATE: parse_group_message,
    C2C_MESSAGE_CREATE: parse_direct_message,
    AT_MESSAGE_CREATE: parse_channel_message,
    DIRECT_MESSAGE_CREATE: parse_channel_direct_message,
    MESSAGE_CREATE: parse_channel_message,
    INTERACTION_CREATE: parse_interaction,
    GROUP_ADD_ROBOT: parse_group_add_robot,
    GROUP_DEL_ROBOT: parse_group_del_robot,
    FRIEND_ADD: parse_friend_add,
    FRIEND_DEL: parse_friend_del,
}


# sender 方法代理表: True = 自动注入 event 作为第一参数, False = 直接透传
_PROXY_METHODS = {
    'reply': True,
    'reply_image': True,
    'reply_voice': True,
    'reply_video': True,
    'reply_file': True,
    'reply_ark': True,
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
        'op',
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
        'member_openid',
        'union_openid',
        'is_bot',
        'group_id',
        'group_openid',
        'guild_id',
        'channel_id',
        'message_type',
        'msg_elements',
        'attachments',
        'image_url',
        'is_group',
        'is_direct',
        'is_channel',
        'is_interaction',
        'is_lifecycle',
        'interaction_data',
        'chat_type_code',
        'scene',
        'scene_source',
        'sharer_id',
        'scene_param',
        'mentions',
        'is_at_self',
        'is_at_other_bot',
        'is_at_all',
        '_sender',
        '_reply_log_cb',
        '_reply_plugin_name',
    )

    def __init__(self):
        self.appid = None
        self.op = None
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
        self.member_openid = None
        self.union_openid = None
        self.is_bot = None
        self.group_id = None
        self.group_openid = None
        self.guild_id = None
        self.channel_id = None
        self.message_type = None
        self.msg_elements = []
        self.attachments = []
        self.image_url = None
        self.is_group = False
        self.is_direct = False
        self.is_channel = False
        self.is_interaction = False
        self.is_lifecycle = False
        self.interaction_data = None
        self.chat_type_code = None
        self.scene = None
        self.scene_source = None
        self.sharer_id = None
        self.scene_param = None
        self.mentions = []
        self.is_at_self = False
        self.is_at_other_bot = False
        self.is_at_other_user = False
        self.is_at_all = False
        self._sender = None
        self._reply_log_cb = None
        self._reply_plugin_name = ''

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
        self.op = payload.get('op')
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
            parser(self, d)
        elif et in MESSAGE_TYPES:
            parse_message_generic(self, d)

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
        if et in (GROUP_ADD_ROBOT, FRIEND_ADD):
            return self._fallback_msg_ep()
        return ''

    def _fallback_msg_ep(self, strict=False):
        """group/user 消息端点 (strict: 仅在 is_group/is_direct 时返回)"""
        gid = self.group_openid or self.group_id
        uid = self.raw_user_id or self.user_id
        if gid and (not strict or self.is_group):
            return f'/v2/groups/{gid}/messages'
        if uid and (not strict or self.is_direct):
            return f'/v2/users/{uid}/messages'
        return ''

    @property
    def recall_endpoint(self):
        gid = self.group_openid or self.group_id
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
        gid = self.group_openid or self.group_id
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

    # ==================== 发送代理 ====================
    # event.reply(...) → sender.reply(event, ...)
    # event.send_to_group(...) → sender.send_to_group(...)

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
