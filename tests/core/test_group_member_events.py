"""GROUP_MEMBER_ADD / GROUP_MEMBER_REMOVE 事件解析与处理测试"""

import json

import pytest

from core.bot.event import EventHandlerMixin
from core.message.event import (
    GROUP_MEMBER_ADD,
    GROUP_MEMBER_REMOVE,
    Event,
)

# ==================== 解析测试 ====================


def _build(event_type, group='group_001', member='user_001'):
    payload = {
        'op': 0,
        'id': f'{event_type}:evt_001',
        't': event_type,
        'd': {
            'timestamp': 1781706703,
            'group_openid': group,
            'member_openid': member,
        },
    }
    return Event.from_websocket('102000001', payload)


class TestGroupMemberParsing:
    def test_parse_member_add(self):
        evt = _build(GROUP_MEMBER_ADD)
        assert evt.event_type == GROUP_MEMBER_ADD
        assert evt.is_lifecycle is True
        assert evt.group_id == 'group_001'
        assert evt.user_id == 'user_001'
        assert evt.member_openid == 'user_001'

    def test_parse_member_remove(self):
        evt = _build(GROUP_MEMBER_REMOVE)
        assert evt.event_type == GROUP_MEMBER_REMOVE
        assert evt.is_lifecycle is True
        assert evt.group_id == 'group_001'
        assert evt.user_id == 'user_001'


# ==================== 处理测试 ====================


class _RecordingLogService:
    """记录 db_queue 写入并可预置 query_data 返回值"""

    def __init__(self, rows=None):
        self.rows = rows or []
        self.writes = []

    def db_queue(self, sql, params=()):
        self.writes.append((sql, params))

    def query_data(self, sql, params=()):
        return self.rows

    async def add(self, log_type, data):
        return True


class _Bot:
    def __init__(self, rows=None):
        self.appid = '102000001'
        self.name = 'test_bot'
        self.log_service = _RecordingLogService(rows)


class _Harness(EventHandlerMixin):
    def __init__(self, rows=None):
        self._init_event_state()
        self.bot = _Bot(rows)
        self._bots = {self.bot.appid: self.bot}

    def _push_web_log(self, *args, **kwargs):
        pass


def _users_of(write):
    """从 (sql, params) 写入中取出 users JSON 解析后的 uid 列表"""
    _sql, params = write
    for p in params:
        try:
            users = json.loads(p)
        except (TypeError, ValueError):
            continue
        if isinstance(users, list):
            return [u['userid'] for u in users]
    raise AssertionError('未找到 users JSON 参数')


class TestGroupMemberHandlers:
    @pytest.mark.asyncio
    async def test_add_inserts_new_group(self):
        h = _Harness(rows=[])
        await h._handle_group_member_add(h.bot, _build(GROUP_MEMBER_ADD, member='u1'))
        writes = [w for w in h.bot.log_service.writes if 'groups_users' in w[0]]
        assert writes, '应写入 groups_users'
        assert 'INSERT INTO groups_users' in writes[0][0]
        assert _users_of(writes[0]) == ['u1']

    @pytest.mark.asyncio
    async def test_add_updates_existing_group(self):
        existing = json.dumps([{'userid': 'u1', 'value': 1, 'last_active': '2020-01-01'}])
        h = _Harness(rows=[{'users': existing}])
        await h._handle_group_member_add(h.bot, _build(GROUP_MEMBER_ADD, member='u2'))
        writes = [w for w in h.bot.log_service.writes if 'groups_users' in w[0]]
        assert 'UPDATE groups_users' in writes[0][0]
        assert set(_users_of(writes[0])) == {'u1', 'u2'}

    @pytest.mark.asyncio
    async def test_remove_deletes_user(self):
        existing = json.dumps([
            {'userid': 'u1', 'value': 1, 'last_active': '2020-01-01'},
            {'userid': 'u2', 'value': 1, 'last_active': '2020-01-01'},
        ])
        h = _Harness(rows=[{'users': existing}])
        await h._handle_group_member_remove(h.bot, _build(GROUP_MEMBER_REMOVE, member='u2'))
        writes = [w for w in h.bot.log_service.writes if 'groups_users' in w[0]]
        assert 'UPDATE groups_users' in writes[0][0]
        assert _users_of(writes[0]) == ['u1']

    @pytest.mark.asyncio
    async def test_remove_absent_group_noop(self):
        h = _Harness(rows=[])
        await h._handle_group_member_remove(h.bot, _build(GROUP_MEMBER_REMOVE, member='u9'))
        writes = [w for w in h.bot.log_service.writes if 'groups_users' in w[0]]
        assert writes == []

    @pytest.mark.asyncio
    async def test_add_then_remove_via_cache(self):
        h = _Harness(rows=[])
        await h._add_user_to_group(h.bot, 'g1', 'u1')
        assert 'u1' in h._group_users_cache['g1'][1]
        await h._remove_user_from_group(h.bot, 'g1', 'u1')
        assert 'u1' not in h._group_users_cache['g1'][1]


# ==================== 群成员角色 (member_role) ====================


def _group_msg(member='user_001', role='admin', group='group_001'):
    payload = {
        'op': 0,
        'id': f'GROUP_AT_MESSAGE_CREATE:{member}',
        't': 'GROUP_AT_MESSAGE_CREATE',
        'd': {
            'id': 'msg_001',
            'content': ' hi ',
            'timestamp': '2026-06-17T23:53:09+08:00',
            'group_openid': group,
            'message_type': 0,
            'author': {
                'id': member,
                'member_openid': member,
                'union_openid': member,
                'username': '叶红尘',
                'member_role': role,
                'bot': False,
            },
        },
    }
    return Event.from_websocket('102000001', payload)


def _entries_of(write):
    """从 (sql, params) 写入中取出 users JSON 解析后的 entry 列表"""
    _sql, params = write
    for p in params:
        try:
            users = json.loads(p)
        except (TypeError, ValueError):
            continue
        if isinstance(users, list):
            return {u['userid']: u for u in users}
    raise AssertionError('未找到 users JSON 参数')


class TestMemberRole:
    def test_parse_member_role(self):
        evt = _group_msg(role='owner')
        assert evt.member_role == 'owner'

    def test_parse_member_role_absent(self):
        payload = {
            'op': 0,
            'id': 'GROUP_AT_MESSAGE_CREATE:x',
            't': 'GROUP_AT_MESSAGE_CREATE',
            'd': {'id': 'm', 'content': 'hi', 'group_openid': 'g', 'author': {'member_openid': 'u'}},
        }
        assert Event.from_websocket('102000001', payload).member_role == ''

    @pytest.mark.asyncio
    async def test_role_stored_on_insert(self):
        h = _Harness(rows=[])
        await h._add_user_to_group(h.bot, 'g1', 'u1', 'admin')
        writes = [w for w in h.bot.log_service.writes if 'groups_users' in w[0]]
        assert _entries_of(writes[0])['u1']['member_role'] == 'admin'

    @pytest.mark.asyncio
    async def test_role_updated_on_change(self):
        existing = json.dumps([{'userid': 'u1', 'value': 1, 'last_active': '2020-01-01', 'member_role': 'member'}])
        h = _Harness(rows=[{'users': existing}])
        await h._add_user_to_group(h.bot, 'g1', 'u1', 'admin')
        writes = [w for w in h.bot.log_service.writes if 'groups_users' in w[0]]
        assert 'UPDATE groups_users' in writes[0][0]
        assert _entries_of(writes[0])['u1']['member_role'] == 'admin'
