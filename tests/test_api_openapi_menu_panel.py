"""API tests for the built-in OpenAPI menu and panel service."""

import json
from types import SimpleNamespace

import web.tools._openapi.menu_panel as menu_panel


class _Request:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


class _Sender:
    def __init__(self):
        self.calls = []

    async def delete_panel(self, panel_id):
        self.calls.append(('delete', panel_id))
        return True, {}

    async def update_panel(self, panel_id, panel):
        self.calls.append(('update', panel_id, panel))
        return True, {'version': 2}

    async def update_panel_targets(self, panel_id, op, **targets):
        self.calls.append(('targets', panel_id, op, targets))
        return True, {}

    async def create_panel(self, scope, panel, **options):
        self.calls.append(('create', scope, panel, options))
        return True, {'panel_id': 'new-panel'}


def _set_sender(sender):
    bot = SimpleNamespace(sender=sender)
    menu_panel.set_context(SimpleNamespace(get_bot=lambda appid: bot))


class TestOpenAPIMenuPanel:
    async def test_menu_panel_bots_is_available_without_plugin(
        self,
        api_client,
        auth_cookies,
    ):
        response = await api_client.get(
            '/api/openapi/menu-panel/bots',
            cookies=auth_cookies,
        )

        assert response.status == 200
        body = await response.json()
        assert body['success'] is True
        assert body['data'] == {'bots': []}

    async def test_save_validates_all_changes_before_remote_writes(self):
        sender = _Sender()
        _set_sender(sender)
        request = _Request({
            'appid': '10001',
            'deleted_panel_ids': ['panel-to-delete'],
            'changes': [{'panel_id': 'panel-to-update'}],
        })

        response = await menu_panel.handle_save_panels(request)
        body = json.loads(response.text)

        assert response.status == 400
        assert body['success'] is False
        assert sender.calls == []

    async def test_append_reuses_panel_from_list_response(self):
        class Sender(_Sender):
            async def get_panels(self, scope, **kwargs):
                self.calls.append(('list', scope, kwargs))
                return ({
                    'records': [{
                        'panel_id': 'global-panel',
                        'target_type': 'all',
                        'panel': {
                            'items': [{'type': 'command', 'name': '旧指令'}],
                            'remark': '保留备注',
                        },
                    }],
                }, None)

            async def get_panel(self, *args, **kwargs):
                raise AssertionError('列表包含 panel 时不应查询详情')

        sender = Sender()
        success, data, operation = await menu_panel._append_panel_items(
            sender,
            'c2c',
            'all',
            [{'type': 'command', 'name': '新指令'}],
        )

        assert success is True
        assert data == {'version': 2}
        assert operation == 'updated'
        assert sender.calls[-1] == (
            'update',
            'global-panel',
            {
                'items': [
                    {'type': 'command', 'name': '旧指令'},
                    {'type': 'command', 'name': '新指令'},
                ],
                'remark': '保留备注',
            },
        )
