"""插件管理器 — 加载/卸载/分发/热重载"""

import os
import re
import sys
import time
import subprocess
import asyncio
import importlib
import importlib.util
import importlib.machinery
from collections import OrderedDict
from core.base.logger import get_logger, PLUGIN, FRAMEWORK, report_error
from core.base.config import cfg
from core.plugin.decorators import (
    _pending_handlers, _pending_on_load, _pending_on_unload, _pending_interceptors,
)
import core.plugin.context as _ctx_mod
from core.plugin.context import PluginContext, PluginInfo, _make_reply_log_cb

log = get_logger(FRAMEWORK, "插件管理")


def _clear_pending():
    _pending_handlers.clear()
    _pending_on_load.clear()
    _pending_on_unload.clear()
    _pending_interceptors.clear()


def _collect_pending():
    return (list(_pending_handlers), list(_pending_on_load),
            list(_pending_on_unload), list(_pending_interceptors))


async def _run_hooks(funcs, name):
    """依次执行 on_load/on_unload 回调, sync 在线程池执行"""
    loop = asyncio.get_running_loop()
    for func, is_coro in funcs:
        try:
            if is_coro:
                await func()
            else:
                await loop.run_in_executor(None, func)
        except Exception as e:
            report_error(PLUGIN, name, e)


def _read_plugin_meta(module):
    """从模块读取 __plugin_meta__ 字典, 支持的字段:
    name, author, description, version, github, homepage, license
    """
    if module is None:
        return {}
    raw = getattr(module, '__plugin_meta__', None)
    if not isinstance(raw, dict):
        return {}
    allowed = {'name', 'author', 'description', 'version', 'github', 'homepage', 'license'}
    return {k: str(v) for k, v in raw.items() if k in allowed and v}


