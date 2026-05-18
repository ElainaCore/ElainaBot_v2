"""can_send_image — 是否支持发送图片"""

from __future__ import annotations

from modules.onebot_adapter.base_action import BaseAction


class CanSendImageAction(BaseAction):
    """can_send_image — 是否支持发送图片"""

    async def execute(self, params: dict, echo=None) -> dict:
        return self._ok({'yes': True}, echo=echo)
