"""插件装饰器: handler / on_load / on_unload / interceptor"""

import re
import asyncio

# 临时注册表 (加载插件时收集, 由 PluginManager.load 消费)
_pending_handlers = []
_pending_on_load = []
_pending_on_unload = []
_pending_interceptors = []


def handler(pattern, *, name='', desc='', priority=0, owner_only=False,
            group_only=False, direct_only=False, channel_only=False,
            event_types=None, cooldown=0):
    """注册消息处理器: async def xxx(event, match)"""
    def decorator(func):
        _pending_handlers.append({
            'func': func,
            'is_coro': asyncio.iscoroutinefunction(func),
            'pattern': pattern,
            'compiled': re.compile(pattern),
            'name': name or func.__name__,
            'desc': desc,
            'priority': priority,
            'owner_only': owner_only,
            'group_only': group_only,
            'direct_only': direct_only,
            'channel_only': channel_only,
            'event_types': frozenset(event_types) if event_types else None,
            'cooldown': cooldown,
        })
        return func
    return decorator


def on_load(func):
    """插件加载时执行 (支持 async/sync)"""
    _pending_on_load.append((func, asyncio.iscoroutinefunction(func)))
    return func


def on_unload(func):
    """插件卸载时执行 (支持 async/sync)"""
    _pending_on_unload.append((func, asyncio.iscoroutinefunction(func)))
    return func


def interceptor(priority=100):
    """消息拦截器: 返回 True 则阻止后续处理"""
    def decorator(func):
        _pending_interceptors.append({
            'func': func,
            'is_coro': asyncio.iscoroutinefunction(func),
            'priority': priority,
        })
        return func
    return decorator
