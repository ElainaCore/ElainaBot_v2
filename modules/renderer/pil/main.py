#!/usr/bin/env python
"""PIL 子进程渲染池子引擎

CPU 密集的 PIL 渲染放到独立子进程执行 (独立 GIL, 不卡主进程事件循环),
全局单例供所有插件共享。常驻池 fork 后不回收, 弹性池按需创建、空闲回收,
崩溃自动重建。

插件中获取:
    pil = bot.module_manager.get("renderer").pil
    img_data, w, h = await pil.render(_render_sync, arg1, arg2)  # 函数与参数须可 pickle

配置: renderer 模块 data/pil.yaml
"""

import asyncio
import contextlib
import ctypes
import multiprocessing
import os
import pickle
import signal
import time
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

from core.base.fork_utils import close_inherited_listen_sockets
from core.base.logger import EXTENSION, get_logger
from modules.renderer.base import IdleEngine

log = get_logger(EXTENSION, 'PIL渲染池')

try:
    _libc = ctypes.CDLL('libc.so.6')
except OSError:
    _libc = None


def _trim_memory():
    """把 glibc 空闲堆内存归还系统: PIL 大图渲染后 free 的内存默认留在 arena 里不还,
    长命子进程 RSS 会居高不下, malloc_trim(0) 主动收缩。非 glibc 平台静默跳过。"""
    if _libc is not None:
        with contextlib.suppress(Exception):
            _libc.malloc_trim(0)


_DEFAULTS = {
    'min_workers': 1,
    'max_workers': 2,
    'max_concurrent': 4,
    'idle_timeout': 300,
    'resident_idle_timeout': 0,
    'task_timeout': 60,
    'max_tasks_per_worker': 300,
}

_COMMENTS = {
    'min_workers': '常驻渲染子进程数 (首次使用时创建; 0=不常驻)',
    'max_workers': '最大渲染子进程数 (超出常驻的弹性部分按需创建, 空闲自动回收)',
    'max_concurrent': '最大并发渲染任务数 (超出排队)',
    'idle_timeout': '弹性子进程空闲回收 (秒)',
    'resident_idle_timeout': '常驻子进程空闲回收 (秒), 0=不回收, 回收后下次使用时重新创建',
    'task_timeout': '单次渲染超时 (秒)',
    'max_tasks_per_worker': '常驻池累计渲染多少次后重建以释放内存 (PIL/字体缓存等常驻累积), 0=不重建',
}


def _invoke(fn, args, kwargs):
    """子进程侧执行入口: 渲染后归还空闲堆内存, 避免子进程 RSS 随大图渲染累积"""
    try:
        return fn(*args, **kwargs)
    finally:
        _trim_memory()


