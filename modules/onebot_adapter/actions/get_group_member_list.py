"""get_group_member_list — 返回空列表 (最小实现)"""

from __future__ import annotations

from modules.onebot_adapter.base_action import BaseAction


class GetGroupMemberListAction(BaseAction):
    """get_group_member_list — 返回空列表 (最小实现)"""

    async def execute(self, params: dict, echo=None) -> dict:
        return self._ok([], echo=echo)
