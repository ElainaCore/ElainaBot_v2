"""通过随附的跨平台转换器将常见音频转换为 Tencent SILK。"""

import asyncio
import contextlib
import os
import platform
import shutil
import subprocess
import tempfile
from functools import lru_cache, partial
from pathlib import Path

from core.base.logger import FRAMEWORK, get_logger

log = get_logger(FRAMEWORK, 'silk转换')

SUPPORTED_RATES = (8000, 12000, 16000, 24000, 32000, 44100, 48000)
DEFAULT_RATE = 24000
_SILK_HEADERS = (b'\x02#!SILK_V3', b'#!SILK_V3')
_RUNTIME_ROOT = Path(__file__).resolve().parents[1] / 'silk_converter'


def is_silk(data: bytes) -> bool:
    """判断字节流是否已经是 SILK v3。"""
    return isinstance(data, bytes) and data.startswith(_SILK_HEADERS)


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

    converter = _find_converter()
    if not converter:
        raise RuntimeError('未找到当前平台的 audio-to-silk 转换器')

    with tempfile.TemporaryDirectory(prefix='elaina-silk-') as directory:
        source = Path(directory) / 'input.audio'
        output = Path(directory) / 'output.silk'
        source.write_bytes(data)
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        process = subprocess.run(
            [converter, str(source), '--output', str(output), '--rate', str(rate)],
            capture_output=True,
            check=False,
            creationflags=creationflags,
            timeout=180,
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


def shutdown_pool() -> None:
    """保留旧生命周期接口；独立转换器不维护进程池。"""


async def convert_to_silk(data: bytes, rate: int = DEFAULT_RATE) -> bytes:
    """在线程中调用转换器；失败时回退原数据，避免阻断消息发送。"""
    if is_silk(data):
        return data
    try:
        return await asyncio.to_thread(partial(audio_to_silk, data, rate))
    except Exception as error:
        log.warning(f'语音转 SILK 失败, 使用原数据发送: {error}')
        return data
