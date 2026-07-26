"""图床公共工具: 线程池 / MIME 检测 / 图片尺寸 / sender 获取"""

import asyncio
import mimetypes
import re
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

from core.base.logger import EXTENSION, get_logger

log = get_logger(EXTENSION, "图床服务")

DIM_PATTERN = re.compile(r'_(\d+)x(\d+)\.[^.]+$')

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36'

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='img_host')


def init_executor():
    global _executor
    _executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='img_host')


def shutdown_executor():
    _executor.shutdown(wait=False)


async def run_sync(fn, *args):
    """在线程池中执行同步函数"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, fn, *args)


def get_any_sender():
    """取第一个在线机器人的 sender"""
    try:
        from core.bot.manager import _bot_manager_ref
        if _bot_manager_ref and _bot_manager_ref._bots:
            return next(iter(_bot_manager_ref._bots.values())).sender
    except Exception as e:
        log.debug(f'获取 sender 失败: {e}')
    return None


def guess_content_type(filename):
    ct, _ = mimetypes.guess_type(filename)
    return ct or 'application/octet-stream'


def get_image_dimensions(file_bytes):
    """从 bytes 读取图片尺寸 -> (w, h) 或 None"""
    try:
        from PIL import Image
        with Image.open(BytesIO(file_bytes)) as img:
            return img.size
    except Exception:
        return None


def detect_mime(data):
    """检测图片 MIME 类型"""
    try:
        import magic
        return magic.Magic(mime=True).from_buffer(data)
    except Exception:
        return 'image/jpeg'


def parse_dimensions_from_filename(filename):
    """从文件名提取 _WxH 尺寸"""
    m = DIM_PATTERN.search(filename)
    return (int(m.group(1)), int(m.group(2))) if m else None


class BaseBed:
    """图床基类: 子类需定义 name / display_name / defaults / comments / upload"""

    name = ''
    display_name = ''
    priority = 100
    defaults = {'enabled': False}
    comments = {}

    __slots__ = ('_cfg',)

    def __init__(self, cfg):
        self._cfg = cfg or {}

    def initialize(self):
        pass

    def is_available(self):
        return bool(self._cfg.get('enabled'))

    async def upload(self, image_data, **kwargs):
        raise NotImplementedError
