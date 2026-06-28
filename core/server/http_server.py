"""HTTP 服务器管理 — aiohttp 启动/关闭/Web面板挂载"""

import logging
from typing import cast

from aiohttp import web
from aiohttp.web import AppRunner, TCPSite

from core.base.config import cfg

log = logging.getLogger('ElainaBot.http_server')


class HttpServer:
    """管理 aiohttp HTTP 服务器生命周期"""

    def __init__(self, bot_manager, base_dir: str):
        self._bot_manager = bot_manager
        self._base_dir = base_dir
        self._app: web.Application | None = None
        self._runner: AppRunner | None = None
        self._site: TCPSite | None = None
        self._sites: list[TCPSite] = []

    @property
    def app(self) -> web.Application:
        return cast(web.Application, self._app)

    def init_app(self) -> web.Application:
        """初始化 aiohttp Application (仅注册核心路由, Web 面板需在 bot_registry 就绪后调用 mount_web_panel)"""
        self._app = web.Application(client_max_size=20 * 1024 * 1024)
        assert self._app is not None
        self._app.router.add_post('/', self._bot_manager._handle_webhook)
        self._app.router.add_get('/health', self._bot_manager._handle_health)
        return self._app

    def mount_web_panel(self):
        """挂载 Web 面板 (路由 + 静态资源 + 鉴权), 需在 bot_registry 创建后调用以正确绑定 sender 回调"""
        try:
            from web.setup import setup_web
            setup_web(self._app, self._bot_manager, self._base_dir)
        except Exception as e:
            log.warning(f'Web 面板加载失败: {e}')

    async def start(self):
        """启动 HTTP 服务器 (支持 IPv4/IPv6, host 可为字符串或列表)"""
        host = cfg.get('settings', 'server.host', '0.0.0.0')
        port = cfg.get('settings', 'server.port', 5200)

        self._runner = AppRunner(self._app)
        await self._runner.setup()

        hosts = host if isinstance(host, list) else [host]
        for h in hosts:
            try:
                site = TCPSite(self._runner, h, port)
                await site.start()
                self._sites.append(site)
                log.info(f'HTTP 服务器已启动: http://{"[" + h + "]" if ":" in str(h) else h}:{port}')
            except OSError as e:
                log.warning(f'绑定 {h}:{port} 失败: {e}')

        if not self._sites:
            raise RuntimeError(f'无法绑定任何地址 ({hosts}:{port})')

        # 兼容旧属性
        self._site = self._sites[0]

    async def stop(self, timeout: float = 5):
        """关闭 HTTP 服务器

        根因修复说明:
        - aiohttp 3.13 的 cleanup() 内部已包含完整 shutdown 序列
          (site.stop → on_shutdown 信号 → server.shutdown(timeout) → 释放资源)
        - 原先卡死是因为: _shutdown_timeout 默认 60s + 面板 WS/SSE 长连接未主动断开
        - 修复: ①提前关闭面板长连接 ②将 timeout 注入 runner._shutdown_timeout
        """
        try:
            from web.ws import get_broadcast

            get_broadcast().shutdown()
        except Exception:
            pass

        if self._sites:
            for site in self._sites:
                await site.stop()
            self._sites.clear()
        elif self._site:
            await self._site.stop()
        if self._runner:
            self._runner._shutdown_timeout = timeout
            await self._runner.cleanup()

        self._runner = None
        self._site = None
        log.info('HTTP 服务器已关闭')
