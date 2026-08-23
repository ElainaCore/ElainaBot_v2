"""系统管理与示例插件"""

import importlib
from pathlib import Path

from core.base.logger import PLUGIN, get_logger
from core.plugin.decorators import on_load, on_unload

# ==================== 插件元数据 ====================
# 插件作者可在此填写信息，并在网页面板中展示
__plugin_meta__ = {
    'name': '系统管理',
    'author': 'ElainaBot',
    'description': '框架内置系统插件, 提供基础信息、管理、查询、示例等功能',
    'version': '1.1.0',
    'github': 'https://github.com/ElainaCore/ElainaBot_v2',
}

# 按固定顺序加载应用目录中的公开子模块，触发处理器和页面注册
_APP_DIR = Path(__file__).parent / 'app'
for _path in sorted(_APP_DIR.glob('[!_]*.py')):
    importlib.import_module(f'plugins.system.app.{_path.stem}')

log = get_logger(PLUGIN, '系统管理')


@on_load
def _on_load():
    log.info('✅ 系统管理插件已加载')


@on_unload
def _on_unload():
    log.info('系统管理插件已卸载')
