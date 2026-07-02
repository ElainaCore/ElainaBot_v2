"""OneBot 适配器外观 — Facade 模式

协调子系统: 配置 → Hook 适配 → 事件分发 → WS 服务 → Action 路由。

职责:
  1. 生命周期: start / stop 协调所有子系统的初始化和销毁
  2. 配置管理: 加载和验证模块配置
  3. 事件分发: 监听 on_raw_event / after_send, 转换为 OneBot 格式推送
  4. Action 路由: 委托 ActionRegistry 处理外部 action 请求
  5. 状态管理: 维护 senders / log_services / msg_id_cache / qq_map 等运行时状态

不负责:
  - Action 具体逻辑 → actions/ (Command 模式)
  - 消息解析/转换 → payload/ (Strategy 模式)
  - ID 映射 → lib/id_mapper.py
  - WebSocket 通信 → lib/ws_server.py
  - 事件格式转换 → lib/event_converter.py
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from modules.onebot_adapter.action_context import ActionContext
from modules.onebot_adapter.action_registry import ActionRegistry
from modules.onebot_adapter.config import OneBotConfig
from modules.onebot_adapter.hook_adapter import HookAdapter
from modules.onebot_adapter.lib.event_converter import (
    convert_lifecycle_event,
    convert_message_event,
)
from modules.onebot_adapter.lib.http_server import OneBotHTTPServer
from modules.onebot_adapter.lib.http_webhook import OneBotHTTPWebhook
from modules.onebot_adapter.lib.id_mapper import IDMapper
from modules.onebot_adapter.lib.ws_server import OneBotWSServer

if TYPE_CHECKING:
    from core.bot.manager import BotManager
    from core.message.event import Event
    from core.module.manager import ModuleContext


class OneBotAdapter:
    """OneBot 适配器外观 (Facade 模式)

    职责:
      1. 生命周期: start / stop 协调所有子系统的初始化和销毁
      2. 配置管理: 加载和验证模块配置
      3. 事件分发: 监听 on_raw_event / after_send, 转换为 OneBot 格式推送
      4. Action 路由: 委托 ActionRegistry 处理外部 action 请求
      5. 状态管理: 维护 senders / log_services / msg_id_cache / qq_map 等运行时状态

    不负责:
      - Action 具体逻辑 → actions/ (Command 模式)
      - 消息解析/转换 → payload/ (Strategy 模式)
      - ID 映射 → lib/id_mapper.py
      - WebSocket 通信 → lib/ws_server.py
      - 事件格式转换 → lib/event_converter.py
    """

    # --- instance variables (declared for type checker) ---
    _mctx: ModuleContext
    log: Any
    cfg: OneBotConfig
    id_mapper: IDMapper | None
    ws_server: OneBotWSServer | None
    http_server: OneBotHTTPServer | None
    http_webhook: OneBotHTTPWebhook | None

    def __init__(self, module_ctx: ModuleContext) -> None:
        self._mctx = module_ctx  # ModuleContext
        self.log = module_ctx.log
        self.cfg: OneBotConfig = OneBotConfig()

        # 基础设施
        self.id_mapper: IDMapper | None = None
        self.ws_server: OneBotWSServer | None = None
        self.http_server: OneBotHTTPServer | None = None
        self.http_webhook: OneBotHTTPWebhook | None = None
        self._hook_adapter = HookAdapter(self.log)

        # Action 上下文 (DI 容器)
        self._actx = ActionContext(log=self.log)

        # Action 路由 (Command 模式)
        self._action_registry: ActionRegistry | None = None

        # 运行时状态
        self._bm: BotManager | None = None  # BotManager 引用

    # ==================== 生命周期 ====================

    async def start(self) -> None:
        """启动适配器: 配置 → ID 映射 → Hook → 网络连接 → Action 路由"""

        # 1. 加载配置 (含旧版扁平配置自动迁移)
        raw_config = self._mctx.read_config()
        migrated = OneBotConfig.migrate_legacy(raw_config)
        if migrated is not None:
            self._mctx.save_config(migrated, comments=OneBotConfig.comments())
            raw_config = migrated
            self.log.info('旧版配置已迁移为 connections 列表')
        else:
            raw_config = self._mctx.ensure_config(OneBotConfig.defaults(), comments=OneBotConfig.comments())
        self.cfg = OneBotConfig.from_dict(raw_config)
        self.log.info(f'配置: {len(self.cfg.connections)} 个连接')

        # 2. 初始化 ID 映射器
        db_path = self._mctx.get_data_path('id_mapping.db')
        self.id_mapper = IDMapper(db_path)
        await self.id_mapper.open()
        self.log.info('ID 映射数据库已加载')

        # 3. 构建 appid → robot_qq 映射
        self._build_qq_map()

        # 4. 初始化 ActionContext (注入基础依赖)
        self._actx.id_mapper = self.id_mapper
        # qq_map 已由 _build_qq_map() 填充; bm 在 _install_hooks() 中设置

        # 5. 安装 Hook (事件 → OneBot 推送)
        self._install_hooks()

        # 6. 构建 Action 注册表 (Command 模式)
        self._action_registry = ActionRegistry.create_default(self._actx)

        # 7. 启动网络连接 (WS 正/反 + HTTP 正/反)
        await self._start_network()

    async def stop(self) -> None:
        """停止适配器: Hook → 网络 → ID 映射"""
        # 1. 卸载 Hook
        self._hook_adapter.uninstall()

        # 2. 停止网络连接
        await self._stop_network()

        # 3. 关闭 ID 映射
        if self.id_mapper:
            await self.id_mapper.close()

        self.log.info('OneBot 适配器已停止')

    async def apply_config(self, raw_config: dict) -> None:
        """保存并应用新配置 (Web 面板调用): 重启网络连接层"""
        self.cfg = OneBotConfig.from_dict(raw_config)
        self._mctx.save_config(
            {
                'connections': self.cfg.connections,
                'heartbeat_interval': self.cfg.heartbeat_interval,
                'debug': self.cfg.debug,
            },
            comments=OneBotConfig.comments(),
        )
        await self._stop_network()
        await self._start_network()
        self.log.info('网络配置已重新应用')

    def network_status(self) -> dict:
        """汇总各连接状态 (供 Web 面板展示)"""
        ws_st = self.ws_server.status() if self.ws_server else {'forward': {}, 'reverse': {}}
        return {
            'ws_server': ws_st.get('forward', {}),
            'ws_reverse': ws_st.get('reverse', {}),
            'http_server': self.http_server.status() if self.http_server else {},
            'http_webhook': self.http_webhook.status() if self.http_webhook else {},
            'port': self._get_framework_port(),
        }

    # ==================== 配置辅助 ====================

    def _build_qq_map(self) -> None:
        """从框架配置构建 appid → robot_qq 映射"""
        try:
            from core.base.config import cfg as _fw_cfg

            for bc in _fw_cfg.get_bot_configs() or []:
                aid = str(bc.get('appid', ''))
                rq = bc.get('robot_qq', '')
                if aid and rq:
                    self._actx.qq_map[aid] = int(rq)
        except Exception:
            pass
        for aid, qq in self._actx.qq_map.items():
            self.log.info(f'QQ 映射: appid={aid} → robot_qq={qq}')
        self._actx.default_qq = next(iter(self._actx.qq_map.values()), 0)

    # ==================== Hook 安装 ====================

    def _install_hooks(self) -> None:
        """注册框架 Hook 并安装 monkey-patch 触发点"""

        # 注册 on_raw_event 监听器 (事件 → OneBot 推送)
        self._mctx.register_hook('on_raw_event', self._on_raw_event, priority=10)

        # 注册 after_send 监听器 (追踪机器人自身发送, 暂留扩展)
        self._mctx.register_hook('after_send', self._on_after_send, priority=100)

        # 通过 HookAdapter 补全 on_raw_event 触发点
        self._bm = self._get_bot_manager()
        if self._bm:
            self._hook_adapter.install(self._bm)
            self._actx.bm = self._bm

    # ==================== 框架资源访问 (Facade 内部) ====================

    @staticmethod
    def _get_bot_manager() -> BotManager | None:
        """获取 BotManager 实例"""
        try:
            from core.application import get_app

            return get_app()
        except Exception:
            return None

    @staticmethod
    def _get_framework_app():
        """获取框架的 aiohttp Application"""
        try:
            from core.application import get_app

            app = get_app()
            if app and app._http_server:
                return app._http_server._app
        except Exception:
            pass
        return None

    @staticmethod
    def _get_framework_port() -> int:
        """获取框架 HTTP 服务器端口"""
        try:
            from core.base.config import cfg

            return cfg.get('settings', 'server.port', 5001)
        except Exception:
            return 5001

    # ==================== 网络连接 (WS 正/反 + HTTP 正/反) ====================

    async def _start_network(self) -> None:
        """按 connections 配置启动所有网络连接"""
        port = self._get_framework_port()

        # 正向 WS (服务端)
        ws_forward_entries = [
            {
                'name': c['name'],
                'path': c['path'],
                'port': int(c.get('port', 0) or 0),
                'token': c['access_token'],
                'appid': c['appid'],
                'enable': True,
            }
            for c in self.cfg.by_type('ws_server')
        ]
        # 反向 WS (客户端)
        ws_reverse_entries = [
            {
                'name': c['name'],
                'url': c['url'],
                'appid': c['appid'],
                'token': c['access_token'],
                'reconnect_interval': c['reconnect_interval'],
            }
            for c in self.cfg.by_type('ws_reverse')
            if c['url'].strip()
        ]

        self.ws_server = OneBotWSServer(
            heartbeat_interval=self.cfg.heartbeat_interval,
            on_action=self._handle_action,
            default_qq=self._actx.default_qq,
            qq_map=self._actx.qq_map,
            log=self.log,
            forward_entries=ws_forward_entries,
            reverse_entries=ws_reverse_entries,
            debug=self.cfg.debug,
        )

        app = self._get_framework_app()
        attached_entries = [e for e in ws_forward_entries if e['port'] <= 0]
        if app:
            for path in self.ws_server.attach(app):
                self.log.info(f'正向 WS 已挂载: ws://0.0.0.0:{port}{path}')
        elif attached_entries:
            self.log.warning('无法获取框架 aiohttp app, 正向 WS 未挂载')

        await self.ws_server.start_standalone()

        await self.ws_server.start_reverse()
        if ws_reverse_entries:
            self.log.info(f'反向 WS 已启动: {len(ws_reverse_entries)} 个连接')

        self.ws_server.start_heartbeat()

        # 正向 HTTP (OneBot HTTP API)
        http_entries = [
            {'name': c['name'], 'path': c['path'], 'token': c['access_token'], 'appid': c['appid'], 'enable': True} for c in self.cfg.by_type('http_server')
        ]
        self.http_server = OneBotHTTPServer(
            entries=http_entries,
            on_action=self._handle_action,
            log=self.log,
            debug=self.cfg.debug,
        )
        if app:
            for path in self.http_server.attach(app):
                self.log.info(f'正向 HTTP 已挂载: http://0.0.0.0:{port}{path}')
        elif http_entries:
            self.log.warning('无法获取框架 aiohttp app, 正向 HTTP 未挂载')

        # 反向 HTTP (事件 POST 上报)
        webhook_entries = [
            {
                'name': c['name'],
                'url': c['url'],
                'appid': c['appid'],
                'token': c['access_token'],
                'secret': c['secret'],
                'timeout': c['timeout'],
            }
            for c in self.cfg.by_type('http_webhook')
            if c['url'].strip()
        ]
        self.http_webhook = OneBotHTTPWebhook(
            entries=webhook_entries,
            on_action=self._handle_action,
            log=self.log,
            debug=self.cfg.debug,
        )
        await self.http_webhook.start()
        if webhook_entries:
            self.log.info(f'HTTP 上报已启动: {len(webhook_entries)} 个目标')

    async def _stop_network(self) -> None:
        """停止所有网络连接"""
        if self.ws_server:
            await self.ws_server.stop()
            self.ws_server = None
        if self.http_server:
            self.http_server.detach()
            self.http_server = None
        if self.http_webhook:
            await self.http_webhook.stop()
            self.http_webhook = None

    # ==================== 事件处理 (Observer) ====================

    async def _on_raw_event(self, event: Event, bot: Any) -> None:
        """on_raw_event 回调 — 将事件转为 OneBot 格式推送到 WS 客户端

        同时动态更新 ActionContext 中的 senders / log_services / qq_map。
        """
        has_ws = self.ws_server is not None and self.ws_server.has_clients
        has_webhook = self.http_webhook is not None and self.http_webhook.has_targets
        if not has_ws and not has_webhook:
            return

        appid = event.appid
        aid = str(appid)

        # 动态维护 sender / log_service 映射
        if appid:
            if hasattr(bot, 'sender'):
                self._actx.senders[appid] = bot.sender
            ls = getattr(bot, 'log_service', None)
            if ls:
                self._actx.log_services[appid] = ls

        # 动态更新 qq_map (插件模块可能后加载)
        if aid and aid not in self._actx.qq_map:
            rq = getattr(bot, 'robot_qq', '') or ''
            if rq:
                self._actx.qq_map[aid] = int(rq)
                if self.ws_server:
                    self.ws_server.qq_map = self._actx.qq_map
                if not self._actx.default_qq:
                    self._actx.default_qq = int(rq)
                    if self.ws_server:
                        self.ws_server._default_qq = self._actx.default_qq

        self_qq = self._actx.qq_map.get(aid, self._actx.default_qq) or self._actx.default_qq

        # 缓存 msg_id (用于后续回复 quote)
        # self._cache_msg_id(event, appid) # 让应用层自己缓存

        # 事件转换 (Strategy: message / lifecycle)
        ob_event = None
        if event.is_lifecycle:
            ob_event = await convert_lifecycle_event(event, self.id_mapper, self_qq)
        else:
            ob_event = await convert_message_event(event, self.id_mapper, self_qq)

        if ob_event:
            if has_ws:
                await self.ws_server.broadcast(ob_event, appid=aid)
            if has_webhook:
                self.http_webhook.push(ob_event, appid=aid)

    def _cache_msg_id(self, event: Event, appid: int) -> None:
        """缓存消息 ID 用于后续回复"""
        if not event.message_id:
            return
        chat_id = event.group_id or event.user_id or ''
        if not chat_id:
            return
        key = (appid, chat_id)
        self._actx.msg_id_cache[key] = event.message_id
        self._actx.msg_id_cache.move_to_end(key)
        if len(self._actx.msg_id_cache) > 500:
            self._actx.msg_id_cache.popitem(last=False)

    async def _on_after_send(self, data: dict[str, Any]) -> None:
        """after_send hook — 追踪机器人自身回复 (暂留扩展)"""
        pass

    # ==================== Action 路由 (Command 委托) ====================

    async def _handle_action(
        self,
        action: str,
        params: dict[str, Any],
        echo: str | None = None,
        appid: str = '',
    ) -> dict[str, Any]:
        """处理来自外部框架的 OneBot action — 委托给 ActionRegistry"""
        return await self._action_registry.dispatch(action, params, echo, appid)
