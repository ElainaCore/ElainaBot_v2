"""普通插件文件的面板开关应立即影响真实消息分发。"""

import asyncio
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.base.config import cfg
from core.plugin.manager import PluginManager
from tests.stress.mocks.event_factory import EventFactory
from tests.stress.mocks.message_sender import MockMessageSender
from web.tools._plugin_mgr import shared
from web.tools._plugin_mgr.files import handle_toggle_plugin

APPID = '123456'
NAME = 'toggle_fixture'


class RecordingSender(MockMessageSender):
    def __init__(self):
        super().__init__(APPID)
        self.replies = []
        self.replied = asyncio.Event()

    async def reply(self, event, content=None, **kwargs):
        result = await super().reply(event, content, **kwargs)
        self.replies.append(content)
        self.replied.set()
        return result


@pytest.fixture
async def toggle_env(api_config_dir, monkeypatch):
    monkeypatch.setattr(cfg, '_ready', False)
    cfg.init(f'{api_config_dir}/config')
    cfg.set_bot_setting(APPID, 'message.send_default_response', False)
    root = Path(api_config_dir)
    plugin_dir = root / 'plugins' / NAME
    plugin_dir.mkdir(parents=True)
    pm = PluginManager(str(root / 'plugins'), bot_appid=APPID)
    monkeypatch.setitem(shared._state, 'base_dir', str(root))
    monkeypatch.setitem(shared._state, 'bot_manager', SimpleNamespace(plugin_manager=pm))
    yield root, plugin_dir, pm
    await pm.unload(NAME)


def write_plugin(plugin_dir, stem):
    (plugin_dir / f'{stem}.py').write_text(
        f'''from core.plugin.decorators import handler

@handler(r'^{stem}$', name='{stem}')
async def command(event, match):
    await event.reply('{stem} reply')
''',
        encoding='utf-8',
    )


async def toggle(stem, action):
    async def request_json():
        return {'name': NAME, 'file': stem, 'action': action}

    response = await handle_toggle_plugin(SimpleNamespace(json=request_json))
    assert response.status == 200
    body = json.loads(response.text)
    assert body['success'] is True
    return body


async def replies(pm, command):
    sender = RecordingSender()
    event = EventFactory.group_at_message(command, appid=APPID)
    if await pm.dispatch(event, sender):
        await asyncio.wait_for(sender.replied.wait(), timeout=1)
    return sender.replies


@pytest.mark.integration
async def test_disable_last_file_stops_replies(toggle_env):
    root, plugin_dir, pm = toggle_env
    write_plugin(plugin_dir, 'sample')
    await pm.load_all()
    assert await replies(pm, 'sample') == ['sample reply']

    body = await toggle('sample', 'disable')
    assert body['message'] == f'{NAME}/sample 已禁用'
    assert pm.is_plugin_disabled(f'{NAME}/sample')
    assert json.loads((root / 'data' / 'plugins_disabled.json').read_text()) == [f'{NAME}/sample']
    assert await replies(pm, 'sample') == []
    assert NAME not in pm.plugins
    assert pm.handler_count == 0


@pytest.mark.integration
async def test_enable_file_after_all_disabled_loads_again(toggle_env):
    _, plugin_dir, pm = toggle_env
    write_plugin(plugin_dir, 'sample')
    await pm.load_all()
    await toggle('sample', 'disable')
    assert NAME not in pm.plugins

    await toggle('sample', 'enable')
    assert not pm.is_plugin_disabled(f'{NAME}/sample')
    assert await replies(pm, 'sample') == ['sample reply']
    assert pm.handler_count == 1


@pytest.mark.integration
async def test_disable_files_individually_and_repeat_reload(toggle_env):
    _, plugin_dir, pm = toggle_env
    write_plugin(plugin_dir, 'first')
    write_plugin(plugin_dir, 'second')
    await pm.load_all()
    assert await replies(pm, 'first') == ['first reply']
    assert await replies(pm, 'second') == ['second reply']

    await toggle('first', 'disable')
    assert await replies(pm, 'first') == []
    assert await replies(pm, 'second') == ['second reply']
    assert pm.handler_count == 1

    await toggle('second', 'disable')
    await toggle('second', 'disable')
    await pm.reload(NAME)
    assert await replies(pm, 'first') == []
    assert await replies(pm, 'second') == []
    assert NAME not in pm.plugins
    assert pm.handler_count == 0

    await toggle('first', 'enable')
    assert await replies(pm, 'first') == ['first reply']
    assert await replies(pm, 'second') == []
    assert pm.handler_count == 1


