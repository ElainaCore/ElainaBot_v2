"""get_group_member_list — 返回空列表 (最小实现)"""

from __future__ import annotations

from typing import Any

from modules.onebot_adapter.base_action import BaseAction


class GetGroupMemberListAction(BaseAction):
    """get_group_member_list — 返回空列表 (最小实现)"""

    async def execute(self, params: dict[str, Any], echo: str | None = None) -> dict[str, Any]:
        return self._ok([], echo=echo)
