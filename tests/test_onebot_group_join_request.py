from types import SimpleNamespace

import pytest

from core.message.event import GROUP_JOIN_REQUEST, Event
from modules.onebot_adapter.action_context import ActionContext
from modules.onebot_adapter.action_registry import ActionRegistry
from modules.onebot_adapter.lib.event_converter import convert_lifecycle_event
from modules.onebot_adapter.lib.group_join_request_flag import GroupJoinRequestFlagCodec


class _IdMapper:
    async def to_qq(self, openid, id_type='user'):
        return {
            ('group_001', 'group'): 20001,
            ('user_001', 'user'): 10001,
        }[(openid, id_type)]


class _Sender:
    def __init__(self, result=(True, {})):
        self.result = result
        self.calls = []

    async def review_group_join_request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result


def _join_request_event():
    return Event.from_websocket(
        '102000001',
        {
            'op': 0,
            'id': 'GROUP_JOIN_REQUEST:event_001',
            't': GROUP_JOIN_REQUEST,
            'd': {
                'group_openid': 'group_001',
                'join_request_id': 'request_001',
                'member_openid': 'user_001',
                'username': '申请人',
                'apply_at': '2026-08-06T12:10:53+08:00',
                'verify_info': {'verify_message': '剑网3玩家'},
            },
        },
    )


@pytest.mark.asyncio
async def test_join_request_converts_to_onebot_group_request():
    converted = await convert_lifecycle_event(_join_request_event(), _IdMapper(), 3889013279)

    assert converted == {
        'time': converted['time'],
        'self_id': 3889013279,
        'post_type': 'request',
        'request_type': 'group',
        'sub_type': 'add',
        'group_id': 20001,
        'user_id': 10001,
        'invitor_id': 0,
        'comment': '剑网3玩家',
        'flag': converted['flag'],
        'event_id': 'GROUP_JOIN_REQUEST:event_001',
        'real_user_id': 'user_001',
        'real_group_id': 'group_001',
    }
    assert GroupJoinRequestFlagCodec.decode(converted['flag']) == {
        'group_openid': 'group_001',
        'member_openid': 'user_001',
        'join_request_id': 'request_001',
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('approve', 'expected_op', 'reason'),
    [
        (True, 'approve', ''),
        (False, 'decline', '等级过低'),
    ],
)
async def test_set_group_add_request_calls_qbot_review_api(approve, expected_op, reason):
    sender = _Sender()
    ctx = ActionContext(
        log=SimpleNamespace(info=lambda *_: None, warning=lambda *_: None, debug=lambda *_: None),
        senders={'102000001': sender},
    )
    registry = ActionRegistry.create_default(ctx)
    flag = GroupJoinRequestFlagCodec.encode('group_001', 'user_001', 'request_001')

    response = await registry.dispatch(
        'set_group_add_request',
        {
            'flag': flag,
            'sub_type': 'add',
            'approve': approve,
            'reason': reason,
        },
        echo='echo-1',
        appid='102000001',
    )

    assert response['status'] == 'ok'
    assert response['echo'] == 'echo-1'
    assert sender.calls == [
        (
            ('group_001', 'user_001', expected_op),
            {
                'join_request_id': 'request_001',
                'reject_reason': reason,
            },
        )
    ]


@pytest.mark.asyncio
async def test_set_group_add_request_rejects_unknown_flag():
    sender = _Sender()
    ctx = ActionContext(log=SimpleNamespace(), senders={'102000001': sender})

    response = await ActionRegistry.create_default(ctx).dispatch(
        'set_group_add_request',
        {'flag': 'unknown', 'sub_type': 'add', 'approve': True},
        appid='102000001',
    )

    assert response['status'] == 'failed'
    assert sender.calls == []


@pytest.mark.asyncio
async def test_set_group_add_request_requires_explicit_boolean_decision():
    sender = _Sender()
    ctx = ActionContext(log=SimpleNamespace(), senders={'102000001': sender})
    flag = GroupJoinRequestFlagCodec.encode('group_001', 'user_001', 'request_001')

    response = await ActionRegistry.create_default(ctx).dispatch(
        'set_group_add_request',
        {'flag': flag, 'sub_type': 'add', 'approve': 'unexpected'},
        appid='102000001',
    )

    assert response['status'] == 'failed'
    assert sender.calls == []
