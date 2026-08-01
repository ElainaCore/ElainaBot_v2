"""后台任务管理 — 持有 fire-and-forget 任务的强引用, 防止被 GC 提前回收"""

import asyncio
from collections.abc import Coroutine
from typing import Any

_background_tasks: set[asyncio.Task] = set()


def spawn(coro: Coroutine[Any, Any, Any]) -> asyncio.Task:
    """创建后台任务并持有强引用防止被 GC 提前回收 (事件循环仅持弱引用), 完成后自动释放"""
    task = asyncio.get_running_loop().create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task