class PILRenderPool(IdleEngine):
    """PIL 子进程渲染池 — 常驻池 + 弹性池 (空闲回收, 崩溃重建)"""

    __slots__ = ('_resident', '_burst', '_resident_tasks')

    def __init__(self, cfg):
        super().__init__(cfg, cfg.get('max_concurrent', 4))
        self._resident = None
        self._burst = None
        self._resident_tasks = 0

    async def render(self, fn, *args, **kwargs):
        """在子进程池执行同步渲染函数并返回结果"""
        if self._closed:
            raise RuntimeError('PIL 渲染池已关闭')
        try:
            pickle.dumps((fn, args, kwargs))
        except Exception as e:
            raise ValueError(f'{getattr(fn, "__name__", fn)} 参数不可 pickle, 无法提交到子进程渲染: {e}') from e

        async with self._semaphore:
            self._active += 1
            try:
                return await self._run_in_pool(fn, args, kwargs, retried=False)
            finally:
                self._active -= 1
                self._mark_released()
                await self._maybe_recycle_resident()

    async def _run_in_pool(self, fn, args, kwargs, retried):
        pool = await self._pick_pool()
        loop = asyncio.get_running_loop()
        timeout = self._cfg.get('task_timeout', 60)
        try:
            fut = loop.run_in_executor(pool, _invoke, fn, args, kwargs)
            result = await asyncio.wait_for(fut, timeout=timeout)
            if pool is self._resident:
                self._resident_tasks += 1
            return result
        except TimeoutError:
            # 卡住的 worker 不会随 future 取消, 回收整个池避免后续任务排队在死进程后面
            fut.cancel()
            await self._discard(pool)
            log.warning(f'渲染任务 {getattr(fn, "__name__", fn)} 超时({timeout}s), 已回收渲染进程池')
            raise
        except BrokenProcessPool as e:
            await self._discard(pool)
            if retried:
                raise RuntimeError('PIL 渲染进程池连续崩溃') from e
            log.warning('渲染进程池崩溃, 重建后重试')
            return await self._run_in_pool(fn, args, kwargs, retried=True)

    async def _pick_pool(self):
        """任务分派: 常驻池优先, 并发超出常驻容量时走弹性池"""
        min_w = self._cfg.get('min_workers', 1)
        max_w = max(self._cfg.get('max_workers', 2), min_w, 1)
        burst_w = max_w - min_w
        if min_w > 0 and (self._active <= min_w or burst_w == 0):
            return await self._ensure_pool(min_w, resident=True)
        return await self._ensure_pool(burst_w or max_w, resident=False)

    async def _ensure_pool(self, workers, resident):
        pool = self._resident if resident else self._burst
        if pool is not None:
            return pool
        async with self._lock:
            pool = self._resident if resident else self._burst
            if pool is not None:
                return pool
            try:
                mp_ctx = multiprocessing.get_context('fork')
            except ValueError as e:
                raise RuntimeError('当前平台不支持 fork, PIL 子进程渲染池不可用') from e
            # fork 继承已加载的插件模块, 子进程可直接执行插件的渲染函数
            pool = ProcessPoolExecutor(max_workers=workers, mp_context=mp_ctx, initializer=close_inherited_listen_sockets)
            if resident:
                self._resident = pool
                if self._cfg.get('resident_idle_timeout', 0):
                    self._start_idle_cleanup(60)
            else:
                self._burst = pool
                self._start_idle_cleanup(60)
            log.info(f'PIL {"常驻" if resident else "弹性"}渲染进程池已创建 ({workers} worker)')
            return pool

    async def _discard(self, pool):
        async with self._lock:
            if pool is self._resident:
                self._resident = None
            elif pool is self._burst:
                self._burst = None
        procs = list(getattr(pool, '_processes', None) or {})
        with contextlib.suppress(Exception):
            pool.shutdown(wait=False, cancel_futures=True)
        for pid in procs:
            with contextlib.suppress(Exception):
                os.kill(pid, signal.SIGKILL)

    async def _maybe_recycle_resident(self):
        """常驻池累计任务达阈值且当前空闲时重建, 释放子进程内累积的内存"""
        limit = self._cfg.get('max_tasks_per_worker', 300)
        if not limit or self._active != 0:
            return
        if self._resident is not None and self._resident_tasks >= limit:
            pool = self._resident
            self._resident_tasks = 0
            await self._discard(pool)
            log.info(f'PIL 常驻渲染进程池已达 {limit} 次任务, 重建以释放内存')

    def _idle_threshold(self):
        timeouts = [
            t for t, pool in (
                (self._cfg.get('idle_timeout', 300), self._burst),
                (self._cfg.get('resident_idle_timeout', 0), self._resident),
            ) if t and pool is not None
        ]
        return min(timeouts) if timeouts else 0

    async def _release_idle(self):
        """空闲超时按各自阈值回收弹性池与常驻池"""
        elapsed = time.monotonic() - self._last_release
        if self._burst is not None and elapsed >= self._cfg.get('idle_timeout', 300):
            await self._discard(self._burst)
            log.info('PIL 弹性渲染进程池空闲回收')
        resident_timeout = self._cfg.get('resident_idle_timeout', 0)
        if resident_timeout and self._resident is not None and elapsed >= resident_timeout:
            pool = self._resident
            self._resident_tasks = 0
            await self._discard(pool)
            log.info('PIL 常驻渲染进程池空闲回收')

    async def close(self):
        self._closed = True
        self._stop_idle_cleanup()
        for pool in (self._resident, self._burst):
            if pool is not None:
                await self._discard(pool)
