"""音频转 silk v3 (QQ 语音格式) — 跨平台

本地语音发送前默认转换为 silk v3 (tencent 变体)。

解码链 (按可用性自动选择, 任一可用即可):
    1. imageio-ffmpeg (pip 包, 自带各平台 ffmpeg 二进制)
    2. 系统 PATH 中的 ffmpeg
    3. soundfile (pip 包, 自带 libsndfile, 支持 WAV/MP3/OGG/FLAC 等)

编码使用 pilk (可选依赖, pip install pilk); 未安装时语音原样发送不做转换。
"""

import array
import asyncio
import contextlib
import os
import shutil
import subprocess
import tempfile

from core.base.logger import FRAMEWORK, get_logger

log = get_logger(FRAMEWORK, 'silk转换')

SUPPORTED_RATES = (8000, 12000, 16000, 24000, 32000, 44100, 48000)
DEFAULT_RATE = 24000  # QQ 语音常用采样率
_SILK_HEADERS = (b'\x02#!SILK_V3', b'#!SILK_V3')


def is_silk(data: bytes) -> bool:
    """判断字节流是否已是 silk v3 格式"""
    return isinstance(data, bytes) and data.startswith(_SILK_HEADERS)


# ==================== 解码: 任意音频 → 16bit 单声道 PCM ====================


def _find_ffmpeg():
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which('ffmpeg')


def _decode_ffmpeg(exe, src, rate):
    cmd = [exe, '-v', 'error', '-i', src, '-f', 's16le', '-ar', str(rate), '-ac', '1', '-']
    proc = subprocess.run(cmd, capture_output=True, check=False)
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError(f'ffmpeg 解码失败: {proc.stderr.decode(errors="replace").strip()}')
    return proc.stdout


def _decode_soundfile(src, rate):
    import soundfile as sf

    with sf.SoundFile(src) as f:
        channels, src_rate = f.channels, f.samplerate
        raw = f.buffer_read(dtype='int16')
    samples = array.array('h')
    samples.frombytes(bytes(raw))
    if not samples:
        raise RuntimeError('soundfile 解码结果为空')
    if channels > 1:
        samples = array.array('h', (sum(samples[i : i + channels]) // channels for i in range(0, len(samples) - channels + 1, channels)))
    if src_rate != rate:
        n = int(len(samples) * rate / src_rate)
        samples = array.array('h', (samples[min(i * src_rate // rate, len(samples) - 1)] for i in range(n)))
    return samples.tobytes()


def _to_pcm(src, rate):
    exe = _find_ffmpeg()
    if exe:
        try:
            return _decode_ffmpeg(exe, src, rate)
        except Exception as e:
            log.debug(f'ffmpeg 解码失败, 尝试 soundfile: {e}')
    try:
        return _decode_soundfile(src, rate)
    except ImportError:
        raise RuntimeError(
            '无法解码该音频: 未找到 imageio-ffmpeg / ffmpeg / soundfile。请安装任一解码依赖: pip install imageio-ffmpeg 或 pip install soundfile'
        ) from None


# ==================== 转换 ====================


def audio_to_silk(data: bytes, rate: int = DEFAULT_RATE) -> bytes:
    """音频字节流转 silk v3 字节流 (已是 silk 则原样返回)"""
    if is_silk(data):
        return data
    import pilk  # 可选依赖, 未安装由调用方回退

    if rate not in SUPPORTED_RATES:
        raise ValueError(f'采样率 {rate} 不受支持, 可选: {SUPPORTED_RATES}')
    src_fd, src_path = tempfile.mkstemp(suffix='.audio')
    pcm_fd, pcm_path = tempfile.mkstemp(suffix='.pcm')
    silk_fd, silk_path = tempfile.mkstemp(suffix='.silk')
    os.close(silk_fd)
    try:
        with os.fdopen(src_fd, 'wb') as f:
            f.write(data)
        try:
            pcm = _to_pcm(src_path, rate)
        except BaseException:
            os.close(pcm_fd)
            raise
        with os.fdopen(pcm_fd, 'wb') as f:
            f.write(pcm)
        pilk.encode(pcm_path, silk_path, pcm_rate=rate, tencent=True)
        with open(silk_path, 'rb') as f:
            return f.read()
    finally:
        for path in (src_path, pcm_path, silk_path):
            with contextlib.suppress(OSError):
                os.remove(path)


async def convert_to_silk(data: bytes, rate: int = DEFAULT_RATE) -> bytes:
    """异步转换 (线程池执行); 转换失败时回退原数据, 不阻断发送"""
    if is_silk(data):
        return data
    try:
        return await asyncio.to_thread(audio_to_silk, data, rate)
    except ImportError:
        log.warning('未安装 pilk, 语音不转 silk 原样发送 (可选: pip install pilk)')
        return data
    except Exception as e:
        log.warning(f'语音转 silk 失败, 使用原数据发送: {e}')
        return data
