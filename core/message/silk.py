"""通过随附的跨平台转换器将常见音频转换为 Tencent SILK。"""

import asyncio
import contextlib
import os
import platform
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

SUPPORTED_RATES = (8000, 12000, 16000, 24000, 32000, 44100, 48000)
DEFAULT_RATE = 24000
DEFAULT_BITRATE = 24000
DEFAULT_COMPLEXITY = 0
_SILK_HEADERS = (b'\x02#!SILK_V3', b'#!SILK_V3')
_RUNTIME_ROOT = Path(__file__).resolve().parents[1] / 'silk_converter'
_CONVERT_TIMEOUT = 60
_MAX_CONCURRENT_CONVERSIONS = 2
_pool: ThreadPoolExecutor | None = None


def is_silk(data: bytes) -> bool:
    """判断字节流是否已经是 SILK v3。"""
    return isinstance(data, bytes) and data.startswith(_SILK_HEADERS)


def is_audio_candidate(data: bytes) -> bool:
    """拒绝明显的网页/API 错误响应，其余格式交给 FFmpeg 判断。"""
    if not isinstance(data, bytes) or len(data) < 4:
        return False
    head = data[:64].lstrip().lower()
    return not head.startswith((b'<', b'{', b'['))


def _runtime_id() -> str | None:
    machine = platform.machine().lower()
    if machine not in {'amd64', 'x86_64'}:
        return None
    if os.name == 'nt':
        return 'win-x64'
    if platform.system() == 'Linux':
        return 'linux-x64'
    return None


def _ensure_linux_executables(converter: Path) -> None:
    if os.name == 'nt':
        return
    for executable in (converter, converter.with_name('ffmpeg')):
        if executable.is_file() and not os.access(executable, os.X_OK):
            with contextlib.suppress(OSError):
                executable.chmod(executable.stat().st_mode | 0o111)


@lru_cache(maxsize=1)
def _find_converter() -> str | None:
    configured = os.getenv('ELAINA_SILK_CONVERTER', '').strip()
    if configured:
        path = Path(configured).expanduser().resolve()
        if path.is_file():
            _ensure_linux_executables(path)
            return str(path)
        return None

    runtime_id = _runtime_id()
    if runtime_id:
        name = 'audio-to-silk.exe' if runtime_id == 'win-x64' else 'audio-to-silk'
        bundled = _RUNTIME_ROOT / runtime_id / name
        if bundled.is_file():
            _ensure_linux_executables(bundled)
            return str(bundled)

    return shutil.which('audio-to-silk')


def audio_to_silk(data: bytes, rate: int = DEFAULT_RATE) -> bytes:
    """调用独立转换器，将 FFmpeg 支持的音频字节转为 Tencent SILK。"""
    if is_silk(data):
        return data
    if rate not in SUPPORTED_RATES:
        raise ValueError(f'采样率 {rate} 不受支持, 可选: {SUPPORTED_RATES}')
    if not is_audio_candidate(data):
        raise ValueError('输入不是转换器支持的音频数据')

    converter = _find_converter()
    if not converter:
        raise RuntimeError('未找到当前平台的 audio-to-silk 转换器')

    with tempfile.TemporaryDirectory(prefix='elaina-silk-') as directory:
        source = Path(directory) / 'input.audio'
        output = Path(directory) / 'output.silk'
        source.write_bytes(data)
        command = [
            converter,
            str(source),
            '--output',
            str(output),
            '--rate',
            str(rate),
            '--bitrate',
            str(DEFAULT_BITRATE),
            '--complexity',
            str(DEFAULT_COMPLEXITY),
        ]
        creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        process = subprocess.run(
            command,
            capture_output=True,
            check=False,
            creationflags=creationflags,
            timeout=_CONVERT_TIMEOUT,
        )
        if process.returncode != 0:
            error = process.stderr.decode(errors='replace').strip()
            raise RuntimeError(error or f'转换器退出码: {process.returncode}')
        if not output.is_file():
            raise RuntimeError('转换器未生成输出文件')
        silk = output.read_bytes()
        if not is_silk(silk):
            raise RuntimeError('转换器未生成有效的 Tencent SILK 数据')
        return silk


def _get_pool() -> ThreadPoolExecutor:
    global _pool
    if _pool is None:
        _pool = ThreadPoolExecutor(max_workers=_MAX_CONCURRENT_CONVERSIONS, thread_name_prefix='silk-converter')
    return _pool


def shutdown_pool() -> None:
    """停止接收转换任务并取消尚未执行的排队任务。"""
    global _pool
    pool, _pool = _pool, None
    if pool:
        pool.shutdown(wait=False, cancel_futures=True)


async def convert_to_silk(data: bytes, rate: int = DEFAULT_RATE) -> bytes:
    """最多并行执行两个转换；多余任务排队等待，不跳过正常转换。"""
    if is_silk(data):
        return data
    if not is_audio_candidate(data):
        return data

    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_get_pool(), audio_to_silk, data, rate)
    except Exception:
        return data
