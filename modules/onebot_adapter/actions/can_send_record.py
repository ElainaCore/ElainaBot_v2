"""can_send_record — 是否支持发送语音"""

from __future__ import annotations

from typing import Any

from modules.onebot_adapter.base_action import BaseAction


class CanSendRecordAction(BaseAction):
    """can_send_record — 是否支持发送语音"""

    async def execute(self, params: dict[str, Any], echo: str | None = None) -> dict[str, Any]:
        return self._ok({'yes': False}, echo=echo)
