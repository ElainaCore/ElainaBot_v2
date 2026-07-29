"""HTTP 服务器管理 — aiohttp 启动/关闭/Web面板挂载"""

import asyncio
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

    async def start(self, bind_timeout: float = 30, retry_interval: float = 2):
        """启动 HTTP 服务器 (支持 IPv4/IPv6, host 可为字符串或列表)

        端口被占用时在 bind_timeout 内重试, 覆盖重启时旧进程/子进程尚未释放端口的窗口。
        """
        host = cfg.get('settings', 'server.host', '0.0.0.0')
        port = cfg.get('settings', 'server.port', 5200)

        self._runner = AppRunner(self.app)
        await self._runner.setup()

        hosts = host if isinstance(host, list) else [host]
        deadline = asyncio.get_running_loop().time() + bind_timeout
        pending = list(hosts)
        while pending:
            failed: list = []
            for h in pending:
                try:
                    site = TCPSite(self._runner, h, port, reuse_address=True)
                    await site.start()
                    self._sites.append(site)
                    log.info(f'HTTP 服务器已启动: http://{"[" + h + "]" if ":" in str(h) else h}:{port}')
                except OSError as e:
                    log.warning(f'绑定 {h}:{port} 失败: {e}')
                    failed.append(h)
            pending = failed
            if not pending or asyncio.get_running_loop().time() >= deadline:
                break
            log.info(f'等待端口释放, {retry_interval}s 后重试绑定: {pending}:{port}')
            await asyncio.sleep(retry_interval)

        if not self._sites:
            await self._runner.cleanup()
            self._runner = None
            raise RuntimeError(f'无法绑定任何地址 ({hosts}:{port})')

        # 兼容旧属性
        self._site = self._sites[0]

    async def stop(self, timeout: float = 5):
        """关闭 HTTP 服务器: 先断开面板长连接, 再限时 cleanup"""
        try:
            from web.ws import get_broadcast

            get_broadcast().shutdown()
        except Exception as e:
            log.debug(f'关闭面板长连接失败: {e}')

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
