"""PluginManager 集成测试 — 真实插件文件创建/加载/卸载"""

import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


# ==================== Fixtures ====================


@pytest.fixture
def temp_plugins_dir():
    """创建临时插件根目录"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def plugin_manager(temp_plugins_dir):
    """创建 PluginManager 实例, 指向临时目录"""
    from core.plugin.manager import PluginManager

    return PluginManager(plugins_dir=temp_plugins_dir)


def _write_plugin_file(plugin_dir, filename, content):
    """在插件目录中写入 .py 文件"""
    filepath = os.path.join(plugin_dir, filename)
    os.makedirs(plugin_dir, exist_ok=True)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    return filepath


# ==================== 插件加载集成测试 ====================


@pytest.mark.integration
@pytest.mark.asyncio
class TestPluginLoadUnload:
    """创建真实插件文件 → 加载 → 验证 → 卸载"""

    async def test_load_single_handler_plugin(self, temp_plugins_dir, plugin_manager):
        """创建含一个 handler 的插件, 加载后可以查到"""
        plugin_dir = os.path.join(temp_plugins_dir, 'test_hello')
        _write_plugin_file(
            plugin_dir,
            'hello.py',
            '''"""测试插件 — hello"""
from core.plugin.decorators import handler

@handler(r'^hello$', name='Hello', desc='回复 hello world')
async def hello_handler(event, match):
    await event.reply("Hello World!")
''',
        )

        await plugin_manager.load('test_hello')
        plugin_manager._rebuild_handler_list()

        # 验证插件已加载
        assert 'test_hello' in plugin_manager.plugins
        plugin = plugin_manager.plugins['test_hello']
        assert plugin.enabled is True
        assert len(plugin.handlers) == 1
        assert plugin.handlers[0]['name'] == 'Hello'
        assert plugin.handlers[0]['pattern'] == '^hello$'

        # 验证 handler 已注册到管理器
        assert plugin_manager.handler_count == 1

    async def test_load_plugin_with_multiple_handlers(self, temp_plugins_dir, plugin_manager):
        """插件包含多个 handler"""
        plugin_dir = os.path.join(temp_plugins_dir, 'test_multi')
        _write_plugin_file(
            plugin_dir,
            'commands.py',
            '''"""测试插件 — 多命令"""
from core.plugin.decorators import handler

@handler(r'^ping$', name='Ping', desc='Ping')
async def ping(event, match):
    pass

@handler(r'^help$', name='Help', desc='帮助')
async def help_cmd(event, match):
    pass

@handler(r'^status$', name='Status', desc='状态', priority=10)
async def status(event, match):
    pass
''',
        )

        await plugin_manager.load('test_multi')
        plugin_manager._rebuild_handler_list()

        assert 'test_multi' in plugin_manager.plugins
        plugin = plugin_manager.plugins['test_multi']
        assert len(plugin.handlers) == 3
        assert plugin_manager.handler_count == 3

        # 验证 priority 排序 (高优先级在前)
        handlers = [h['name'] for h in plugin_manager._all_handlers]
        assert handlers[0] == 'Status'  # priority=10 最高

    async def test_disable_and_enable_plugin(self, temp_plugins_dir, plugin_manager):
        """禁用/启用插件影响 handler_count"""
        plugin_dir = os.path.join(temp_plugins_dir, 'test_toggle')
        _write_plugin_file(
            plugin_dir,
            'cmd.py',
            '''"""可切换插件"""
from core.plugin.decorators import handler

@handler(r'^toggle$', name='Toggle', desc='测试切换')
async def toggle(event, match):
    pass
''',
        )

        await plugin_manager.load('test_toggle')
        plugin_manager._rebuild_handler_list()
        assert plugin_manager.handler_count == 1

        # 禁用
        plugin_manager.disable_plugin('test_toggle')
        assert plugin_manager.plugins['test_toggle'].enabled is False
        assert plugin_manager.handler_count == 0

        # 重新启用
        plugin_manager.enable_plugin('test_toggle')
        assert plugin_manager.plugins['test_toggle'].enabled is True
        assert plugin_manager.handler_count == 1

    async def test_unload_nonexistent_plugin(self, plugin_manager):
        """加载不存在的插件不抛异常, 仅 log error 后返回"""
        await plugin_manager.load('nonexistent_plugin')  # 不抛异常

    async def test_load_plugin_with_no_py_files(self, temp_plugins_dir, plugin_manager):
        """空插件目录 (无 .py 文件) 不抛异常, 仅 log error 后返回"""
        os.makedirs(os.path.join(temp_plugins_dir, 'empty_plugin'))
        await plugin_manager.load('empty_plugin')  # 不抛异常

    async def test_plugin_list_after_load(self, temp_plugins_dir, plugin_manager):
        """get_plugin_list() 返回加载信息"""
        plugin_dir = os.path.join(temp_plugins_dir, 'test_list')
        _write_plugin_file(
            plugin_dir,
            'cmd.py',
            '''"""列表测试"""
from core.plugin.decorators import handler

@handler(r'^list$', name='ListCmd', desc='列表命令')
async def list_cmd(event, match):
    pass
''',
        )

        await plugin_manager.load('test_list')
        plugin_manager._rebuild_handler_list()

        plugin_list = plugin_manager.get_plugin_list()
        assert len(plugin_list) == 1
        info = plugin_list[0]
        assert info['name'] == 'test_list'
        assert info['enabled'] is True
        assert info['handler_count'] == 1
        assert info['handlers'] == ['ListCmd']


@pytest.mark.integration
@pytest.mark.asyncio
class TestPluginCommandList:
    """命令列表与 Web 信息接口"""

    async def test_get_command_list(self, temp_plugins_dir, plugin_manager):
        """get_command_list() 返回注册的命令"""
        plugin_dir = os.path.join(temp_plugins_dir, 'test_cmds')
        _write_plugin_file(
            plugin_dir,
            'cmds.py',
            '''"""命令测试"""
from core.plugin.decorators import handler

@handler(r'^cmd1$', name='Cmd1', desc='命令1', owner_only=True, priority=5)
async def cmd1(event, match):
    pass

@handler(r'^cmd2$', name='Cmd2', desc='命令2', priority=0)
async def cmd2(event, match):
    pass
''',
        )

        await plugin_manager.load('test_cmds')
        plugin_manager._rebuild_handler_list()

        cmds = plugin_manager.get_command_list()
        assert len(cmds) == 2

        # priority 排序: Cmd1(5) > Cmd2(0)
        assert cmds[0]['name'] == 'Cmd1'
        assert cmds[0]['owner_only'] is True
        assert cmds[1]['name'] == 'Cmd2'  # Fixed: was Cmd2
        assert cmds[1]['owner_only'] is False

    async def test_get_web_plugin_info(self, temp_plugins_dir, plugin_manager):
        """get_web_plugin_info() 返回 Web 面板所需信息"""
        plugin_dir = os.path.join(temp_plugins_dir, 'test_webinfo')
        _write_plugin_file(
            plugin_dir,
            'info.py',
            '''"""Web 信息测试插件"""
from core.plugin.decorators import handler

__plugin_meta__ = {
    'name': 'WebInfo Plugin',
    'author': 'Tester',
    'version': '1.0.0',
}

@handler(r'^webcmd$', name='WebCmd', desc='Web 命令')
async def webcmd(event, match):
    pass
''',
        )

        await plugin_manager.load('test_webinfo')
        plugin_manager._rebuild_handler_list()

        web_info = plugin_manager.get_web_plugin_info()
        assert 'test_webinfo' in web_info
        info = web_info['test_webinfo']
        assert len(info['commands']) == 1
        assert info['commands'][0]['name'] == 'WebCmd'
        assert info['meta'] == {
            'name': 'WebInfo Plugin',
            'author': 'Tester',
            'version': '1.0.0',
        }


@pytest.mark.integration
@pytest.mark.asyncio
class TestPluginIsolation:
    """测试间隔离 — 确保插件管理器状态不泄漏"""

    async def test_separate_managers_independent(self, temp_plugins_dir):
        """两个独立 PluginManager 互不影响"""
        from core.plugin.manager import PluginManager

        # Manager A — 加载插件 A
        with tempfile.TemporaryDirectory() as tmp_a:
            mgr_a = PluginManager(plugins_dir=tmp_a)
            plugin_dir_a = os.path.join(tmp_a, 'plugin_a')
            _write_plugin_file(
                plugin_dir_a,
                'cmd.py',
                '''"""Plugin A"""
from core.plugin.decorators import handler

@handler(r'^a$', name='CmdA', desc='A')
async def cmd_a(event, match):
    pass
''',
            )
            await mgr_a.load('plugin_a')
            mgr_a._rebuild_handler_list()
            assert mgr_a.handler_count == 1

            # Manager B — 加载插件 B (完全独立)
            with tempfile.TemporaryDirectory() as tmp_b:
                mgr_b = PluginManager(plugins_dir=tmp_b)
                plugin_dir_b = os.path.join(tmp_b, 'plugin_b')
                _write_plugin_file(
                    plugin_dir_b,
                    'cmd.py',
                    '''"""Plugin B"""
from core.plugin.decorators import handler

@handler(r'^b1$', name='CmdB1', desc='B1')
async def cmd_b1(event, match):
    pass

@handler(r'^b2$', name='CmdB2', desc='B2')
async def cmd_b2(event, match):
    pass
''',
                )
                await mgr_b.load('plugin_b')
                mgr_b._rebuild_handler_list()
                assert mgr_b.handler_count == 2

                # Manager A 不受 Manager B 影响
                assert mgr_a.handler_count == 1
