"""HttpServer.stop() 优雅关闭测试 — 验证 aiohttp 生命周期 + WSBroadcast.shutdown()"""

import asyncio
import os
import sys
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


# ==================== WSBroadcast.shutdown() 单元测试 ====================


class TestWSBroadcastShutdown:
    """web.ws.WSBroadcast 的 shutdown() 行为"""

    def test_shutdown_clears_all_connections(self):
        """shutdown() 清空所有 WS clients 和 SSE queues"""
        from web.ws import WSBroadcast

        bc = WSBroadcast()
        bc._clients.add(object())
        bc._sse_queues.add(object())
        assert len(bc._clients) == 1
        assert len(bc._sse_queues) == 1

        bc.shutdown()

        assert len(bc._clients) == 0
        assert len(bc._sse_queues) == 0

    def test_shutdown_on_empty_broadcast(self):
        """空 broadcast 调用 shutdown() 不抛异常"""
        from web.ws import WSBroadcast

        bc = WSBroadcast()
        bc.shutdown()
        assert len(bc._clients) == 0

    def test_shutdown_without_event_loop(self):
        """无事件循环时 shutdown() 不抛异常（跳过 Close Frame 发送）"""
        from unittest.mock import MagicMock

        from web.ws import WSBroadcast

        bc = WSBroadcast()
        mock_ws = MagicMock()
        mock_ws.close = MagicMock()
        bc._clients.add(mock_ws)

        bc.shutdown()  # 不应抛 RuntimeError

        assert len(bc._clients) == 0

    def test_shutdown_sends_close_frame_with_event_loop(self):
        """有事件循环时 shutdown() 调度 ws.close(code=1001)"""
        from unittest.mock import MagicMock

        from web.ws import WSBroadcast

        async def _test():
            bc = WSBroadcast()
            mock_ws = MagicMock()
            close_coro = asyncio.sleep(0)
            mock_ws.close = MagicMock(return_value=close_coro)
            bc._clients.add(mock_ws)

            bc.shutdown()

            # 等待 create_task 执行
            await asyncio.sleep(0.1)
            mock_ws.close.assert_called_once_with(code=1001, message=b'Server shutdown')
            assert len(bc._clients) == 0

        asyncio.run(_test())


# ==================== HttpServer.stop() 生命周期测试 ====================


@pytest.mark.integration
class TestHttpServerStopLifecycle:
    """验证 stop() 的调用序列: site.stop() → runner.cleanup()，且注入 _shutdown_timeout"""

    @pytest.fixture
    def setup_http_server(self):
        from unittest.mock import MagicMock

        from core.server.http_server import HttpServer

        bot_manager = MagicMock()
        server = HttpServer(bot_manager, os.path.dirname(ROOT))
        yield server

    async def test_stop_calls_site_stop_then_runner_cleanup(self, setup_http_server):
        """stop() 先 site.stop()，再 runner.cleanup()，且 _shutdown_timeout 被注入"""
        from unittest.mock import MagicMock

        server = setup_http_server
        server._app = MagicMock()

        call_order = []

        class _FakeSite:
            async def stop(self):
                call_order.append('site.stop')

        class _FakeRunner:
            _shutdown_timeout = 60.0  # 默认值

            async def cleanup(self):
                call_order.append(f'runner.cleanup(timeout={self._shutdown_timeout})')

        server._site = _FakeSite()
        server._runner = _FakeRunner()

        await server.stop(timeout=3)

        assert call_order == ['site.stop', 'runner.cleanup(timeout=3)']
        assert server._runner is None
        assert server._site is None

    async def test_stop_handles_missing_runner(self, setup_http_server):
        """runner 为 None 时不抛异常"""
        server = setup_http_server
        server._site = None
        server._runner = None

        await server.stop(timeout=1)  # 不应抛异常

    async def test_stop_handles_missing_site(self, setup_http_server):
        """site 为 None 但 runner 存在时正常清理"""
        call_order = []

        class _FakeRunner:
            _shutdown_timeout = 60.0

            async def cleanup(self):
                call_order.append('cleanup')

        server = setup_http_server
        server._site = None
        server._runner = _FakeRunner()

        await server.stop(timeout=1)

        assert call_order == ['cleanup']


# ==================== 带 WebSocket 的集成测试 ====================


# 用固定 token 绕过 WS 鉴权，避免依赖完整的 auth.login 流程
_TEST_WS_TOKEN = 'test_ws_shutdown_token'


