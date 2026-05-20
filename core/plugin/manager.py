"""插件管理器 — 加载/卸载/分发/热重载"""

import asyncio
import json
import os
from collections import OrderedDict

from core.base.logger import FRAMEWORK, get_logger
from core.plugin._blacklist import _BlacklistMixin
from core.plugin._dispatch import _DispatchMixin
from core.plugin._loader import _LoaderMixin
from core.plugin._watcher import _WatcherMixin

log = get_logger(FRAMEWORK, '插件管理')


class PluginManager(_LoaderMixin, _WatcherMixin, _DispatchMixin, _BlacklistMixin):
    """插件管理器 — 通过 Mixin 组合加载/分发/监视/黑名单能力"""

    def __init__(self, plugins_dir='plugins', bot_appid=''):
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
        self._disabled_plugins = set()
        self._lock = asyncio.Lock()
        self._base_dir = os.path.dirname(self._dir)
        self._file_mtimes = {}
        self._watcher_task = None
        self._watcher_running = False
        self._load_blacklists()
        self._load_plugin_bots()
        self._load_disabled_plugins()
        self._migrate_ban_files()

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
                h['_plugin'] = plugin.name
                handlers.append(h)
            for ic in plugin.interceptors:
                ic['_plugin'] = plugin.name
                intercepts.append(ic)
        self._all_handlers = sorted(handlers, key=lambda h: -h['priority'])
        self._all_interceptors = sorted(intercepts, key=lambda i: -i['priority'])
        self._apply_bot_bindings()
        self._build_dispatch_index()

    def _apply_bot_bindings(self):
        pb = self._plugin_bots
        for h in self._all_handlers:
            h['_allowed_bots'] = _resolve_allowed_bots(pb, h.get('_plugin', ''), h.get('_file', ''))

    # ==================== 管理接口 ====================

    def enable_plugin(self, name):
        """启用插件: 从禁用列表移除并持久化; 已加载则直接切换状态"""
        changed = name in self._disabled_plugins
        self._disabled_plugins.discard(name)
        if changed:
            self._save_disabled_plugins()
        if name in self._plugins:
            self._plugins[name].enabled = True
            self._rebuild_handler_list()
            return True
        return changed

    def disable_plugin(self, name):
        """禁用插件: 加入禁用列表并持久化; 已加载则切换状态并重建索引"""
        changed = name not in self._disabled_plugins
        self._disabled_plugins.add(name)
        if changed:
            self._save_disabled_plugins()
        if name in self._plugins:
            self._plugins[name].enabled = False
            self._rebuild_handler_list()
            return True
        return changed

    def is_plugin_disabled(self, name):
        """检查插件是否在持久化禁用列表中"""
        return name in self._disabled_plugins

    def get_disabled_plugins(self):
        """获取所有被持久化禁用的插件名列表"""
        return sorted(self._disabled_plugins)

    def get_plugin_list(self):
        return [
            {
                'name': p.name,
                'enabled': p.enabled,
                'disabled_persist': p.name in self._disabled_plugins,
                'handlers': [h['name'] for h in p.handlers],
                'handler_count': len(p.handlers),
                'load_time': round(p.load_time, 3),
                'error': p.error,
                'is_large': p.is_large,
            }
            for p in self._plugins.values()
        ]

    def get_command_list(self):
        return [
            {
                'name': h['name'],
                'pattern': h['pattern'],
                'desc': h['desc'],
                'plugin': h.get('_plugin', ''),
                'owner_only': h['owner_only'],
                'priority': h['priority'],
            }
            for h in self._all_handlers
        ]

    def get_web_plugin_info(self):
        result = {}
        for p in self._plugins.values():
            cmds = [
                {
                    'name': h.get('name', ''),
                    'pattern': h.get('pattern', ''),
                    'desc': h.get('desc', ''),
                    'owner_only': h.get('owner_only', False),
                    'group_only': h.get('group_only', False),
                }
                for h in p.handlers
            ]
            desc = ''
            if p.module and getattr(p.module, '__doc__', None):
                desc = p.module.__doc__.strip().split('\n')[0]
            result[p.name] = {'commands': cmds, 'description': desc, 'meta': p.meta}
        return result

    # ==================== 禁用插件持久化 ====================

    def _load_disabled_plugins(self):
        path = os.path.join(self._base_dir, 'data', 'plugins_disabled.json')
        if not os.path.isfile(path):
            return
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    self._disabled_plugins = set(data)
        except Exception as e:
            log.warning(f'加载禁用插件列表失败: {e}')

    def _save_disabled_plugins(self):
        path = os.path.join(self._base_dir, 'data', 'plugins_disabled.json')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(sorted(self._disabled_plugins), f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.warning(f'保存禁用插件列表失败: {e}')

    def _migrate_ban_files(self):
        """迁移旧版 .py.ban 文件: 自动纳入禁用列表并还原文件名"""
        plugins_dir = self._dir
        if not os.path.isdir(plugins_dir):
            return
        migrated = False
        for root, _, files in os.walk(plugins_dir):
            for f in files:
                if not f.endswith('.py.ban'):
                    continue
                ban_path = os.path.join(root, f)
                original = ban_path[:-4]  # 去掉 .ban
                try:
                    os.rename(ban_path, original)
                    rel = os.path.relpath(root, plugins_dir)
                    plugin_name = rel.split(os.sep)[0] if rel != '.' else os.path.basename(root)
                    self._disabled_plugins.add(plugin_name)
                    migrated = True
                    log.info(f'迁移 .ban 文件: {ban_path} → 已禁用插件 [{plugin_name}]')
                except OSError as e:
                    log.warning(f'迁移 .ban 文件失败 [{ban_path}]: {e}')
        if migrated:
            self._save_disabled_plugins()


def _resolve_allowed_bots(pb, plugin_name, file_name):
    if not pb:
        return None
    if file_name:
        bots = pb.get(f'{plugin_name}/{file_name}')
        if bots is not None:
            return frozenset(bots) if bots else None
    if plugin_name:
        bots = pb.get(plugin_name)
        if bots is not None:
            return frozenset(bots) if bots else None
    return None
