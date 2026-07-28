"""渲染子引擎共用基类 — 并发限流 + 空闲自动回收"""

import asyncio
import contextlib
import time


class IdleEngine:
    """按需创建、空闲自动回收的渲染子引擎"""

    __slots__ = ('_cfg', '_lock', '_semaphore', '_active', '_last_release', '_cleanup_task', '_closed')

    def __init__(self, cfg, max_concurrent):
        self._cfg = cfg
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._active = 0
        self._last_release = 0.0
        self._cleanup_task = None
        self._closed = False

    def is_available(self):
        return not self._closed

    def _mark_released(self):
        self._last_release = time.monotonic()

    def _start_idle_cleanup(self, interval):
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._idle_loop(interval))

    async def _idle_loop(self, interval):
        timeout = self._cfg.get('idle_timeout', 300)
        while True:
            await asyncio.sleep(interval)
            if self._active == 0 and self._last_release and time.monotonic() - self._last_release >= timeout:
                with contextlib.suppress(Exception):
                    await self._release_idle()

    async def _release_idle(self):
        """空闲超时回调, 子类释放底层资源"""
        raise NotImplementedError

    def _stop_idle_cleanup(self):
        task, self._cleanup_task = self._cleanup_task, None
        if task and not task.done():
            task.cancel()
