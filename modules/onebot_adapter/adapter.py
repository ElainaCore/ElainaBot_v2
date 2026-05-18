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

    def __init__(self, module_ctx: ModuleContext) -> None:
        self._mctx = module_ctx  # ModuleContext
        self.log = module_ctx.log
        self.cfg: OneBotConfig = OneBotConfig()

        # 基础设施
        self.id_mapper: IDMapper | None = None
        self.ws_server: OneBotWSServer | None = None
        self._hook_adapter = HookAdapter(self.log)

        # Action 上下文 (DI 容器)
        self._actx = ActionContext(log=self.log)

        # Action 路由 (Command 模式)
        self._action_registry: ActionRegistry | None = None

        # 运行时状态
        self._bm: BotManager | None = None  # BotManager 引用

    # ==================== 生命周期 ====================

    async def start(self) -> None:
        """启动适配器: 配置 → ID 映射 → Hook → WS → Action 路由"""

        # 1. 加载配置
        raw_config = self._mctx.ensure_config(OneBotConfig.defaults(), comments=OneBotConfig.comments())
        self.cfg = OneBotConfig.from_dict(raw_config)
        self.log.info(f'配置: path={self.cfg.ws_path}, token={"***" if self.cfg.has_token else "(无)"}')

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

        # 7. 启动 WebSocket 服务
        await self._start_ws_server()

    async def stop(self) -> None:
        """停止适配器: Hook → WS → ID 映射"""
        # 1. 卸载 Hook
        self._hook_adapter.uninstall()

        # 2. 停止 WS
        if self.ws_server:
            await self.ws_server.stop()

        # 3. 关闭 ID 映射
        if self.id_mapper:
            await self.id_mapper.close()

        self.log.info('OneBot 适配器已停止')

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

    # ==================== WebSocket 服务 ====================

    async def _start_ws_server(self) -> None:
        """创建并启动 WebSocket 服务 (正向 + 反向 + 心跳)"""

        # 解析反向 WS 配置
        reverse_entries = [
            {'url': str(e.get('url', '')), 'appid': str(e.get('appid', ''))}
            for e in (self.cfg.reverse_ws_urls or [])
            if isinstance(e, dict) and str(e.get('url', '')).strip()
        ]

        self.ws_server = OneBotWSServer(
            access_token=self.cfg.access_token,
            heartbeat_interval=self.cfg.heartbeat_interval,
            on_action=self._handle_action,
            default_qq=self._actx.default_qq,
            qq_map=self._actx.qq_map,
            log=self.log,
            ws_path=self.cfg.ws_path,
            reverse_entries=reverse_entries,
            reconnect_interval=self.cfg.reconnect_interval,
            debug=self.cfg.debug,
        )

        # 正向 WS: 挂载到框架 aiohttp app
        app = self._get_framework_app()
        if app:
            self.ws_server.attach(app)
            port = self._get_framework_port()
            self.log.info(f'正向 WS 已挂载: ws://0.0.0.0:{port}{self.cfg.ws_path}')
        else:
            self.log.warning('无法获取框架 aiohttp app, 正向 WS 未挂载')

        # 反向 WS: 主动连接外部服务器
        await self.ws_server.start_reverse()
        if reverse_entries:
            self.log.info(f'反向 WS 已启动: {len(reverse_entries)} 个连接')

        # 心跳
        self.ws_server.start_heartbeat()

    # ==================== 事件处理 (Observer) ====================

    async def _on_raw_event(self, event: Event, bot: Any) -> None:
        """on_raw_event 回调 — 将事件转为 OneBot 格式推送到 WS 客户端

        同时动态更新 ActionContext 中的 senders / log_services / qq_map。
        """
        if not self.ws_server or not self.ws_server.has_clients:
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
                self.ws_server.qq_map = self._actx.qq_map
                if not self._actx.default_qq:
                    self._actx.default_qq = int(rq)
                    self.ws_server._default_qq = self._actx.default_qq

        self_qq = self._actx.qq_map.get(aid, self._actx.default_qq) or self._actx.default_qq

        # 缓存 msg_id (用于后续回复 quote)
        self._cache_msg_id(event, appid)

        # 事件转换 (Strategy: message / lifecycle)
        ob_event = None
        if event.is_lifecycle:
            ob_event = await convert_lifecycle_event(event, self.id_mapper, self_qq)
        else:
            ob_event = await convert_message_event(event, self.id_mapper, self_qq)

        if ob_event:
            await self._actx.log_recv(aid, event, ob_event)
            await self.ws_server.broadcast(ob_event, appid=aid)

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
