import asyncio
import errno
import os
from types import SimpleNamespace

import psutil

from core.server import http_server


class FakeProcess:
    def __init__(self, pid, connections=()):
        self.pid = pid
        self.connections = connections
        self.terminated = False
        self.killed = False

    def name(self):
        return 'python'

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def net_connections(self, kind):
        assert kind == 'inet'
        return self.connections


def test_is_address_in_use_supports_posix_and_windows():
    assert http_server._is_address_in_use(OSError(errno.EADDRINUSE, 'in use'))

    windows_error = OSError('in use')
    windows_error.winerror = 10048
    assert http_server._is_address_in_use(windows_error)

    assert not http_server._is_address_in_use(OSError(errno.EACCES, 'denied'))


def test_kill_port_listeners_excludes_current_process(monkeypatch):
    listen_5200 = SimpleNamespace(status=psutil.CONN_LISTEN, laddr=SimpleNamespace(port=5200))
    processes = [
        FakeProcess(101, [listen_5200]),
        FakeProcess(202, [listen_5200]),
        FakeProcess(303, [SimpleNamespace(status='ESTABLISHED', laddr=SimpleNamespace(port=5200))]),
        FakeProcess(404, [SimpleNamespace(status=psutil.CONN_LISTEN, laddr=SimpleNamespace(port=5300))]),
        FakeProcess(505, [listen_5200]),
    ]
    monkeypatch.setattr(http_server.os, 'getpid', lambda: 202)
    monkeypatch.setattr(http_server.psutil, 'process_iter', lambda: processes)

    assert http_server._kill_port_listeners(5200) == [101, 505]

    assert processes[0].killed and processes[4].killed
    assert not any(process.killed for process in processes[1:4])


def test_restart_recovery_terminates_listener_then_binds(monkeypatch):
    attempts = []
    terminated_ports = []
    sleep_intervals = []

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

    async def fake_sleep(interval):
        sleep_intervals.append(interval)

    monkeypatch.setattr(http_server.asyncio, 'sleep', fake_sleep)
    monkeypatch.setenv(http_server.RESTART_RECOVERY_ENV, '1')

    server = http_server.HttpServer(None, '')
    server._app = object()
    asyncio.run(server.start(bind_timeout=1))

    assert attempts == [('0.0.0.0', 5200)] * 3
    assert terminated_ports == [5200, 5200]
    assert sleep_intervals == [0.1, 0.1]
    assert http_server.RESTART_RECOVERY_ENV not in os.environ
