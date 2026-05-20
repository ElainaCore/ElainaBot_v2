"""Application — 顶层编排, 组合所有子系统"""

import asyncio
import contextlib
import logging
import os

from core.base.config import cfg
from core.base.logger import SYSTEM, get_logger
from core.base.logger import setup as setup_logger
from core.bot.event import EventHandlerMixin
from core.bot.registry import BotRegistry
from core.module.hook import HookManager
from core.module.manager import ModuleManager
from core.plugin.manager import PluginManager
from core.services.config_watcher import ConfigWatcherService
from core.services.media_cleanup import MediaCleanupService
from core.services.scheduler import RestartScheduler
from core.storage.dau import DAUService
from core.storage.log import SharedLogService

log = get_logger(SYSTEM, '启动器')

# 全局应用实例 (由 start() 设置)
_app: 'Application | None' = None


def get_app() -> 'Application | None':
    """获取当前运行的 Application 实例 (线程安全)"""
    return _app


class Application(EventHandlerMixin):
    """ElainaBot 应用入口 — 组合 BotRegistry, Plugin/Module Manager, 服务"""

    def __init__(self):
        super().__init__()
        self._base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        # 核心组件
        self._bot_registry = None
        self._plugin_manager = None
        self._module_manager = None
        self._hook_manager = HookManager()
        self._shared_log = None
        self._dau_service = None
        self._http_server = None

        # 服务
        self._config_watcher = None
        self._media_cleanup = None
        self._restart_scheduler = None

        # 状态
        self._web_log_cb = None
        self._log_base = ''
        self._media_dir = ''
        self._stop_event = None
        self._restart_requested = False

        # 日志回调 (替代模块级全局列表)
        self._error_callbacks: list = []
        self._framework_callbacks: list = []

        self._init_event_state()

    # ===== 属性 (向后兼容 manager.py) =====

    @property
    def dau_service(self):
        return self._dau_service

    @property
    def module_manager(self):
        return self._module_manager

    @property
    def web_app(self):
        """aiohttp.web.Application 实例"""
        return self._http_server.app if self._http_server else None

    @property
    def _app(self):
        """向后兼容: 老插件用 _bot_manager_ref._app 访问 aiohttp web.Application"""
        return self.web_app

    @property
    def router(self):
        """向后兼容: _bot_manager_ref._app.router"""
        app = self.web_app
        return app.router if app else None

    @property
    def plugin_manager(self):
        return self._plugin_manager

    @property
    def _bots(self):
        """向后兼容 EventHandlerMixin"""
        return self._bot_registry.bots if self._bot_registry else {}

    def get_bot(self, appid):
        return self._bot_registry.get(appid) if self._bot_registry else None

    def _path(self, *parts):
        return os.path.join(self._base_dir, *parts)

    # ===== 启动 =====

    async def start(self):
        global _app
        _app = self

        try:
            from core.bot import manager as _bot_manager_module
            _bot_manager_module._bot_manager_ref = self
        except Exception:
            pass

        # 1) 初始化配置
        cfg.init(self._path('config'))

        fw_name = cfg.get('settings', 'web.framework_name', 'ElainaBot')
        log_level_str = cfg.get('settings', 'logging.level', 'INFO').upper()
        log_level = getattr(logging, log_level_str, logging.INFO)
        setup_logger(framework_name=fw_name, level=log_level)
        log.info(f'{"=" * 5} {fw_name} 启动中 {"=" * 5}')
        log.info(f'日志等级: {log_level_str}')

        bot_configs = cfg.get_bot_configs()
        valid_bots = [b for b in bot_configs if b.get('appid') and b.get('secret')]
        if not valid_bots:
            log.warning('未配置有效的机器人, 仅启动 Web 面板')
            # 输出诊断信息: 哪个 bot 配置缺失了哪些字段
            for b in bot_configs:
                missing = []
                if not b.get('appid'):
                    missing.append('appid')
                if not b.get('secret'):
                    missing.append('secret (请通过 Web 面板配置)')
                if missing:
                    log.warning(f'  bot 配置不完整: {b.get("appid", "?")} — 缺失 {", ".join(missing)}')

        # 2) HTTP 应用
        from core.server.http_server import HttpServer

        self._http_server = HttpServer(self, self._base_dir)
        self._http_server.init_app()

        # 3) Module 管理器
        self._module_manager = ModuleManager(self._path('modules'), self._hook_manager)
        self._module_manager.discover()
        await self._module_manager.start_enabled()

        # 4) Plugin 管理器
        self._plugin_manager = PluginManager(self._path('plugins'))
        await self._plugin_manager.load_all()
        self._plugin_manager.start_watcher()

        # 5) 日志服务
        log_base = self._path('data', cfg.get('settings', 'logging.dir', 'log'))
        log_cfg = cfg.get('settings', 'logging') or {}
        self._shared_log = SharedLogService(
            base_dir=log_base,
            wal_mode=log_cfg.get('wal_mode', True),
            insert_interval=log_cfg.get('insert_interval', 2),
            retention_days=log_cfg.get('retention_days', 5),
        )
        await self._shared_log.start()

        self._log_base = log_base
        self._media_dir = self._path('data', 'media')
        os.makedirs(self._media_dir, exist_ok=True)

        # 6) Bot 注册表
        self._bot_registry = BotRegistry(
            log_base=log_base,
            on_event=self._on_event,
            push_web_log=self._push_web_log,
            media_dir=self._media_dir,
        )
        if valid_bots:
            await self._bot_registry.start_all()

        cfg.on_change('bot', self._bot_registry.on_config_change)

        # 7) DAU 统计
        self._dau_service = DAUService(log_base)
        await self._dau_service.start()

        # 8) 挂载 Web 面板 (bot_registry 就绪后, 才能正确绑定 sender 回调)
        self._http_server.mount_web_panel()

        # 9) HTTP 服务器
        await self._http_server.start()

        # 10) 后台服务
        self._config_watcher = ConfigWatcherService(interval=5.0)
        self._media_cleanup = MediaCleanupService(media_dir=self._media_dir, max_age_days=3, interval=3600)
        self._restart_scheduler = RestartScheduler(on_restart=self._trigger_restart)

        for svc in (self._config_watcher, self._media_cleanup, self._restart_scheduler):
            svc.start()

        msg = f'✅ 启动完成: {len(self._bot_registry)} 个机器人, {self._plugin_manager.handler_count} 个命令处理器'
        log.info(msg)
        self._push_web_log('framework', {'source': '启动器', 'content': msg})

        self._stop_event = asyncio.Event()
        self._install_signal_handlers()
        try:
            await self._stop_event.wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await self.shutdown()
        return self._restart_requested

    def _install_signal_handlers(self):
        """注册 SIGTERM/SIGINT 信号处理器, 保证宝塔/systemd/Docker 关闭时优雅退出"""
        import signal
        loop = asyncio.get_running_loop()

        def _handle(signame):
            log.info(f'收到 {signame} 信号, 触发优雅关闭')
            if self._stop_event and not self._stop_event.is_set():
                self._stop_event.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                # POSIX: async-safe 注册
                loop.add_signal_handler(sig, _handle, sig.name)
            except (NotImplementedError, RuntimeError):
                # Windows 不支持 add_signal_handler, 用 signal.signal 兜底
                with contextlib.suppress(ValueError, OSError):
                    signal.signal(sig, lambda s, f: loop.call_soon_threadsafe(_handle, signal.Signals(s).name))

    # ===== 关闭 =====

    async def shutdown(self):
        log.info('正在关闭...')

        if self._plugin_manager:
            self._plugin_manager.stop_watcher()

        # 停止后台服务
        for svc in (self._config_watcher, self._media_cleanup, self._restart_scheduler):
            if svc:
                svc.stop()

        # 按依赖顺序关闭
        cleanup = [
            self._dau_service and self._dau_service.stop(),
            self._bot_registry and self._bot_registry.shutdown(),
            self._module_manager and self._module_manager.shutdown(),
            self._shared_log and self._shared_log.shutdown(),
        ]
        for coro in cleanup:
            if coro:
                try:
                    await asyncio.wait_for(coro, timeout=10)
                except TimeoutError:
                    log.warning(f'关闭超时(10s), 跳过: {coro}')

        if self._http_server:
            await self._http_server.stop(timeout=5)

        log.info('已关闭')

    # ===== Webhook / Health (桥接到 manager 兼容) =====

    async def _handle_webhook(self, request):
        """桥接: 委托给 WebhookHandler"""
        from core.server.webhook import WebhookHandler

        handler = WebhookHandler(
            bot_registry=self._bot_registry,
            on_event=self._on_event,
        )
        return await handler.handle(request)

    async def _handle_health(self, request):
        from aiohttp import web

        return web.json_response(
            {
                'status': 'ok',
                'bots': len(self._bot_registry) if self._bot_registry else 0,
                'plugins': self._plugin_manager.handler_count if self._plugin_manager else 0,
            }
        )

    # ===== 辅助 =====

    def _push_web_log(self, log_type: str, entry: dict):
        if self._web_log_cb:
            with contextlib.suppress(Exception):
                self._web_log_cb(log_type, entry)

    def _trigger_restart(self):
        self._push_web_log('framework', {'content': '⏰ 定时重启触发'})
        self._restart_requested = True
        if self._stop_event:
            self._stop_event.set()

    @property
    def bot_registry(self):
        return self._bot_registry

    @property
    def hook_manager(self):
        return self._hook_manager

    # ===== 日志回调 (替代 logger.py 模块级全局列表) =====

    def on_error(self, callback):
        """注册错误回调"""
        self._error_callbacks.append(callback)

    def on_framework_log(self, callback):
        """注册框架日志回调"""
        self._framework_callbacks.append(callback)

    def _fire_error_callbacks(self, data):
        for cb in self._error_callbacks:
            with contextlib.suppress(Exception):
                cb(data)

    def _fire_framework_callbacks(self, data):
        for cb in self._framework_callbacks:
            with contextlib.suppress(Exception):
                cb(data)
