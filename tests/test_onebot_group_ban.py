from datetime import datetime
from types import SimpleNamespace

from modules.onebot_adapter.action_context import ActionContext
from modules.onebot_adapter.action_registry import ActionRegistry
from modules.onebot_adapter.actions.set_group_ban import SetGroupBanAction


class FakeSender:
    def __init__(self, members=None, *, query_error=None, set_result=(True, {})):
        self.members = members or []
        self.query_error = query_error
        self.set_result = set_result
        self.query_calls = []
        self.set_calls = []

    async def get_group_restrict_chat_setting(self, group_id, *, return_error=False):
        self.query_calls.append((group_id, return_error))
        if self.query_error:
            return None, self.query_error
        return {'global_rule': {}, 'members': self.members}, None

    async def set_group_member_mute(self, group_id, members):
        self.set_calls.append((group_id, members))
        return self.set_result


class FakeIDMapper:
    async def to_openid_by_type(self, value, id_type):
        return {('group', 10001): 'group-openid', ('user', 20002): 'member-openid'}.get((id_type, value))


def build_context(sender, *, id_mapper=None, log_records=None):
    records = log_records if log_records is not None else []

    def record(level):
        return lambda message: records.append((level, message))

    return ActionContext(
        log=SimpleNamespace(debug=record('debug'), info=record('info'), warning=record('warning')),
        senders={'app': sender},
        id_mapper=id_mapper,
    )


async def test_set_group_ban_action_is_registered_and_adds_mute():
    sender = FakeSender()
    log_records = []
    registry = ActionRegistry.create_default(build_context(sender, log_records=log_records))

    before = datetime.now().astimezone()
    result = await registry.dispatch(
        'set_group_ban',
        {'group_id': 'group-openid', 'user_id': 'member-openid', 'duration': 600},
        echo='echo-1',
    )
    after = datetime.now().astimezone()

    assert result == {'status': 'ok', 'retcode': 0, 'data': {}, 'echo': 'echo-1'}
    assert sender.query_calls == [('group-openid', True)]
    group_id, members = sender.set_calls[0]
    assert group_id == 'group-openid'
    assert members[0]['op'] == 'add'
    assert members[0]['member_openid'] == 'member-openid'
    expire_at = datetime.fromisoformat(members[0]['mute_expire_at'])
    assert before.timestamp() + 599 <= expire_at.timestamp() <= after.timestamp() + 601
    assert ('info', 'set_group_ban 请求: group_id=group-openid, user_id=member-openid, duration=600') in log_records
    assert ('info', 'set_group_ban 成功: op=add, group_id=group-openid, user_id=member-openid, duration=600') in log_records


async def test_set_group_ban_action_updates_and_deletes_existing_mute():
    sender = FakeSender(members=[{'member_openid': 'member-openid'}])
    action = SetGroupBanAction(build_context(sender, id_mapper=FakeIDMapper()))

    update_result = await action.execute({'group_id': 10001, 'user_id': 20002, 'duration': 120})
    delete_result = await action.execute({'group_id': 10001, 'user_id': 20002, 'duration': 0})

    assert update_result['status'] == 'ok'
    assert delete_result['status'] == 'ok'
    assert sender.set_calls[0][1][0]['op'] == 'update'
    assert sender.set_calls[1] == (
        'group-openid',
        [{'op': 'del', 'member_openid': 'member-openid'}],
    )


async def test_set_group_ban_action_returns_platform_query_error():
    sender = FakeSender(query_error={'code': 11293, 'message': 'permission denied'})
    log_records = []
    action = SetGroupBanAction(build_context(sender, log_records=log_records))

    result = await action.execute({'group_id': 'group-openid', 'user_id': 'member-openid', 'duration': 60}, echo='e')

    assert result == {
        'status': 'failed',
        'retcode': 11293,
        'data': None,
        'msg': 'permission denied',
        'wording': 'permission denied',
        'echo': 'e',
    }
    assert sender.set_calls == []
    assert any(
        level == 'warning'
        and 'set_group_ban 查询禁言状态失败' in message
        and "'code': 11293" in message
        and 'permission denied' in message
        for level, message in log_records
    )


async def test_set_group_ban_action_logs_platform_set_error():
    sender = FakeSender(set_result=(False, {'code': 11293, 'message': 'permission denied'}))
    log_records = []
    action = SetGroupBanAction(build_context(sender, log_records=log_records))

    result = await action.execute(
        {'group_id': 'group-openid', 'user_id': 'member-openid', 'duration': 60},
        echo='e',
    )

    assert result['status'] == 'failed'
    assert result['retcode'] == 11293
    assert result['msg'] == 'permission denied'
    assert any(
        level == 'warning'
        and 'set_group_ban 设置失败: op=add' in message
        and "'code': 11293" in message
        and 'permission denied' in message
        for level, message in log_records
    )
