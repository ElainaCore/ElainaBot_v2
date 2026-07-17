"""插件加载/卸载/导入 — PluginManager 的 Mixin"""

import asyncio
import importlib
import importlib.machinery
import importlib.util
import os
import sys
import time

import core.plugin.context as _ctx_mod
from core.base.logger import FRAMEWORK, PLUGIN, get_logger, report_error
from core.base.pip_helper import install_requirements as _install_deps
from core.plugin.context import PluginContext, PluginInfo
from core.plugin.decorators import (
    _pending_handlers,
    _pending_interceptors,
    _pending_on_load,
    _pending_on_unload,
)

log = get_logger(FRAMEWORK, '插件管理')

_ENTRY_NAMES = frozenset({'index.py', 'app.py', 'main.py'})


def _clear_pending():
    _pending_handlers.clear()
    _pending_on_load.clear()
    _pending_on_unload.clear()
    _pending_interceptors.clear()


def _collect_pending():
    return (
        list(_pending_handlers),
        list(_pending_on_load),
        list(_pending_on_unload),
        list(_pending_interceptors),
    )


async def _run_hooks(funcs, name):
    loop = asyncio.get_running_loop()
    for func, is_coro in funcs:
        try:
            await func() if is_coro else await loop.run_in_executor(None, func)
        except Exception as e:
            report_error(PLUGIN, name, e)


def _read_plugin_meta(module):
    if module is None:
        return {}
    raw = getattr(module, '__plugin_meta__', None)
    if not isinstance(raw, dict):
        return {}
    allowed = {
        'name',
        'author',
        'description',
        'version',
        'github',
        'homepage',
        'license',
    }
    return {k: str(v) for k, v in raw.items() if k in allowed and v}


def _sub_key(func, plugin_name, prefix):
    """从 handler 函数的 __module__ 提取子模块禁用 key (如 system/app/stats)"""
    mod = getattr(func, '__module__', '') or ''
    if mod.startswith(prefix):
        return f'{plugin_name}/{mod[len(prefix):].replace(".", "/")}'
    return ''


