"""QQ群成员接口与 groups_users 同步测试。"""

import json

import pytest

from core.message.sender import MessageSender


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
    assert sender.requests[-1] == (
        'POST',
        '/v2/groups/group-1/batch_remove_members',
        {'member_openids': ['member-1'], 'add_to_member_blacklist': True},
    )

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
