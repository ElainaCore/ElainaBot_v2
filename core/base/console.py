"""控制台输出捕获 — Tee stdout/stderr 到环形缓冲, 供 web 面板实时展示"""

import contextlib
import re
import sys
import threading
from datetime import datetime

MAX_LINES = 500

_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')
_LEVEL_RE = re.compile(r' - (DEBUG|INFO|WARNING|ERROR|CRITICAL)\s*-? ')

_lines: list[dict] = []
_lock = threading.Lock()
_callbacks: list = []
_installed = False
_tls = threading.local()
_last_level: dict[str, str] = {}


class _Tee:
    """写入原始流的同时按行捕获到缓冲"""

    def __init__(self, raw, stream_name):
        self._raw = raw
        self._name = stream_name
        self._buf = ''

    def write(self, s):
        with contextlib.suppress(Exception):
            self._raw.write(s)
        with contextlib.suppress(Exception):
            self._capture(s)
        return len(s)

    def _capture(self, s):
        if not isinstance(s, str):
            s = str(s)
        self._buf += s
        while '\n' in self._buf:
            line, self._buf = self._buf.split('\n', 1)
            _emit(self._name, line)

    def flush(self):
        with contextlib.suppress(Exception):
            self._raw.flush()

    def isatty(self):
        try:
            return self._raw.isatty()
        except Exception:
            return False

    def fileno(self):
        return self._raw.fileno()

    def writable(self):
        return True

    @property
    def encoding(self):
        return self._raw.encoding

    @property
    def errors(self):
        return self._raw.errors


def _emit(stream, line):
    if _tls.__dict__.get('busy'):
        return
    text = _ANSI_RE.sub('', line).rstrip('\r')
    if not text.strip():
        return
    m = _LEVEL_RE.search(text)
    if m:
        level = m.group(1)
        _last_level[stream] = level
    else:
        # 无等级标记的续行 (如 traceback) 继承同流上一行的等级
        level = _last_level.get(stream, 'ERROR' if stream == 'stderr' else 'INFO')
    entry = {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'stream': stream,
        'level': level,
        'content': text,
    }
    with _lock:
        _lines.append(entry)
        if len(_lines) > MAX_LINES:
            del _lines[: len(_lines) - MAX_LINES]
    _tls.busy = True
    try:
        for cb in list(_callbacks):
            with contextlib.suppress(Exception):
                cb(entry)
    finally:
        _tls.busy = False


def install():
    """接管 stdout/stderr (幂等)"""
    global _installed
    if _installed:
        return
    _installed = True
    if not isinstance(sys.stdout, _Tee):
        sys.stdout = _Tee(sys.stdout, 'stdout')
    if not isinstance(sys.stderr, _Tee):
        sys.stderr = _Tee(sys.stderr, 'stderr')


def get_lines() -> list[dict]:
    """获取缓冲中的控制台日志"""
    with _lock:
        return list(_lines)


def on_line(callback):
    """注册新行回调 (在写入线程同步调用)"""
    if callback not in _callbacks:
        _callbacks.append(callback)
