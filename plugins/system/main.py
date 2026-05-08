"""系统管理与示例插件"""

# ==================== 插件元数据 ====================
# 插件作者可在此处填写信息, 将在 Web 面板中展示
__plugin_meta__ = {
    'name': '系统管理',
    'author': 'ElainaBot',
    'description': '框架内置系统插件, 提供基础信息、管理、查询、示例等功能',
    'version': '1.0.0',
    'github': 'https://github.com/ElainaCore/ElainaBot_v2',
}

from core.plugin.decorators import on_load, on_unload
from core.base.logger import get_logger, PLUGIN

# 导入 app 子模块, 触发 @handler / 侧边栏页面等注册
from plugins.system.app import basic      # noqa: F401
from plugins.system.app import admin      # noqa: F401
from plugins.system.app import stats      # noqa: F401

log = get_logger(PLUGIN, "系统管理")


@on_load
def _on_load():
    log.info("✅ 系统管理插件已加载")


@on_unload
def _on_unload():
    log.info("系统管理插件已卸载")