@pytest.mark.integration
async def test_disabled_files_stay_disabled_after_manager_restart(toggle_env, monkeypatch):
    root, plugin_dir, pm = toggle_env
    write_plugin(plugin_dir, 'first')
    write_plugin(plugin_dir, 'second')
    await pm.load_all()
    await toggle('first', 'disable')
    await toggle('second', 'disable')
    await pm.unload(NAME)

    restarted = PluginManager(str(root / 'plugins'), bot_appid=APPID)
    monkeypatch.setitem(shared._state, 'bot_manager', SimpleNamespace(plugin_manager=restarted))
    try:
        await restarted.load_all()
        assert restarted.get_disabled_plugins() == [f'{NAME}/first', f'{NAME}/second']
        assert NAME not in restarted.plugins
        assert restarted.handler_count == 0
        assert await replies(restarted, 'first') == []
        assert await replies(restarted, 'second') == []

        await toggle('second', 'enable')
        assert await replies(restarted, 'first') == []
        assert await replies(restarted, 'second') == ['second reply']
        assert restarted.handler_count == 1
    finally:
        await restarted.unload(NAME)


@pytest.mark.integration
async def test_last_file_disable_runs_unload_hook_once(toggle_env):
    _, plugin_dir, pm = toggle_env
    write_plugin(plugin_dir, 'sample')
    marker = plugin_dir / 'unloaded.txt'
    with (plugin_dir / 'sample.py').open('a', encoding='utf-8') as f:
        f.write('''
from pathlib import Path
from core.plugin.decorators import on_unload

@on_unload
async def unloaded():
    marker = Path(__file__).with_name('unloaded.txt')
    with marker.open('a', encoding='utf-8') as f:
        f.write('unloaded\\n')
''')
    await pm.load_all()
    assert not marker.exists()
    await toggle('sample', 'disable')
    assert marker.read_text(encoding='utf-8') == 'unloaded\n'
    await toggle('sample', 'disable')
    await pm.reload(NAME)
    assert marker.read_text(encoding='utf-8') == 'unloaded\n'
    assert await replies(pm, 'sample') == []


@pytest.mark.integration
@pytest.mark.parametrize('missing', [False, True], ids=['empty-directory', 'missing-directory'])
async def test_reload_unavailable_directory_preserves_previous_instance(toggle_env, missing):
    _, plugin_dir, pm = toggle_env
    write_plugin(plugin_dir, 'sample')
    await pm.load_all()
    previous = pm.plugins[NAME]
    (plugin_dir / 'sample.py').unlink()
    if missing:
        shutil.rmtree(plugin_dir)

    await pm.reload(NAME)
    assert pm.plugins[NAME] is previous
    assert await replies(pm, 'sample') == ['sample reply']


@pytest.mark.integration
async def test_enabling_child_does_not_load_disabled_large_entry(toggle_env):
    _, plugin_dir, pm = toggle_env
    write_plugin(plugin_dir, 'main')
    write_plugin(plugin_dir, 'sample')
    await pm.load_all()
    assert await replies(pm, 'main') == ['main reply']
    await toggle('main', 'disable')
    assert NAME not in pm.plugins

    await toggle('sample', 'enable')
    assert pm.is_plugin_disabled(f'{NAME}/main')
    assert NAME not in pm.plugins
    assert await replies(pm, 'main') == []
    assert pm.handler_count == 0


@pytest.mark.integration
async def test_enabling_file_preserves_directory_level_disable(toggle_env):
    _, plugin_dir, pm = toggle_env
    write_plugin(plugin_dir, 'sample')
    pm.disable_plugin(NAME)
    pm.disable_plugin(f'{NAME}/sample')
    await pm.load_all()
    assert NAME not in pm.plugins

    await toggle('sample', 'enable')
    assert pm.is_plugin_disabled(NAME)
    assert not pm.is_plugin_disabled(f'{NAME}/sample')
    assert NAME not in pm.plugins
    assert await replies(pm, 'sample') == []
