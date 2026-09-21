"""语音分段工具。"""

from __future__ import annotations

import io
import struct
import wave

MAX_VOICE_SECONDS = 300

_BITRATES = {
    3: (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    2: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}
_SAMPLE_RATES = (44100, 48000, 32000)
_SILK_HEADERS = (b'\x02#!SILK_V3', b'#!SILK_V3')


def split_voice(data: bytes, max_seconds: int = MAX_VOICE_SECONDS) -> list[bytes] | None:
    """按时长切分 MP3/WAV；无法识别格式时返回 None。"""
    if not isinstance(data, bytes) or not data:
        return None
    if data.startswith(b'RIFF') and data[8:12] == b'WAVE':
        return _split_wav(data, max_seconds)
    if data.startswith(_SILK_HEADERS):
        return _split_silk(data, max_seconds)
    if _mp3_frames(data) is not None:
        return _split_mp3(data, max_seconds)
    return None


def _split_silk(data: bytes, max_seconds: int) -> list[bytes] | None:
    header = next(item for item in _SILK_HEADERS if data.startswith(item))
    offset = len(header)
    frames = []
    while offset + 2 <= len(data):
        length = int.from_bytes(data[offset:offset + 2], 'little')
        offset += 2
        if length == 0:
            break
        if offset + length > len(data):
            return None
        frames.append(data[offset - 2:offset + length])
        offset += length
    if not frames:
        return [data]
    frames_per_part = max(1, int(max_seconds * 50))
    if len(frames) <= frames_per_part:
        return [data]
    return [header + b''.join(frames[index:index + frames_per_part]) for index in range(0, len(frames), frames_per_part)]


def _split_wav(data: bytes, max_seconds: int) -> list[bytes] | None:
    try:
        with wave.open(io.BytesIO(data), 'rb') as source:
            params = source.getparams()
            rate = params.framerate
            total = source.getnframes()
            if not rate or total <= rate * max_seconds:
                return [data]
            frames_per_part = max(1, rate * max_seconds)
            parts = []
            while True:
                frames = source.readframes(frames_per_part)
                if not frames:
                    break
                output = io.BytesIO()
                with wave.open(output, 'wb') as target:
                    target.setparams(params._replace(nframes=0))
                    target.writeframes(frames)
                parts.append(output.getvalue())
            return parts or [data]
    except (EOFError, OSError, struct.error, ValueError):
        return None


def _mp3_frames(data: bytes):
    offset = 10 if data.startswith(b'ID3') and len(data) >= 10 else 0
    if offset:
        size = 0
        for byte in data[6:10]:
            size = (size << 7) | (byte & 0x7F)
        offset += size + (10 if data[5] & 0x10 else 0)
    frames = []
    while offset + 4 <= len(data):
        header = int.from_bytes(data[offset:offset + 4], 'big')
        if header >> 21 != 0x7FF:
            break
        version = (header >> 19) & 3
        layer = (header >> 17) & 3
        bitrate_index = (header >> 12) & 15
        rate_index = (header >> 10) & 3
        padding = (header >> 9) & 1
        if version == 1 or layer != 1 or bitrate_index in (0, 15) or rate_index == 3:
            break
        version_key = 3 if version == 3 else 2
        bitrate = _BITRATES[version_key][bitrate_index]
        rate = _SAMPLE_RATES[rate_index]
        if version == 2:
            rate //= 2
        elif version == 0:
            rate //= 4
        length = (144000 * bitrate // rate if version == 3 else 72000 * bitrate // rate) + padding
        if length <= 4 or offset + length > len(data):
            break
        samples = 1152 if version == 3 else 576
        frames.append((offset, offset + length, samples / rate))
        offset += length
    return frames or None


def _split_mp3(data: bytes, max_seconds: int) -> list[bytes] | None:
    frames = _mp3_frames(data)
    if not frames:
        return None
    if sum(item[2] for item in frames) <= max_seconds:
        return [data]
    prefix = data[:frames[0][0]]
    parts = []
    start = 0
    elapsed = 0.0
    for index, (_, end, duration) in enumerate(frames):
        if elapsed and elapsed + duration > max_seconds:
            part_start = frames[start][0]
            part_end = frames[index - 1][1]
            parts.append(prefix + data[part_start:part_end] if start == 0 else data[part_start:part_end])
            start = index
            elapsed = 0.0
        elapsed += duration
    part_start = frames[start][0]
    part_end = frames[-1][1]
    parts.append(prefix + data[part_start:part_end] if start == 0 else data[part_start:part_end])
    return parts
