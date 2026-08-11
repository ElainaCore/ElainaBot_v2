"""框架 Hook 适配器 — Adapter 模式

封装对 BotManager._on_event 的运行时 monkey-patch 逻辑:
  - 补全 on_raw_event hook 的触发点 (框架已声明但未实装)
  - teardown 时安全还原原始方法
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.bot.manager import BotManager


class HookAdapter:
    """框架 Hook 适配器 (Adapter 模式)

    封装对 BotManager._on_event 的运行时 monkey-patch 逻辑:
      - 补全 on_raw_event hook 的触发点 (框架已声明但未实装)
      - teardown 时安全还原原始方法
    """

    _log: Any
    _bm: BotManager | None
    _original_on_event: Callable[..., Coroutine[Any, Any, None]] | None

    def __init__(self, log: Any) -> None:
        self._log = log
        self._bm = None  # BotManager
        self._original_on_event = None

    def install(self, bm: BotManager) -> None:
        """安装 on_raw_event hook 触发点

        通过运行时 patch BotManager._on_event, 在原始事件分派前
        触发 on_raw_event hook。
        """
        try:
            from core.module.hook import get_hook_manager

            hooks = get_hook_manager()
            self._bm = bm
            self._original_on_event = bm._on_event

            original = self._original_on_event  # 闭包捕获

            async def patched_on_event(event):
                bot = bm._bots.get(event.appid)
                if bot and hooks.has('on_raw_event'):
                    asyncio.create_task(hooks.emit('on_raw_event', event, bot))
                await original(event)

            bm._on_event = patched_on_event

            # 同步更新已有 WS 客户端的回调引用
            for bot_inst in bm._bots.values():
                if hasattr(bot_inst, 'ws_client') and bot_inst.ws_client:
                    bot_inst.ws_client._on_event = patched_on_event

            self._log.info('on_raw_event hook 已安装')
        except Exception as e:
            self._log.warning(f'安装事件 hook 失败: {e}')

    def uninstall(self) -> None:
        """还原 _on_event 到原始方法"""
        if self._original_on_event is None or self._bm is None:
            return
        try:
            self._bm._on_event = self._original_on_event
            for bot_inst in self._bm._bots.values():
                if hasattr(bot_inst, 'ws_client') and bot_inst.ws_client:
                    bot_inst.ws_client._on_event = self._original_on_event
            self._log.info('on_raw_event hook 已卸载')
        except Exception as e:
            self._log.warning(f'卸载事件 hook 失败: {e}')
        finally:
            self._bm = None
            self._original_on_event = None