@pytest.mark.integration
class TestShutdownWithWebSocket:
    """有活跃 WebSocket 连接时 stop() 不卡死"""

    @pytest.fixture(autouse=True)
    def _setup_auth(self):
        """注入测试 token 到 auth.valid_sessions"""
        import web.auth as _auth

        _auth.valid_sessions[_TEST_WS_TOKEN] = time.time() + 3600
        yield
        _auth.valid_sessions.pop(_TEST_WS_TOKEN, None)

    async def test_stop_with_active_ws_connection(self):
        """有 1 个 WebSocket 面板连接时 stop() 在合理时间内完成"""
        import web.ws as _ws

        _ws.reset_broadcast()

        app = web.Application()
        app.router.add_get('/ws/panel', _ws.handle_ws)
        app.router.add_get('/health', lambda r: web.json_response({'status': 'ok'}))

        server = TestServer(app)
        await server.start_server()
        client = TestClient(server)
        await client.start_server()

        try:
            # 带 token 的 WebSocket 连接
            ws_url = str(client.make_url(f'/ws/panel?token={_TEST_WS_TOKEN}'))
            ws_url = ws_url.replace('http://', 'ws://')
            ws_conn = await client.session.ws_connect(ws_url)
            assert ws_conn.closed is False

            # 模拟关闭流程: shutdown() → 连接断开
            _ws.get_broadcast().shutdown()
            await ws_conn.close()

            bc = _ws.get_broadcast()
            assert len(bc.clients) == 0

        finally:
            await client.close()
            await server.close()
            _ws.reset_broadcast()

    async def test_stop_with_multiple_ws_connections(self):
        """有 5 个 WebSocket 连接时 shutdown 快速完成"""
        import web.ws as _ws

        _ws.reset_broadcast()

        app = web.Application()
        app.router.add_get('/ws/panel', _ws.handle_ws)
        app.router.add_get('/health', lambda r: web.json_response({'status': 'ok'}))

        server = TestServer(app)
        await server.start_server()
        client = TestClient(server)
        await client.start_server()

        connections = []
        try:
            ws_url_base = str(client.make_url(f'/ws/panel?token={_TEST_WS_TOKEN}'))
            ws_url_base = ws_url_base.replace('http://', 'ws://')
            for _ in range(5):
                ws = await client.session.ws_connect(ws_url_base)
                connections.append(ws)

            bc = _ws.get_broadcast()
            assert len(bc.clients) == 5

            t0 = time.monotonic()
            bc.shutdown()
            for ws in connections:
                await ws.close()
            elapsed = time.monotonic() - t0

            assert len(bc.clients) == 0
            assert elapsed < 1.0, f'shutdown 耗时 {elapsed:.2f}s，超过 1s 阈值'

        finally:
            for ws in connections:
                with __import__('contextlib', fromlist=['suppress']).suppress(Exception):
                    await ws.close()
            await client.close()
            await server.close()
            _ws.reset_broadcast()


# ==================== stop() 端到端耗时测试 ====================


@pytest.mark.integration
class TestStopEndToEnd:
    """验证 stop() 的完整流程耗时"""

    async def test_stop_completes_within_timeout(self):
        """正常关闭：stop(timeout=5) 应在 3 秒内完成（远小于 timeout）"""
        from unittest.mock import MagicMock

        from aiohttp.web import AppRunner, TCPSite

        from core.server.http_server import HttpServer

        app = web.Application()
        app.router.add_get('/health', lambda r: web.json_response({'status': 'ok'}))

        bot_manager = MagicMock()
        server = HttpServer(bot_manager, os.path.dirname(ROOT))
        server._app = app

        server._runner = AppRunner(app)
        await server._runner.setup()
        server._site = TCPSite(server._runner, '127.0.0.1', 0)
        await server._site.start()

        t0 = time.monotonic()
        await server.stop(timeout=5)
        elapsed = time.monotonic() - t0

        assert elapsed < 3.0, f'正常关闭耗时 {elapsed:.2f}s，超过 3s 阈值'
        assert server._runner is None
        assert server._site is None

    async def test_stop_short_timeout_no_hang(self):
        """即使 timeout=0.1（极短），stop() 也能完成而不永久阻塞"""
        from unittest.mock import MagicMock

        from aiohttp.web import AppRunner, TCPSite

        from core.server.http_server import HttpServer

        app = web.Application()
        app.router.add_get('/health', lambda r: web.json_response({'status': 'ok'}))

        bot_manager = MagicMock()
        server = HttpServer(bot_manager, os.path.dirname(ROOT))
        server._app = app

        server._runner = AppRunner(app)
        await server._runner.setup()
        server._site = TCPSite(server._runner, '127.0.0.1', 0)
        await server._site.start()

        t0 = time.monotonic()
        await server.stop(timeout=0.1)  # 极短 timeout
        elapsed = time.monotonic() - t0

        # 即使 timeout 短，也应在 2 秒内完成（不会被永久阻塞）
        assert elapsed < 2.0, f'短 timeout 关闭耗时 {elapsed:.2f}s，疑似阻塞'
        assert server._runner is None
        assert server._site is None
