"""can_send_image — 是否支持发送图片"""

from __future__ import annotations

from typing import Any

from modules.onebot_adapter.base_action import BaseAction


class CanSendImageAction(BaseAction):
    """can_send_image — 是否支持发送图片"""

    async def execute(self, params: dict[str, Any], echo: str | None = None) -> dict[str, Any]:
        return self._ok({'yes': True}, echo=echo)
