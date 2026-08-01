"""文件监视 (代码变更自动热重载) — PluginManager 的 Mixin"""

import asyncio
import contextlib
import os

from core.base.logger import FRAMEWORK, PLUGIN, get_logger, report_error

log = get_logger(FRAMEWORK, '插件管理')


class _WatcherMixin:
    """文件变更监视 + 自动热重载"""

    def _iter_py_mtimes(self, pdir):
        """单次 scandir 遍历, 直接复用目录项的 stat, 减少磁盘系统调用"""
        try:
            with os.scandir(pdir) as it:
                entries = list(it)
        except OSError:
            return
        for e in entries:
            with contextlib.suppress(OSError):
                if e.is_dir(follow_symlinks=False):
                    yield from self._iter_py_mtimes(e.path)
                elif e.name.endswith('.py') and not e.name.startswith('_'):
                    yield e.path, e.stat().st_mtime

    def _scan_plugin_mtimes(self, pdir):
        for fp, mt in self._iter_py_mtimes(pdir):
            self._file_mtimes[fp] = mt

    def _snapshot_all_mtimes(self):
        self._file_mtimes.clear()
        for name in self._plugins:
            pdir = os.path.join(self._dir, name)
            if os.path.isdir(pdir):
                self._scan_plugin_mtimes(pdir)

    def _plugin_of(self, filepath):
        return os.path.relpath(filepath, self._dir).split(os.sep)[0]

    def _detect_changed_plugins(self):
        """单次遍历同时检测新增/修改/删除, 避免逐文件 getmtime + 二次 os.walk"""
        changed = set()
        current = {}
        for name in self._plugins:
            pdir = os.path.join(self._dir, name)
            if os.path.isdir(pdir):
                current.update(self._iter_py_mtimes(pdir))
        for fp, mt in current.items():
            if self._file_mtimes.get(fp) != mt:
                changed.add(self._plugin_of(fp))
        for fp in self._file_mtimes.keys() - current.keys():
            changed.add(self._plugin_of(fp))
            self._file_mtimes.pop(fp, None)
        return changed

    async def _watcher_loop(self):
        loop = asyncio.get_running_loop()
        while self._watcher_running:
            try:
                await asyncio.sleep(2)
                changed = await loop.run_in_executor(None, self._detect_changed_plugins)
                for name in changed:
                    if name in self._plugins:
                        try:
                            await self.reload(name)
                        except Exception as e:
                            report_error(PLUGIN, name, e)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.debug(f'插件监视异常: {e}')

    def start_watcher(self):
        if self._watcher_task and not self._watcher_task.done():
            return
        self._watcher_running = True
        self._watcher_task = asyncio.ensure_future(self._watcher_loop())
        log.info('📡 插件文件监视已启动')

    def stop_watcher(self):
        self._watcher_running = False
        if self._watcher_task and not self._watcher_task.done():
            self._watcher_task.cancel()
            self._watcher_task = None
