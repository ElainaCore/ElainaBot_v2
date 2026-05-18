"""Event 数据类单元测试"""

from core.message.event import Event


class TestEventCreation:
    """Event 创建测试"""

    def test_create_empty_event(self):
        evt = Event()
        assert evt.appid is None
        assert evt.content == ''
        assert evt.is_group is False
        assert evt.is_direct is False
        assert evt.attachments == []

    def test_set_attributes(self):
        evt = Event()
        evt.event_type = 'GROUP_AT_MESSAGE_CREATE'
        evt.is_group = True
        evt.user_id = 'user_abc'
        evt.content = '/help'
        evt.group_id = 'group_001'

        assert evt.event_type == 'GROUP_AT_MESSAGE_CREATE'
        assert evt.is_group is True
        assert evt.user_id == 'user_abc'
        assert evt.content == '/help'
        assert evt.group_id == 'group_001'

    def test_event_repr(self):
        evt = Event()
        evt.event_type = 'GROUP_AT_MESSAGE_CREATE'
        evt.user_id = 'user_abc'
        r = repr(evt)
        assert 'user_abc' in r


class TestEventConstants:
    """事件类型常量测试"""

    def test_message_types_frozenset(self):
        from core.message.event import MESSAGE_TYPES

        assert 'GROUP_AT_MESSAGE_CREATE' in MESSAGE_TYPES
        assert 'C2C_MESSAGE_CREATE' in MESSAGE_TYPES

    def test_group_types(self):
        from core.message.event import GROUP_TYPES

        assert 'GROUP_AT_MESSAGE_CREATE' in GROUP_TYPES
        assert 'C2C_MESSAGE_CREATE' not in GROUP_TYPES

    def test_direct_types(self):
        from core.message.event import DIRECT_TYPES

        assert 'C2C_MESSAGE_CREATE' in DIRECT_TYPES

    def test_lifecycle_types(self):
        from core.message.event import LIFECYCLE_TYPES

        assert 'FRIEND_ADD' in LIFECYCLE_TYPES
        assert 'GROUP_ADD_ROBOT' in LIFECYCLE_TYPES


class TestEventParsing:
    """事件解析测试"""

    def test_parse_group_at_message(self):
        from core.message.event import GROUP_AT_MESSAGE_CREATE

        payload = {
            'id': 'evt_001',
            'op': 0,
            't': GROUP_AT_MESSAGE_CREATE,
            'd': {
                'id': 'msg_001',
                'author': {
                    'id': 'user_abc',
                    'member_openid': 'member_xyz',
                },
                'content': '/help',
                'timestamp': '2026-05-17T10:00:00+08:00',
                'group_openid': 'group_001',
                'message_reference': {},
                'attachments': [],
            },
        }
        result = Event.from_websocket('123456', payload)
        assert result.event_type == GROUP_AT_MESSAGE_CREATE
        # 群聊消息 user_id 优先使用 member_openid (parse_message_generic line 58)
        assert result.user_id == 'member_xyz'
        assert result.content == '/help'
        assert result.is_group is True
        assert result.is_direct is False
        assert result.appid == '123456'

    def test_parse_c2c_message(self):
        from core.message.event import C2C_MESSAGE_CREATE

        payload = {
            'id': 'evt_002',
            'op': 0,
            't': C2C_MESSAGE_CREATE,
            'd': {
                'id': 'msg_002',
                'author': {
                    'id': 'user_def',
                },
                'content': 'hello',
                'timestamp': '2026-05-17T10:01:00+08:00',
                'message_reference': {},
                'attachments': [],
            },
        }
        result = Event.from_websocket('123456', payload)
        assert result.event_type == C2C_MESSAGE_CREATE
        assert result.user_id == 'user_def'
        assert result.is_direct is True
        assert result.is_group is False

    def test_chat_type_property(self):
        from core.message.event import GROUP_AT_MESSAGE_CREATE

        payload = {
            'id': 'evt_003',
            'op': 0,
            't': GROUP_AT_MESSAGE_CREATE,
            'd': {
                'id': 'msg_003',
                'author': {'id': 'user_ghi'},
                'content': 'test',
                'timestamp': '2026-05-17T10:02:00+08:00',
                'group_openid': 'group_002',
                'message_reference': {},
                'attachments': [],
            },
        }
        result = Event.from_websocket('123456', payload)
        assert result.chat_type == 'group'
