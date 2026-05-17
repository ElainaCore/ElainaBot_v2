"""插件管理器 — 加载/卸载/分发/热重载"""

import asyncio
import os
from collections import OrderedDict

from core.base.logger import FRAMEWORK, get_logger
from core.plugin._blacklist import _BlacklistMixin
from core.plugin._dispatch import _DispatchMixin
from core.plugin._loader import _LoaderMixin
from core.plugin._watcher import _WatcherMixin

log = get_logger(FRAMEWORK, "插件管理")


class PluginManager(_LoaderMixin, _WatcherMixin, _DispatchMixin, _BlacklistMixin):
    """插件管理器 — 通过 Mixin 组合加载/分发/监视/黑名单能力"""

    def __init__(self, plugins_dir="plugins", bot_appid=""):
        self._dir = os.path.abspath(plugins_dir)
        self._appid = str(bot_appid)
        self._plugins = OrderedDict()
        self._all_handlers = []
        self._all_interceptors = []
        self._handlers_any_et = []
        self._et_merged = {}
        self._blacklist_users = set()
        self._blacklist_groups = set()
        self._plugin_bots = {}
        self._lock = asyncio.Lock()
        self._base_dir = os.path.dirname(self._dir)
        self._file_mtimes = {}
        self._watcher_task = None
        self._watcher_running = False
        self._load_blacklists()
        self._load_plugin_bots()

    @property
    def plugins(self):
        return dict(self._plugins)

    @property
    def handler_count(self):
        return len(self._all_handlers)

    # ==================== 索引构建 ====================

    def _rebuild_handler_list(self):
        handlers, intercepts = [], []
        for plugin in self._plugins.values():
            if not plugin.enabled:
                continue
            for h in plugin.handlers:
                h["_plugin"] = plugin.name
                handlers.append(h)
            for ic in plugin.interceptors:
                ic["_plugin"] = plugin.name
                intercepts.append(ic)
        self._all_handlers = sorted(handlers, key=lambda h: -h["priority"])
        self._all_interceptors = sorted(intercepts, key=lambda i: -i["priority"])
        self._apply_bot_bindings()
        self._build_dispatch_index()

    def _apply_bot_bindings(self):
        pb = self._plugin_bots
        for h in self._all_handlers:
            h["_allowed_bots"] = _resolve_allowed_bots(
                pb, h.get("_plugin", ""), h.get("_file", "")
            )

    # ==================== 管理接口 ====================

    def enable_plugin(self, name):
        if name in self._plugins:
            self._plugins[name].enabled = True
            self._rebuild_handler_list()
            return True
        return False

    def disable_plugin(self, name):
        if name in self._plugins:
            self._plugins[name].enabled = False
            self._rebuild_handler_list()
            return True
        return False

    def get_plugin_list(self):
        return [
            {
                "name": p.name,
                "enabled": p.enabled,
                "handlers": [h["name"] for h in p.handlers],
                "handler_count": len(p.handlers),
                "load_time": round(p.load_time, 3),
                "error": p.error,
                "is_large": p.is_large,
            }
            for p in self._plugins.values()
        ]

    def get_command_list(self):
        return [
            {
                "name": h["name"],
                "pattern": h["pattern"],
                "desc": h["desc"],
                "plugin": h.get("_plugin", ""),
                "owner_only": h["owner_only"],
                "priority": h["priority"],
            }
            for h in self._all_handlers
        ]

    def get_web_plugin_info(self):
        result = {}
        for p in self._plugins.values():
            cmds = [
                {
                    "name": h.get("name", ""),
                    "pattern": h.get("pattern", ""),
                    "desc": h.get("desc", ""),
                    "owner_only": h.get("owner_only", False),
                    "group_only": h.get("group_only", False),
                }
                for h in p.handlers
            ]
            desc = ""
            if p.module and getattr(p.module, "__doc__", None):
                desc = p.module.__doc__.strip().split("\n")[0]
            result[p.name] = {"commands": cmds, "description": desc, "meta": p.meta}
        return result


def _resolve_allowed_bots(pb, plugin_name, file_name):
    if not pb:
        return None
    if file_name:
        bots = pb.get(f"{plugin_name}/{file_name}")
        if bots is not None:
            return frozenset(bots) if bots else None
    if plugin_name:
        bots = pb.get(plugin_name)
        if bots is not None:
            return frozenset(bots) if bots else None
    return None
