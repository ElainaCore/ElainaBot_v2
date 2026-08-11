"""get_status — 返回在线状态"""

from __future__ import annotations

from typing import Any

from modules.onebot_adapter.base_action import BaseAction


class GetStatusAction(BaseAction):
    """get_status — 返回在线状态"""

    async def execute(self, params: dict[str, Any], echo: str | None = None) -> dict[str, Any]:
        return self._ok({'online': True, 'good': True}, echo=echo)
