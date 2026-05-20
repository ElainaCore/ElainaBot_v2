"""系统管理与示例插件"""

import importlib
import os

from core.base.logger import PLUGIN, get_logger
from core.plugin.decorators import on_load, on_unload

# ==================== 插件元数据 ====================
# 插件作者可在此处填写信息, 将在 Web 面板中展示
__plugin_meta__ = {
    'name': '系统管理',
    'author': 'ElainaBot',
    'description': '框架内置系统插件, 提供基础信息、管理、查询、示例等功能',
    'version': '1.0.0',
    'github': 'https://github.com/ElainaCore/ElainaBot_v2',
}

# 动态加载 app 下所有子模块, 触发 @handler / 侧边栏页面等注册
_app_dir = os.path.join(os.path.dirname(__file__), 'app')
for _name in os.listdir(_app_dir):
    if _name.endswith('.py') and not _name.startswith('_'):
        mod_name = _name[:-3]
        importlib.import_module(f'plugins.system.app.{mod_name}')

log = get_logger(PLUGIN, '系统管理')


@on_load
def _on_load():
    log.info('✅ 系统管理插件已加载')


@on_unload
def _on_unload():
    log.info('系统管理插件已卸载')