class _LoaderMixin:
    """插件加载/卸载/导入逻辑"""

    async def load_all(self):
        if not os.path.isdir(self._dir):
            os.makedirs(self._dir, exist_ok=True)
            log.warning(f'插件目录为空: {self._dir}')
            return
        dirs = sorted(d for d in os.listdir(self._dir) if os.path.isdir(os.path.join(self._dir, d)) and not d.startswith(('_', '.')))
        loaded = skipped = large = 0
        for name in dirs:
            try:
                entry = self._find_large_entry(os.path.join(self._dir, name))
                # 禁用检查: 目录级 or 入口文件级
                entry_key = f'{name}/{os.path.basename(entry)[:-3]}' if entry else ''
                if name in self._disabled_plugins or (entry_key and entry_key in self._disabled_plugins):
                    log.info(f'插件 [{name}] 已禁用, 跳过')
                    skipped += 1
                    continue
                if entry:
                    await self._load_large(name)
                    large += 1
                else:
                    if not self._list_py_files(os.path.join(self._dir, name)):
                        continue
                    await self.load(name)
                loaded += 1
            except Exception as e:
                report_error(PLUGIN, name, e)
        self._rebuild_handler_list()
        self._snapshot_all_mtimes()
        log.info(f'插件加载完成: {loaded}/{len(dirs)} 个 (大型 {large}, 跳过 {skipped}), 共 {self.handler_count} 个处理器')

    async def load(self, name):
        plugin_dir = os.path.join(self._dir, name)
        if not os.path.isdir(plugin_dir):
            get_logger(PLUGIN, name).error(f'[{name}]插件所在目录不存在: {plugin_dir}')
            return
        py_files = self._list_py_files(plugin_dir)
        py_files = [p for p in py_files if f'{name}/{os.path.basename(p)[:-3]}' not in self._disabled_plugins]
        if not py_files:
            get_logger(PLUGIN, name).error(f'[{name}]插件所在目录中无可用 .py 文件: {plugin_dir}')
            return
        async with self._lock:
            if name in self._plugins:
                await self._unload_plugin(name)
            await _install_deps(name, plugin_dir, skip_if_met=True)
            plugin_ctx = PluginContext(name, plugin_dir)
            _ctx_mod.ctx = plugin_ctx
            start = time.time()
            all_h, all_load, all_unload, all_ic = [], [], [], []
            first_module = None
            for py_path in py_files:
                _clear_pending()
                fname = os.path.basename(py_path)[:-3]
                mod_name = f'plugins.{name}.{fname}'
                try:
                    spec = importlib.util.spec_from_file_location(mod_name, py_path, submodule_search_locations=[plugin_dir])
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[mod_name] = module
                    spec.loader.exec_module(module)
                    if first_module is None:
                        first_module = module
                    h, lo, ul, ic = _collect_pending()
                    for item in h:
                        item['_file'] = fname
                    all_h.extend(h)
                    all_load.extend(lo)
                    all_unload.extend(ul)
                    all_ic.extend(ic)
                except Exception as e:
                    report_error(PLUGIN, f'{name}/{fname}', e)
            error = '未注册任何处理器 (可能存在导入错误)' if not all_h and py_files else ''
            plugin = _finalize_plugin(
                name,
                plugin_dir,
                first_module,
                plugin_ctx,
                all_h,
                all_load,
                all_unload,
                all_ic,
                start,
                error=error,
            )
            await _run_hooks(plugin.on_load_funcs, name)
            self._plugins[name] = plugin
            get_logger(PLUGIN, name).info(f'加载完成 ({len(py_files)} 个文件, {len(plugin.handlers)} 个处理器, {plugin.load_time:.2f}s)')

    async def _load_large(self, name):
        plugin_dir = os.path.join(self._dir, name)
        entry = self._find_large_entry(plugin_dir)
        if not entry:
            get_logger(PLUGIN, name).error(f'[{name}]大型插件入口不存在: {plugin_dir}')
            return
        async with self._lock:
            if name in self._plugins:
                await self._unload_plugin(name)
            await _install_deps(name, plugin_dir, skip_if_met=True)
            _clear_pending()
            plugin_ctx = PluginContext(name, plugin_dir)
            _ctx_mod.ctx = plugin_ctx
            start = time.time()
            module = self._import_plugin(name, plugin_dir, entry)
            h, lo, ul, ic = _collect_pending()
            # 过滤禁用子模块
            prefix = f'plugins.{name}.'
            h = [x for x in h if _sub_key(x['func'], name, prefix) not in self._disabled_plugins]
            ic = [x for x in ic if _sub_key(x['func'], name, prefix) not in self._disabled_plugins]
            plugin = _finalize_plugin(name, plugin_dir, module, plugin_ctx, h, lo, ul, ic, start, is_large=True)
            await _run_hooks(plugin.on_load_funcs, name)
            self._plugins[name] = plugin
            get_logger(PLUGIN, name).info(f'大型插件加载完成 ({len(h)} 个处理器, {plugin.load_time:.2f}s)')

    async def reload(self, name):
        is_large = bool(self._find_large_entry(os.path.join(self._dir, name)))
        await (self._load_large(name) if is_large else self.load(name))
        self._rebuild_handler_list()
        pdir = os.path.join(self._dir, name)
        if os.path.isdir(pdir):
            self._scan_plugin_mtimes(pdir)
        info = self._plugins.get(name)
        log.info(f'🔄 插件热重载: {name} ({len(info.handlers) if info else 0} 个处理器, {info.load_time:.2f}s)' if info else f'🔄 插件热重载: {name}')
        return True

    async def unload(self, name):
        async with self._lock:
            if name not in self._plugins:
                return False
            await self._unload_plugin(name)
            self._rebuild_handler_list()
            return True

    async def _unload_plugin(self, name):
        plugin = self._plugins.pop(name, None)
        if not plugin:
            return
        await _run_hooks(plugin.on_unload_funcs, name)
        from core.plugin.web_pages import clear_routes_by_owner

        clear_routes_by_owner(name)
        prefix = f'plugins.{name}'
        for k in [k for k in sys.modules if k == prefix or k.startswith(prefix + '.')]:
            sys.modules.pop(k, None)

    # ==================== 发现与导入 ====================

    @staticmethod
    def _list_py_files(plugin_dir):
        return sorted(os.path.join(plugin_dir, f) for f in os.listdir(plugin_dir) if f.endswith('.py') and not f.startswith('_') and f not in _ENTRY_NAMES)

    @staticmethod
    def _find_large_entry(plugin_dir):
        for c in ('index.py', 'app.py', 'main.py'):
            p = os.path.join(plugin_dir, c)
            if os.path.isfile(p):
                return p
        return None

    @staticmethod
    def _register_pkg(mod_name, path):
        if mod_name in sys.modules:
            return sys.modules[mod_name]
        spec = importlib.machinery.ModuleSpec(mod_name, None, is_package=True)
        spec.submodule_search_locations = [path]
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        return mod

    @classmethod
    def _import_plugin(cls, name, plugin_dir, entry_path):
        mod_name = f'plugins.{name}'
        parent = cls._register_pkg('plugins', os.path.dirname(plugin_dir))
        pkg = cls._register_pkg(mod_name, plugin_dir)
        subs = []
        with os.scandir(plugin_dir) as it:
            for e in it:
                if e.is_dir() and not e.name.startswith(('_', '.')):
                    sub = cls._register_pkg(f'{mod_name}.{e.name}', e.path)
                    setattr(pkg, e.name, sub)
                    subs.append((e.name, sub))
        spec = importlib.util.spec_from_file_location(mod_name, entry_path, submodule_search_locations=[plugin_dir])
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        setattr(parent, name, module)
        for s, sub in subs:
            setattr(module, s, sub)
        spec.loader.exec_module(module)
        return module


def _finalize_plugin(
    name,
    plugin_dir,
    module,
    ctx,
    handlers,
    on_load,
    on_unload,
    interceptors,
    start,
    *,
    is_large=False,
    error='',
):
    plugin = PluginInfo(name, plugin_dir)
    plugin.module, plugin.ctx = module, ctx
    plugin.handlers, plugin.on_load_funcs = handlers, on_load
    plugin.on_unload_funcs, plugin.interceptors = on_unload, interceptors
    plugin.is_large, plugin.load_time = is_large, time.time() - start
    plugin.meta = _read_plugin_meta(module)
    if error:
        plugin.error = error
    _ctx_mod.ctx = None
    return plugin
