import asyncio
import io
import subprocess
import threading
import wave
from pathlib import Path

import pytest

from core.message import silk


def _wav_bytes(*, seconds: float = 0.1, rate: int = 24000) -> bytes:
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(rate)
        wav_file.writeframes(b'\0\0' * int(seconds * rate))
    return output.getvalue()


def test_audio_candidate_rejects_error_page() -> None:
    assert silk.is_audio_candidate(_wav_bytes())
    assert silk.is_audio_candidate(b'ID3\x04\x00\x00\x00\x00\x00\x00')
    assert not silk.is_audio_candidate(b'<html>upstream error</html>')
    assert not silk.is_audio_candidate(b'{"error":"not found"}')


def test_audio_to_silk_uses_low_complexity(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(command)
        output = Path(command[command.index('--output') + 1])
        output.write_bytes(b'\x02#!SILK_V3payload')
        return subprocess.CompletedProcess(command, 0, b'', b'')

    monkeypatch.setattr(silk, '_find_converter', lambda: 'audio-to-silk')
    monkeypatch.setattr(silk.subprocess, 'run', fake_run)

    converted = silk.audio_to_silk(_wav_bytes())

    assert silk.is_silk(converted)
    command = commands[0]
    assert command[command.index('--complexity') + 1] == '0'
    assert command[command.index('--bitrate') + 1] == '24000'


def test_audio_to_silk_rejects_invalid_data_before_starting_process(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_run(*args, **kwargs):
        pytest.fail('invalid input must not start the converter')

    monkeypatch.setattr(silk.subprocess, 'run', unexpected_run)

    with pytest.raises(ValueError, match='不是转换器支持的音频数据'):
        silk.audio_to_silk(b'<html>bad gateway</html>')


def test_bundled_converter_smoke() -> None:
    silk._find_converter.cache_clear()
    if silk._find_converter() is None:
        pytest.skip('bundled converter is unavailable on this platform')

    converted = silk.audio_to_silk(_wav_bytes(seconds=0.2))

    assert silk.is_silk(converted)
    assert len(converted) > len(b'\x02#!SILK_V3')


@pytest.mark.asyncio
async def test_convert_to_silk_runs_at_most_two_conversions(monkeypatch: pytest.MonkeyPatch) -> None:
    silk.shutdown_pool()
    lock = threading.Lock()
    barrier = threading.Barrier(2)
    active = 0
    peak = 0

    def fake_convert(data: bytes, rate: int) -> bytes:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            barrier.wait(timeout=2)
            return b'\x02#!SILK_V3' + data[-1:]
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(silk, 'audio_to_silk', fake_convert)
    inputs = [_wav_bytes() + bytes([index]) for index in range(6)]

    try:
        results = await asyncio.gather(*(silk.convert_to_silk(data) for data in inputs))
    finally:
        silk.shutdown_pool()

    assert all(silk.is_silk(result) for result in results)
    assert peak == 2
