"""插件上下文、数据模型与辅助函数"""

import asyncio
from core.base.logger import PLUGIN
from core.base.context import BaseContext

# 当前正在加载的插件上下文 (由 PluginManager 在加载期间赋值)
ctx = None


class PluginContext(BaseContext):
    """插件上下文 — 继承 BaseContext, 无额外方法"""

    __slots__ = ()

    def __init__(self, name, plugin_dir):
        super().__init__(name, plugin_dir, PLUGIN)

    @property
    def plugin_dir(self):
        return self._root_dir


class PluginInfo:
    """已加载插件的信息"""
    __slots__ = ('name', 'plugin_dir', 'module', 'handlers', 'on_load_funcs',
                 'on_unload_funcs', 'interceptors', 'enabled', 'load_time',
                 'error', 'ctx', 'is_large', 'meta')

    def __init__(self, name, plugin_dir):
        self.name = name
        self.plugin_dir = plugin_dir
        self.module = None
        self.handlers = []
        self.on_load_funcs = []
        self.on_unload_funcs = []
        self.interceptors = []
        self.enabled = True
        self.load_time = 0
        self.error = None
        self.ctx = None
        self.is_large = False
        self.meta = {}  # __plugin_meta__ from module


def _make_reply_log_cb(plugin_name, log_service):
    """创建插件回复日志回调 (避免在匹配循环中反复定义闭包)"""
    def cb(text, uid, gid, raw_message=''):
        if log_service:
            asyncio.ensure_future(log_service.add('message', {
                'type': 'plugin',
                'user_id': uid, 'group_id': gid,
                'content': text, 'plugin_name': plugin_name,
                'raw_message': raw_message,
            }))
    return cb
