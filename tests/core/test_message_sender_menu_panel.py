"""自定义菜单与指令面板请求参数测试。"""

from core.message.sender import MessageSender


class _TokenManager:
    appid = 'test-appid'


class _RecordingSender(MessageSender):
    def __init__(self):
        super().__init__(_TokenManager())
        self.calls = []

    async def post_json(self, endpoint, payload):
        self.calls.append(('POST', endpoint, payload))
        return True, {'panel_id': 'panel-id'}

    async def put(self, endpoint, **kwargs):
        self.calls.append(('PUT', endpoint, kwargs.get('json')))
        return True, {'version': 2}


async def test_update_global_menu_wraps_menu_object():
    sender = _RecordingSender()
    menu = {'items': [{'type': 'send_message', 'name': '帮助'}]}

    ok, response = await sender.update_global_menu(menu)

    assert ok is True
    assert response == {'version': 2}
    assert sender.calls == [('PUT', '/v2/menu', {'menu': menu})]


async def test_create_specific_group_panel_builds_platform_payload():
    sender = _RecordingSender()
    panel = {'items': [{'type': 'command', 'name': '签到'}]}

    ok, response = await sender.create_panel(
        'GROUP',
        panel,
        target_type='SPECIFIC',
        group_openids=['group-1', 'group-2'],
    )

    assert ok is True
    assert response == {'panel_id': 'panel-id'}
    assert sender.calls == [(
        'POST',
        '/v2/panels',
        {
            'scope': 'group',
            'target_type': 'specific',
            'panel': panel,
            'group_openids': ['group-1', 'group-2'],
        },
    )]


async def test_panel_scope_rejects_wrong_target_kind_before_request():
    sender = _RecordingSender()

    ok, error = await sender.create_panel(
        'c2c',
        {'items': []},
        target_type='specific',
        group_openids=['group-1'],
    )

    assert ok is False
    assert 'user_openids' in error['message']
    assert sender.calls == []


async def test_update_panel_targets_requires_one_target_list():
    sender = _RecordingSender()

    ok, error = await sender.update_panel_targets('panel-id', 'add')

    assert ok is False
    assert '必须提供' in error['message']
    assert sender.calls == []