class PluginManager:
    """插件管理器"""

    def __init__(self, plugins_dir='plugins', bot_appid=''):
        self._dir = os.path.abspath(plugins_dir)
        self._appid = str(bot_appid)
        self._plugins = OrderedDict()   # {name: PluginInfo}
        self._all_handlers = []          # 排序后的全部 handlers
        self._all_interceptors = []      # 排序后的拦截器
        self._blacklist_users = set()
        self._blacklist_groups = set()
        self._plugin_bots = {}           # {key: [appid, ...]} 机器人绑定
        self._lock = asyncio.Lock()
        self._base_dir = os.path.dirname(self._dir)  # 项目根目录
        self._file_mtimes = {}           # {file_path: mtime} 文件修改时间跟踪
        self._watcher_task = None        # 文件监视 asyncio.Task
        self._watcher_running = False
        self._load_blacklists()
        self._load_plugin_bots()

    @property
    def plugins(self):
        return dict(self._plugins)

    @property
    def handler_count(self):
        return len(self._all_handlers)

    # ==================== 加载 ====================

    @staticmethod
    def _register_pkg(mod_name, path):
        """注册包到 sys.modules (带完整 ModuleSpec, 兼容 Python 3.9+)"""
        if mod_name in sys.modules:
            return sys.modules[mod_name]
        spec = importlib.machinery.ModuleSpec(mod_name, None, is_package=True)
        spec.submodule_search_locations = [path]
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        return mod

    async def load_all(self):
        """加载 plugins/ 下所有插件目录"""
        if not os.path.isdir(self._dir):
            os.makedirs(self._dir, exist_ok=True)
            log.warning(f"插件目录为空: {self._dir}")
            return

        dirs = sorted(d for d in os.listdir(self._dir)
                       if os.path.isdir(os.path.join(self._dir, d))
                       and not d.startswith(('_', '.')))
        loaded = 0
        large_count = 0
        for name in dirs:
            plugin_dir = os.path.join(self._dir, name)
            try:
                if self._find_large_entry(plugin_dir):
                    await self._load_large(name)
                    large_count += 1
                else:
                    py_files = self._list_py_files(plugin_dir)
                    if not py_files:
                        continue
                    await self.load(name)
                loaded += 1
            except Exception as e:
                report_error(PLUGIN, name, e)

        self._rebuild_handler_list()
        self._snapshot_all_mtimes()
        log.info(f"插件加载完成: {loaded}/{len(dirs)} 个 (大型 {large_count}), "
                 f"共 {self.handler_count} 个处理器")

    async def load(self, name):
        """加载或重新加载小型插件 (注册目录内全部 .py)"""
        plugin_dir = os.path.join(self._dir, name)
        if not os.path.isdir(plugin_dir):
            raise FileNotFoundError(f"插件目录不存在: {plugin_dir}")
        py_files = self._list_py_files(plugin_dir)
        if not py_files:
            raise FileNotFoundError(f"插件目录中无 .py 文件: {plugin_dir}")

        async with self._lock:
            if name in self._plugins:
                await self._unload_plugin(name)

            await self._install_requirements(name, plugin_dir)

            plugin_ctx = PluginContext(name, plugin_dir)
            _ctx_mod.ctx = plugin_ctx
            start = time.time()

            all_h, all_load, all_unload, all_ic = [], [], [], []
            first_module = None

            for py_path in py_files:
                _clear_pending()
                fname = os.path.basename(py_path)[:-3]
                mod_name = f"plugins.{name}.{fname}"
                try:
                    spec = importlib.util.spec_from_file_location(
                        mod_name, py_path,
                        submodule_search_locations=[plugin_dir])
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[mod_name] = module
                    spec.loader.exec_module(module)
                    if first_module is None:
                        first_module = module
                    h, lo, ul, ic = _collect_pending()
                    for item in h:
                        item['_file'] = fname
                    all_h.extend(h); all_load.extend(lo)
                    all_unload.extend(ul); all_ic.extend(ic)
                except Exception as e:
                    report_error(PLUGIN, f"{name}/{fname}", e)

            plugin = PluginInfo(name, plugin_dir)
            plugin.module = first_module
            plugin.ctx = plugin_ctx
            plugin.handlers, plugin.on_load_funcs = all_h, all_load
            plugin.on_unload_funcs, plugin.interceptors = all_unload, all_ic
            plugin.load_time = time.time() - start
            plugin.meta = _read_plugin_meta(first_module)
            if not all_h and py_files:
                plugin.error = "未注册任何处理器 (可能存在导入错误)"

            _ctx_mod.ctx = None
            await _run_hooks(plugin.on_load_funcs, name)
            self._plugins[name] = plugin
            plog = get_logger(PLUGIN, name)
            plog.info(f"加载完成 ({len(py_files)} 个文件, {len(plugin.handlers)} 个处理器, {plugin.load_time:.2f}s)")

    async def _load_large(self, name):
        """加载大型插件 (含 index.py/app.py/main.py 入口)"""
        plugin_dir = os.path.join(self._dir, name)
        entry = self._find_large_entry(plugin_dir)
        if not entry:
            raise FileNotFoundError(f"大型插件入口不存在: {plugin_dir} (需要 index.py/app.py/main.py)")

        async with self._lock:
            if name in self._plugins:
                await self._unload_plugin(name)

            await self._install_requirements(name, plugin_dir)

            _clear_pending()
            plugin_ctx = PluginContext(name, plugin_dir)
            _ctx_mod.ctx = plugin_ctx
            start = time.time()

            module = self._import_plugin(name, plugin_dir, entry)
            h, lo, ul, ic = _collect_pending()

            plugin = PluginInfo(name, plugin_dir)
            plugin.module = module
            plugin.ctx = plugin_ctx
            plugin.handlers, plugin.on_load_funcs = h, lo
            plugin.on_unload_funcs, plugin.interceptors = ul, ic
            plugin.is_large = True
            plugin.load_time = time.time() - start
            plugin.meta = _read_plugin_meta(module)

            _ctx_mod.ctx = None
            await _run_hooks(plugin.on_load_funcs, name)
            self._plugins[name] = plugin
            plog = get_logger(PLUGIN, name)
            plog.info(f"大型插件加载完成 ({len(plugin.handlers)} 个处理器, {plugin.load_time:.2f}s)")

    async def reload(self, name):
        """热重载插件 (大型/小型均支持)"""
        plugin = self._plugins.get(name)
        if plugin and plugin.is_large:
            await self._load_large(name)
        else:
            await self.load(name)
        self._rebuild_handler_list()
        pdir = os.path.join(self._dir, name)
        if os.path.isdir(pdir):
            self._scan_plugin_mtimes(pdir)
        info = self._plugins.get(name)
        count = len(info.handlers) if info else 0
        t = f'{info.load_time:.2f}s' if info else '?'
        log.info(f"🔄 插件热重载: {name} ({count} 个处理器, {t})")
        return True

    # ==================== 文件监视 (代码变更自动热重载) ====================

    def _scan_plugin_mtimes(self, pdir):
        """记录单个插件目录下 .py 文件 mtime"""
        for root, _, files in os.walk(pdir):
            for f in files:
                if f.endswith('.py') and not f.startswith('_'):
                    fp = os.path.join(root, f)
                    try:
                        self._file_mtimes[fp] = os.path.getmtime(fp)
                    except OSError:
                        pass

    def _plugin_of(self, filepath):
        """文件路径 → 所属插件名"""
        return os.path.relpath(filepath, self._dir).split(os.sep)[0]

    def _snapshot_all_mtimes(self):
        """扫描所有插件目录, 记录 .py 文件 mtime"""
        self._file_mtimes.clear()
        for name in self._plugins:
            pdir = os.path.join(self._dir, name)
            if os.path.isdir(pdir):
                self._scan_plugin_mtimes(pdir)

    def _detect_changed_plugins(self):
        """检测文件变更, 返回需要热重载的插件名集合"""
        changed = set()
        for fp, old_mt in list(self._file_mtimes.items()):
            try:
                if os.path.getmtime(fp) != old_mt:
                    changed.add(self._plugin_of(fp))
            except OSError:
                changed.add(self._plugin_of(fp))
                self._file_mtimes.pop(fp, None)
        for name in self._plugins:
            pdir = os.path.join(self._dir, name)
            if not os.path.isdir(pdir):
                continue
            for root, _, files in os.walk(pdir):
                for f in files:
                    if f.endswith('.py') and not f.startswith('_'):
                        if os.path.join(root, f) not in self._file_mtimes:
                            changed.add(name)
        return changed

    async def _watcher_loop(self):
        """每 2 秒对比 mtime, 变更则热重载"""
        while self._watcher_running:
            try:
                await asyncio.sleep(2)
                for name in self._detect_changed_plugins():
                    if name not in self._plugins:
                        continue
                    try:
                        await self.reload(name)
                    except Exception as e:
                        report_error(PLUGIN, name, e)
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    def start_watcher(self):
        """启动文件监视"""
        if self._watcher_task and not self._watcher_task.done():
            return
        self._watcher_running = True
        self._watcher_task = asyncio.ensure_future(self._watcher_loop())
        log.info("📡 插件文件监视已启动")

    def stop_watcher(self):
        """停止文件监视"""
        self._watcher_running = False
        if self._watcher_task and not self._watcher_task.done():
            self._watcher_task.cancel()
            self._watcher_task = None

    async def unload(self, name):
        """卸载插件"""
        async with self._lock:
            if name not in self._plugins:
                return False
            await self._unload_plugin(name)
            self._rebuild_handler_list()
            return True

    async def _unload_plugin(self, name):
        """内部卸载 (含子模块清理)"""
        plugin = self._plugins.pop(name, None)
        if not plugin:
            return
        await _run_hooks(plugin.on_unload_funcs, name)
        prefix = f"plugins.{name}"
        for k in [k for k in sys.modules if k == prefix or k.startswith(prefix + '.')]:
            sys.modules.pop(k, None)

    # ==================== 发现 ====================

    @staticmethod
    def _list_py_files(plugin_dir):
        """列出小型插件目录内全部 .py (不含 _ 开头, 排除入口文件名, 不递归)"""
        _ENTRY_NAMES = {'index.py', 'app.py', 'main.py'}
        return sorted(
            os.path.join(plugin_dir, f) for f in os.listdir(plugin_dir)
            if f.endswith('.py') and not f.startswith('_') and f not in _ENTRY_NAMES)

    @staticmethod
    def _find_large_entry(plugin_dir):
        """查找大型插件入口 (index.py / app.py / main.py)"""
        for candidate in ('index.py', 'app.py', 'main.py'):
            path = os.path.join(plugin_dir, candidate)
            if os.path.isfile(path):
                return path
        return None

    @classmethod
    def _import_plugin(cls, name, plugin_dir, entry_path):
        """动态导入插件目录 (预注册包层级, 兼容 Python 3.9+)"""
        plugins_dir = os.path.dirname(plugin_dir)
        mod_name = f"plugins.{name}"
        parent = cls._register_pkg('plugins', plugins_dir)
        sub_dirs = [(s, os.path.join(plugin_dir, s))
                    for s in os.listdir(plugin_dir)
                    if os.path.isdir(os.path.join(plugin_dir, s))
                    and not s.startswith(('_', '.'))]
        for sub, sub_path in sub_dirs:
            sub_mod = cls._register_pkg(f'{mod_name}.{sub}', sub_path)
            setattr(sys.modules.get(mod_name, parent), sub, sub_mod)
        spec = importlib.util.spec_from_file_location(
            mod_name, entry_path,
            submodule_search_locations=[plugin_dir])
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        setattr(parent, name, module)
        for sub, _ in sub_dirs:
            sub_mod = sys.modules.get(f'{mod_name}.{sub}')
            if sub_mod:
                setattr(module, sub, sub_mod)
        spec.loader.exec_module(module)
        return module

    # ==================== 依赖安装 ====================

    async def _install_requirements(self, name, target_dir):
        """检查并安装 requirements.txt 依赖"""
        req_path = os.path.join(target_dir, 'requirements.txt')
        if not os.path.isfile(req_path):
            return

        if not cfg.get('settings', 'pip.auto_install', True):
            return

        mirror = cfg.get('settings', 'pip.mirror', '')
        cmd = [sys.executable, '-m', 'pip', 'install', '-r', req_path, '--quiet']
        if mirror:
            cmd.extend(['-i', mirror])

        loop = asyncio.get_running_loop()
        try:
            exit_code = await loop.run_in_executor(None, self._pip_install_sync, cmd, name)
            if exit_code != 0:
                log.warning(f"[{name}] 依赖安装可能失败 (exit={exit_code})")
        except Exception as e:
            log.warning(f"[{name}] 依赖安装异常: {e}")

    @staticmethod
    def _pip_install_sync(cmd, name):
        log.info(f"[{name}] 正在安装依赖...")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            log.info(f"[{name}] 依赖安装完成")
        else:
            stderr = result.stderr.strip()
            if stderr:
                log.warning(f"[{name}] pip: {stderr[:200]}")
        return result.returncode

    def _rebuild_handler_list(self):
        """重建全局处理器列表(按优先级排序)"""
        handlers = []
        intercepts = []
        for plugin in self._plugins.values():
            if not plugin.enabled:
                log.debug(f"[{plugin.name}] 已禁用, 跳过")
                continue
            for h in plugin.handlers:
                h['_plugin'] = plugin.name
                handlers.append(h)
            for ic in plugin.interceptors:
                ic['_plugin'] = plugin.name
                intercepts.append(ic)
            if plugin.handlers:
                log.debug(f"[{plugin.name}] {len(plugin.handlers)} 个处理器")
        self._all_handlers = sorted(handlers, key=lambda h: -h['priority'])
        self._all_interceptors = sorted(intercepts, key=lambda i: -i['priority'])
        self._apply_bot_bindings()

    def _apply_bot_bindings(self):
        """预计算每个 handler 的允许机器人集合, 避免 dispatch 时重复计算"""
        pb = self._plugin_bots
        for h in self._all_handlers:
            h['_allowed_bots'] = self._resolve_allowed_bots(
                pb, h.get('_plugin', ''), h.get('_file', ''))

    @staticmethod
    def _resolve_allowed_bots(pb, plugin_name, file_name):
        """解析 handler 允许的 appid 集合"""
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

    # ==================== 分发 ====================

    async def dispatch(self, event, sender):
        """分发事件到匹配的插件处理器"""
        content = event.content or ''
        user_id = event.user_id or ''
        appid = event.appid or self._appid
        et = event.event_type
        event._sender = sender

        # 黑名单 (纯内存查找, 无 IO)
        bl_type = self._check_blacklist(event)
        if bl_type:
            tpl = 'blacklist' if bl_type == 'user' else 'group_blacklist'
            tvars = {'user_id': user_id, 'reason': '未指明原因'} if bl_type == 'user' else None
            await event.reply(template_name=tpl, template_vars=tvars)
            return True

        # 维护模式
        if cfg.get_bot_setting(appid, 'maintenance.enabled', False) and not self._is_owner(event):
            await event.reply(template_name='maintenance')
            return True

        # 拦截器
        for ic in self._all_interceptors:
            try:
                result = await ic['func'](event) if ic['is_coro'] else \
                    await asyncio.get_running_loop().run_in_executor(None, ic['func'], event)
                if result is True:
                    return True
            except Exception as e:
                report_error(PLUGIN, ic.get('_plugin', '?'), e)

        # 匹配处理器 (支持 / 前缀自动去除)
        contents_to_try = (content[1:], content) if content[:1] == '/' and len(content) > 1 else (content,)
        log_service = None

        for try_content in contents_to_try:
            for h in self._all_handlers:
                # 快速过滤 (位掩码级检查)
                if (h['_allowed_bots'] is not None and appid not in h['_allowed_bots'])\
                        or (h['event_types'] and et not in h['event_types'])\
                        or (h['group_only'] and not event.is_group)\
                        or (h['direct_only'] and not event.is_direct)\
                        or (h['channel_only'] and not event.is_channel):
                    continue
                match = h['compiled'].search(try_content)
                if not match:
                    continue

                # 权限检查
                if h['owner_only'] and not self._is_owner(event):
                    await event.reply(template_name='owner_only',
                                      template_vars={'user_id': user_id})
                    return True

                # 惰性获取 log_service
                if log_service is None:
                    log_service = self._get_log_service(event)

                plugin_name = h['name'] or h.get('_plugin', '')
                sender._reply_log_cb = _make_reply_log_cb(plugin_name, log_service)
                sender._reply_plugin_name = plugin_name or ''

                try:
                    fn, timeout = h['func'], 30
                    if h['is_coro']:
                        await asyncio.wait_for(fn(event, match), timeout=timeout)
                    else:
                        await asyncio.wait_for(
                            asyncio.get_running_loop().run_in_executor(None, fn, event, match),
                            timeout=timeout)
                except asyncio.TimeoutError:
                    report_error(PLUGIN, plugin_name or '?',
                                 f"处理器 [{h['name']}] 超时(30s)",
                                 context={'handler': h['name'], 'user_id': user_id,
                                          'event_type': et, 'content': content[:200]})
                except Exception as e:
                    report_error(PLUGIN, plugin_name or '?', e,
                                 context={'handler': h['name'], 'user_id': user_id,
                                          'group_id': event.group_id or '',
                                          'event_type': et, 'content': content[:200]})
                finally:
                    sender._reply_log_cb = None
                    sender._reply_plugin_name = ''
                return True

        # 无匹配 -> 默认回复
        if et in ('GROUP_AT_MESSAGE_CREATE', 'C2C_MESSAGE_CREATE') \
                and cfg.get_bot_setting(appid, 'message.send_default_response', True):
            excluded = cfg.get_bot_setting(appid, 'message.default_response_excluded_regex', []) or []
            if not any(re.search(p, content) for p in excluded if p):
                await event.reply(template_name='default', template_vars={'user_id': user_id})

        return False

    # ==================== 日志服务 ====================

    _bot_manager_ref = None  # 缓存引用, 避免重复 import

    def _get_log_service(self, event):
        """获取当前 bot 的 log_service"""
        if self._bot_manager_ref is None:
            try:
                from core.bot.manager import _bot_manager_ref
                PluginManager._bot_manager_ref = _bot_manager_ref
            except Exception:
                return None
        bm = self._bot_manager_ref
        if not bm:
            return None
        bot = bm.get_bot(event.appid)
        return bot.log_service if bot else None

    # ==================== 黑名单 / 权限 ====================

    def _load_blacklists(self):
        """从文件加载黑名单"""
        data_dir = os.path.join(self._base_dir, 'data')
        os.makedirs(data_dir, exist_ok=True)
        for attr, fname in (('_blacklist_users', 'blacklist_users.txt'),
                             ('_blacklist_groups', 'blacklist_groups.txt')):
            path = os.path.join(data_dir, fname)
            items = set()
            if os.path.isfile(path):
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        for line in f:
                            line = line.strip()
                            if line and not line.startswith('#'):
                                items.add(line)
                except Exception as e:
                    log.warning(f"加载黑名单失败 [{fname}]: {e}")
            setattr(self, attr, items)

    def _save_blacklist(self, attr, fname):
        """保存黑名单到文件"""
        data_dir = os.path.join(self._base_dir, 'data')
        os.makedirs(data_dir, exist_ok=True)
        path = os.path.join(data_dir, fname)
        try:
            with open(path, 'w', encoding='utf-8') as f:
                for item in sorted(getattr(self, attr)):
                    f.write(item + '\n')
        except Exception as e:
            log.warning(f"保存黑名单失败 [{fname}]: {e}")

    def _check_blacklist(self, event):
        appid = event.appid or self._appid
        user_id, group_id = event.user_id or '', event.group_id or ''
        if user_id and cfg.get_bot_setting(appid, 'blacklist.user_enabled', False) and (
                user_id in self._blacklist_users or
                user_id in (cfg.get_bot_setting(appid, 'blacklist.user_list', []) or [])):
            return 'user'
        if group_id and cfg.get_bot_setting(appid, 'blacklist.group_enabled', False) and (
                group_id in self._blacklist_groups or
                group_id in (cfg.get_bot_setting(appid, 'blacklist.group_list', []) or [])):
            return 'group'
        return None

    def _is_owner(self, event):
        """检查是否为机器人主人"""
        if not event.user_id:
            return False
        bot_cfg = cfg.get_bot_config(event.appid or self._appid)
        return bool(bot_cfg) and event.user_id in (bot_cfg.get('owner_ids') or [])

    def add_blacklist_user(self, user_id):
        self._blacklist_users.add(user_id)
        self._save_blacklist('_blacklist_users', 'blacklist_users.txt')

    def remove_blacklist_user(self, user_id):
        self._blacklist_users.discard(user_id)
        self._save_blacklist('_blacklist_users', 'blacklist_users.txt')

    def add_blacklist_group(self, group_id):
        self._blacklist_groups.add(group_id)
        self._save_blacklist('_blacklist_groups', 'blacklist_groups.txt')

    def remove_blacklist_group(self, group_id):
        self._blacklist_groups.discard(group_id)
        self._save_blacklist('_blacklist_groups', 'blacklist_groups.txt')

    # ==================== 插件机器人绑定 ====================

    def _load_plugin_bots(self):
        """从 data/plugin_bots.yaml 加载插件机器人绑定配置"""
        import yaml
        path = os.path.join(self._base_dir, 'data', 'plugin_bots.yaml')
        if not os.path.isfile(path):
            self._plugin_bots = {}
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
            # 规范化: 值统一为字符串列表
            self._plugin_bots = {
                str(k): [str(v) for v in vs] if isinstance(vs, list) else []
                for k, vs in data.items()
            }
        except Exception as e:
            log.warning(f"加载插件机器人绑定失败: {e}")
            self._plugin_bots = {}

    def _save_plugin_bots(self):
        """保存插件机器人绑定到 data/plugin_bots.yaml"""
        import yaml
        data_dir = os.path.join(self._base_dir, 'data')
        os.makedirs(data_dir, exist_ok=True)
        path = os.path.join(data_dir, 'plugin_bots.yaml')
        try:
            with open(path, 'w', encoding='utf-8') as f:
                yaml.dump(self._plugin_bots, f, allow_unicode=True,
                          default_flow_style=False, sort_keys=False)
        except Exception as e:
            log.warning(f"保存插件机器人绑定失败: {e}")

    def get_plugin_bots(self):
        """获取插件机器人绑定配置 (供 Web API 读取)"""
        return dict(self._plugin_bots)

    def set_plugin_bots(self, data):
        """设置插件机器人绑定配置 (供 Web API 保存)

        data: {key: [appid, ...]} 其中 key 为 "插件名" 或 "插件名/文件名"
        """
        self._plugin_bots = {
            str(k): [str(v) for v in vs] if isinstance(vs, list) else []
            for k, vs in data.items()
        }
        self._save_plugin_bots()
        self._apply_bot_bindings()

    def reload_plugin_bots(self):
        """重新加载插件机器人绑定 (配置热更新)"""
        self._load_plugin_bots()
        self._apply_bot_bindings()

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
        """获取插件列表"""
        return [{'name': p.name, 'enabled': p.enabled,
                 'handlers': [h['name'] for h in p.handlers],
                 'handler_count': len(p.handlers),
                 'load_time': round(p.load_time, 3),
                 'error': p.error, 'is_large': p.is_large}
                for p in self._plugins.values()]

    def get_command_list(self):
        """获取所有命令列表"""
        return [{'name': h['name'], 'pattern': h['pattern'], 'desc': h['desc'],
                 'plugin': h.get('_plugin', ''), 'owner_only': h['owner_only'],
                 'priority': h['priority']}
                for h in self._all_handlers]

    def get_web_plugin_info(self):
        """为 Web 面板提供插件信息 (含指令列表 + 插件元数据), 按插件名分组"""
        result = {}
        for p in self._plugins.values():
            cmds = [{'name': h.get('name', ''), 'pattern': h.get('pattern', ''),
                     'desc': h.get('desc', ''), 'owner_only': h.get('owner_only', False),
                     'group_only': h.get('group_only', False)}
                    for h in p.handlers]
            desc = ''
            if p.module and getattr(p.module, '__doc__', None):
                desc = p.module.__doc__.strip().split('\n')[0]
            result[p.name] = {'commands': cmds, 'description': desc, 'meta': p.meta}
        return result
