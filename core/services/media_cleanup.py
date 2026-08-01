"""媒体文件定时清理服务"""

import asyncio
import os
import time

from core.base.logger import FRAMEWORK, get_logger

log = get_logger(FRAMEWORK, '媒体清理')


class MediaCleanupService:
    """定时清理过期的媒体文件 (图片/语音/视频等)"""

    def __init__(self, media_dir: str, max_age_days: float = 3, interval: float = 3600):
        self._media_dir = media_dir
        self._max_age_secs = max_age_days * 86400
        self._interval = interval
        self._task = None

    async def __call__(self):
        """可直接作为 asyncio.create_task() 的协程"""
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(self._interval)
            try:
                cutoff = time.time() - self._max_age_secs
                await loop.run_in_executor(None, self._do_cleanup, cutoff)
            except Exception as e:
                log.debug(f'媒体清理失败: {e}')

    def _do_cleanup(self, cutoff: float):
        for name in os.listdir(self._media_dir):
            fpath = os.path.join(self._media_dir, name)
            try:
                if os.path.isfile(fpath) and os.path.getmtime(fpath) < cutoff:
                    os.remove(fpath)
            except OSError:
                pass

    def start(self):
        self._task = asyncio.create_task(self())

    def stop(self):
        if self._task:
            self._task.cancel()
            self._task = None
