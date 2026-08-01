"""Hook 系统 — emit(广播) / pipeline(管道)"""

import asyncio
from collections import defaultdict

from core.base.logger import FRAMEWORK, get_logger

log = get_logger(FRAMEWORK, 'Hook')


def _make_callback(cb, args, kwargs):
    """创建闭包以固定 loop 变量, 避免 B023 问题"""

    def _wrapper():
        return cb(*args, **kwargs)

    return _wrapper


class HookManager:
    """通用 Hook 管理器 (全局单例, 任意字符串作为 hook 名称)"""

    __slots__ = ('_hooks', '_sorted')

    def __init__(self):
        self._hooks = defaultdict(list)  # {name: [(pri, owner, cb, is_coro)]}
        self._sorted = {}

    def register(self, hook_name, callback, *, owner='unknown', priority=100):
        """注册 hook 回调 (priority 越小越先执行)"""
        self._hooks[hook_name].append((priority, owner, callback, asyncio.iscoroutinefunction(callback)))
        self._sorted.pop(hook_name, None)

    def unregister(self, hook_name, callback):
        """注销单个回调"""
        entries = self._hooks.get(hook_name)
        if not entries:
            return
        self._hooks[hook_name] = [e for e in entries if e[2] is not callback]
        self._sorted.pop(hook_name, None)

    def unregister_owner(self, owner):
        """注销某个 owner 的所有 hook"""
        for name in list(self._hooks):
            orig = self._hooks[name]
            filtered = [e for e in orig if e[1] != owner]
            if len(filtered) == len(orig):
                continue
            self._sorted.pop(name, None)
            if filtered:
                self._hooks[name] = filtered
            else:
                del self._hooks[name]

    def _get_sorted(self, hook_name):
        cached = self._sorted.get(hook_name)
        if cached is not None:
            return cached
        result = sorted(self._hooks.get(hook_name, []), key=lambda x: x[0])
        self._sorted[hook_name] = result
        return result

    async def emit(self, hook_name, *args, **kwargs):
        """广播执行 — 依次调用所有回调, 异常不中断"""
        loop = asyncio.get_running_loop()
        for _, owner, cb, is_coro in self._get_sorted(hook_name):
            try:
                if is_coro:
                    await cb(*args, **kwargs)
                else:
                    await loop.run_in_executor(None, _make_callback(cb, args, kwargs))
            except Exception as e:
                log.warning(f"[{owner}] hook '{hook_name}': {e}")

    async def pipeline(self, hook_name, data):
        """管道执行 — 数据依次流过回调, 返回 None 表示拦截"""
        if data is None:
            return None
        loop = asyncio.get_running_loop()
        for _, owner, cb, is_coro in self._get_sorted(hook_name):
            try:
                data = await cb(data) if is_coro else await loop.run_in_executor(None, cb, data)
                if data is None:
                    return None
            except Exception as e:
                log.warning(f"[{owner}] hook '{hook_name}': {e}")
        return data

    def has(self, hook_name):
        """是否有回调注册"""
        return bool(self._hooks.get(hook_name))

    def list_hooks(self):
        """列出所有已注册 hook"""
        return {name: [{'owner': e[1], 'priority': e[0]} for e in self._get_sorted(name)] for name, entries in self._hooks.items() if entries}

    def clear(self):
        self._hooks.clear()
        self._sorted.clear()


_instance = None


def get_hook_manager():
    """获取全局 HookManager"""
    global _instance
    try:
        from core.application import get_app

        app = get_app()
        if app is not None:
            return app.hook_manager
    except ImportError:
        pass
    if _instance is None:
        _instance = HookManager()
    return _instance


def reset_hook_manager():
    global _instance
    _instance = HookManager()
    return _instance
