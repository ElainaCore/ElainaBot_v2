"""QQ群成员接口与 groups_users 同步测试。"""

import json
import sqlite3

import pytest

from core.message.sender import MessageSender
from core.storage._schema import _SCHEMAS, _migrate_data_tables


class _TokenManager:
    appid = 'test-appid'


class _LogService:
    def __init__(self, users='[]'):
        self.users = users
        self.writes = []

    async def db_fetch_one(self, sql, params):
        return {'users': self.users}

    async def db_execute(self, sql, params):
        self.writes.append((sql, params))
        self.users = params[1]


class _RecordingSender(MessageSender):
    def __init__(self, response=None, log_service=None):
        super().__init__(_TokenManager())
        self.response = response or {}
        self.requests = []
        self._log_service = log_service

    async def get_json(self, endpoint, **kwargs):
        self.requests.append(('GET', endpoint, kwargs))
        return True, dict(self.response)

    async def post_json(self, endpoint, payload):
        self.requests.append(('POST', endpoint, payload))
        return True, {'ok': True}


@pytest.mark.asyncio
async def test_get_group_members_requests_page_and_syncs_database():
    log_service = _LogService()
    sender = _RecordingSender(
        {
            'members': [{'member_openid': 'member-1', 'username': '小明'}],
            'next_cursor': 'cursor-2',
        },
        log_service,
    )

    page = await sender.get_group_members('group-1', cursor='cursor-1')

    assert page['next_cursor'] == 'cursor-2'
    assert sender.requests == [
        ('GET', '/v2/groups/group-1/members', {'params': {'cursor': 'cursor-1'}}),
    ]
    users = json.loads(log_service.users)
    assert users == [
        {
            'userid': 'member-1',
            'value': 1,
            'last_active': '',
            'username': '小明',
        },
    ]


@pytest.mark.asyncio
async def test_member_sync_deduplicates_by_userid_and_preserves_existing_fields():
    log_service = _LogService(json.dumps([
        {'userid': 'member-1', 'value': 3, 'last_active': 'today'},
        {'userid': 'member-1', 'value': 4, 'legacy': True},
        {'userid': 'member-2', 'value': 2},
    ]))
    sender = _RecordingSender(log_service=log_service)

    await sender._sync_group_members('group-1', [
        {'member_openid': 'member-1', 'username': '新名字', 'member_role': 'admin'},
        {'member_openid': 'member-1', 'username': '最终名字'},
        {'member_openid': 'member-3', 'bot': False},
    ])

    users = json.loads(log_service.users)
    assert [item['userid'] for item in users] == ['member-1', 'member-2', 'member-3']
    assert users[0]['username'] == '最终名字'
    assert users[0]['member_role'] == 'admin'
    assert users[0]['value'] == 4
    assert users[0]['legacy'] is True


@pytest.mark.asyncio
async def test_group_member_mutations_use_platform_payload_and_limit_batches():
    sender = _RecordingSender()

    ok, response = await sender.batch_remove_group_members(
        'group-1',
        [' member-1 '],
        add_to_member_blacklist=True,
    )
    assert (ok, response) == (True, {'ok': True})
    assert sender.requests == [
        ('POST', '/v2/groups/group-1/batch_remove_members', {'member_openids': ['member-1']}),
        ('POST', '/v2/groups/group-1/member_blacklist', {'op': 'add', 'member_openids': ['member-1']}),
    ]

    await sender.operate_group_member_blacklist('group-1', 'ADD', ['member-1'])
    assert sender.requests[-1] == (
        'POST',
        '/v2/groups/group-1/member_blacklist',
        {'op': 'add', 'member_openids': ['member-1']},
    )

    page = await sender.get_group_member_blacklist('group-1', limit=999)
    assert page == {'users': [], 'next_cursor': ''}
    assert sender.requests[-1] == (
        'GET',
        '/v2/groups/group-1/member_blacklist',
        {'params': {'limit': 100}},
    )

    request_count = len(sender.requests)
    ok, error = await sender.batch_remove_group_members('group-1', ['member'] * 21)
    assert ok is False
    assert error['code'] == -1
    assert len(sender.requests) == request_count


async def test_batch_remove_reports_members_that_could_not_be_blacklisted(monkeypatch):
    sender = _RecordingSender()

    async def blacklist(group_id, op, member_openids):
        assert (group_id, op, member_openids) == ('group-1', 'add', ['member-1', 'member-2'])
        return True, {'fail_openids': ['member-2']}

    monkeypatch.setattr(sender, 'operate_group_member_blacklist', blacklist)
    ok, response = await sender.batch_remove_group_members(
        'group-1', ['member-1', 'member-2'], add_to_member_blacklist=True,
    )

    assert ok is False
    assert response['remove_members_result'] == 'success'
    assert response['fail_openids'] == ['member-2']


async def test_group_profile_survives_migration_and_is_available_locally():
    with sqlite3.connect(':memory:') as conn:
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE groups_users (
                group_id TEXT PRIMARY KEY, group_name TEXT, group_member_num INTEGER,
                is_admin INTEGER, is_full_access INTEGER, allow_proactive_msg INTEGER, in_group INTEGER
            );
            INSERT INTO groups_users VALUES ('group-1', '旧群名', 3, 1, 1, 1, 1);
            PRAGMA user_version = 20002;
        """)
        conn.executescript(_SCHEMAS['data'])
        _migrate_data_tables(conn)

        class LogService:
            async def db_execute(self, sql, params):
                conn.execute(sql, params)

            async def db_fetch_one(self, sql, params):
                return dict(conn.execute(sql, params).fetchone())

            async def group_member_entries(self, group_id):
                return []

        profile = {
            'group_name': '新群名',
            'group_member_num': 8,
            'union_group_openid': 'union-group',
            'group_finger_memo': '群简介',
            'group_class_text': '文化',
            'group_tags': ['学习', '读书'],
        }
        sender = _RecordingSender(profile, LogService())
        old = await sender.get_group_record('group-1')
        assert old['group_name'] == '旧群名'
        assert old['group_tags'] == []

        assert await sender.get_group_info('group-1') == profile
        record = await sender.get_group_record('group-1')
        assert {key: record[key] for key in profile} == profile
        assert record['is_admin'] and record['is_full_access'] and record['allow_proactive_msg']
