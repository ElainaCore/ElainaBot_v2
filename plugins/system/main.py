"""系统管理插件 (大型插件入口)

包含:
    基础信息: 我的id、关于、原始数据
    管理功能: dm调试、重启、黑名单管理
    查询功能: 查询机器人
    示例功能: 媒体发送、ark卡片、撤回、主动消息等
"""

from core.plugin.decorators import on_load, on_unload
from core.base.logger import get_logger, PLUGIN

# 导入 app 子模块, 触发 @handler 注册
from plugins.system.app import basic    # noqa: F401
from plugins.system.app import admin    # noqa: F401、
from plugins.system.app import examples  # noqa: F401

log = get_logger(PLUGIN, "系统管理")


@on_load
def _on_load():
    log.info("✅ 系统管理插件已加载")


@on_unload
def _on_unload():
    log.info("系统管理插件已卸载")
