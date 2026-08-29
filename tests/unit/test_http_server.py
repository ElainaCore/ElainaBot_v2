import asyncio
import errno
import os
from types import SimpleNamespace

import psutil

from core.server import http_server


class FakeProcess:
    def __init__(self, pid):
        self.pid = pid
        self.terminated = False
        self.killed = False

    def name(self):
        return 'python'

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


def test_is_address_in_use_supports_posix_and_windows():
    assert http_server._is_address_in_use(OSError(errno.EADDRINUSE, 'in use'))

    windows_error = OSError('in use')
    windows_error.winerror = 10048
    assert http_server._is_address_in_use(windows_error)

    assert not http_server._is_address_in_use(OSError(errno.EACCES, 'denied'))


def test_kill_port_listeners_excludes_current_process(monkeypatch):
    processes = {101: FakeProcess(101), 202: FakeProcess(202)}
    connections = [
        SimpleNamespace(pid=101, status=psutil.CONN_LISTEN, laddr=SimpleNamespace(port=5200)),
        SimpleNamespace(pid=202, status=psutil.CONN_LISTEN, laddr=SimpleNamespace(port=5200)),
        SimpleNamespace(pid=303, status='ESTABLISHED', laddr=SimpleNamespace(port=5200)),
        SimpleNamespace(pid=404, status=psutil.CONN_LISTEN, laddr=SimpleNamespace(port=5300)),
    ]
    monkeypatch.setattr(http_server.os, 'getpid', lambda: 202)
    monkeypatch.setattr(http_server.psutil, 'net_connections', lambda kind: connections)
    monkeypatch.setattr(http_server.psutil, 'Process', lambda pid: processes[pid])

    http_server._kill_port_listeners(5200)

    assert processes[101].killed
    assert not processes[202].killed


def test_restart_recovery_terminates_listener_then_binds(monkeypatch):
    attempts = []
    terminated_ports = []

    class FakeRunner:
        def __init__(self, app):
            self.app = app

        async def setup(self):
            pass

        async def cleanup(self):
            pass

    class FakeSite:
        def __init__(self, runner, host, port, reuse_address):
            self.host = host
            self.port = port

        async def start(self):
            attempts.append((self.host, self.port))
            if len(attempts) < 3:
                raise OSError(errno.EADDRINUSE, 'address already in use')

    monkeypatch.setattr(http_server.cfg, 'get', lambda section, key, default: {'server.host': '0.0.0.0', 'server.port': 5200}[key])
    monkeypatch.setattr(http_server, 'AppRunner', FakeRunner)
    monkeypatch.setattr(http_server, 'TCPSite', FakeSite)
    monkeypatch.setattr(
        http_server,
        '_kill_port_listeners',
        lambda port: terminated_ports.append(port),
    )
    monkeypatch.setenv(http_server.RESTART_RECOVERY_ENV, '1')

    server = http_server.HttpServer(None, '')
    server._app = object()
    asyncio.run(server.start(bind_timeout=1, retry_interval=0))

    assert attempts == [('0.0.0.0', 5200)] * 3
    assert terminated_ports == [5200, 5200]
    assert http_server.RESTART_RECOVERY_ENV not in os.environ
