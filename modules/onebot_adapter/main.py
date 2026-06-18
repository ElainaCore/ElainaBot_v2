"""OneBot 11 适配器模块 — 入口

将 Elaina_V2 的消息/事件以 OneBot 11 标准协议通过 WebSocket 推送到
外部机器人框架 (如 Yunzai-Bot), 并处理其回复动作 (send_msg 等)。

设计模式:
  - Facade:      OneBotAdapter (adapter.py)
  - Adapter:     HookAdapter (hook_adapter.py)
  - Command:     BaseAction + 11 子类 (actions/)
  - Registry:    ActionRegistry (action_registry.py)
  - Builder:     ResponseBuilder (response_builder.py)
  - Strategy:    ImageDecoder / SegmentParser / PayloadConverter / MessageSenderService (payload/)
  - Observer:    _on_raw_event (adapter.py)
  - DI:          ActionContext (action_context.py)
  - VO:          OneBotConfig (config.py)

实现方式:
    1. setup 时通过 HookAdapter 运行时 patch 让 BotManager._on_event 在处理前
       触发 on_raw_event hook (该 hook 已在框架 hook.py 文档中声明但未实装)
    2. 模块监听 on_raw_event, 将事件转为 OneBot 格式后推给 WS 客户端
    3. 模块将 WS 路由挂载到框架已有的 aiohttp 服务器, 接收外部框架的 action 请求并调用 sender
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from modules.onebot_adapter.adapter import OneBotAdapter

if TYPE_CHECKING:
    from core.module.manager import ModuleContext

__module_meta__ = {
    'name': 'OneBot 适配器',
    'description': 'OneBot 11 协议适配器, 将消息/事件推送到外部机器人框架',
    'version': '1.4.0',
    'author': 'Elaina',
}

_adapter: OneBotAdapter | None = None  # 全局引用, 用于 teardown


async def setup(ctx: ModuleContext) -> None:
    """模块启用入口"""
    global _adapter
    _adapter = OneBotAdapter(ctx)
    await _adapter.start()


async def teardown() -> None:
    """模块禁用/框架停止入口"""
    global _adapter
    if _adapter:
        await _adapter.stop()
        _adapter = None
