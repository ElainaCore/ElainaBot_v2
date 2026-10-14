"""按数据库文件借用/退役连接；元数据锁内不执行 SQLite 或等待使用锁。"""

import threading
from contextlib import contextmanager


class _Entry:
    __slots__ = ('writer', 'reader', 'write_lock', 'read_lock', 'users', 'retiring')

    def __init__(self):
        self.writer = self.reader = None
        self.write_lock = threading.Lock()
        self.read_lock = threading.Lock()
        self.users = 0
        self.retiring = False


class SQLiteConnectionPool:
    """读写连接分离；退役等待已借用者，并阻止新借用者取得旧连接。"""

    def __init__(self, open_writer, open_reader, check_path=None):
        self._open_writer = open_writer
        self._open_reader = open_reader
        self._check_path = check_path
        self._entries = {}
        self._condition = threading.Condition()
        self._closed = False

    @contextmanager
    def connection(self, path, log_type, *, read_only=False):
        with self._condition:
            while True:
                if self._closed:
                    raise RuntimeError('SQLite 日志服务已关闭')
                if self._check_path is not None:
                    self._check_path(path, log_type)
                entry = self._entries.get(path)
                if entry is None:
                    entry = self._entries[path] = _Entry()
                if not entry.retiring:
                    entry.users += 1
                    break
                self._condition.wait()
        try:
            lock = entry.read_lock if read_only else entry.write_lock
            with lock:
                if read_only:
                    if entry.reader is None:
                        # 首次读连接需要完整 schema；已建立的读连接无需等待写锁。
                        if entry.writer is None:
                            with entry.write_lock:
                                self._writer(entry, path, log_type)
                        entry.reader = self._open_reader(path, log_type)
                    conn = entry.reader
                else:
                    conn = self._writer(entry, path, log_type)
                yield conn
        finally:
            with self._condition:
                entry.users -= 1
                if entry.users == 0:
                    self._condition.notify_all()

    def _writer(self, entry, path, log_type):
        if entry.writer is None:
            entry.writer = self._open_writer(path, log_type)
        return entry.writer

    def paths(self):
        with self._condition:
            return tuple(self._entries)

    def retire(self, path):
        with self._condition:
            while True:
                entry = self._entries.get(path)
                if entry is None:
                    return
                if not entry.retiring:
                    entry.retiring = True
                    break
                self._condition.wait()
            while entry.users:
                self._condition.wait()
        failures = []
        try:
            # 此时没有借用者，关闭过程也不会占用其他文件的元数据锁。
            for conn in (entry.reader, entry.writer):
                if conn is not None:
                    try:
                        conn.close()
                    except Exception as exc:
                        failures.append(exc)
        finally:
            with self._condition:
                self._entries.pop(path, None)
                self._condition.notify_all()
        if failures:
            raise failures[0]

    def close(self):
        with self._condition:
            self._closed = True
            paths = tuple(self._entries)
            self._condition.notify_all()
        failures = []
        for path in paths:
            try:
                self.retire(path)
            except Exception as exc:
                failures.append(exc)
        if failures:
            raise failures[0]
