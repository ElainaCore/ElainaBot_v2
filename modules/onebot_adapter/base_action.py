"""OneBot Action 命令基类 — Command 模式"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from modules.onebot_adapter.action_context import ActionContext
from modules.onebot_adapter.response_builder import ResponseBuilder


class BaseAction(ABC):
    """OneBot Action 命令基类 (Command 模式)

    子类通过实现 execute() 来响应具体的 OneBot API action。
    """

    _ctx: ActionContext

    def __init__(self, ctx: ActionContext) -> None:
        self._ctx = ctx

    @abstractmethod
    async def execute(self, params: dict[str, Any], echo: str | None = None) -> dict[str, Any]:
        """执行 action 并返回 OneBot 11 格式响应"""
        ...

    # ---- 响应构建辅助 (委托给 ResponseBuilder) ----

    @staticmethod
    def _ok(data: Any = None, echo: str | None = None) -> dict[str, Any]:
        return ResponseBuilder.ok(data, echo)

    @staticmethod
    def _fail(msg: str = '', echo: str | None = None, retcode: int = 1) -> dict[str, Any]:
        return ResponseBuilder.fail(msg, echo, retcode)
