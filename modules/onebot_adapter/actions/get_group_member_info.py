"""get_group_member_info — 返回群成员信息 (最小实现)"""

from __future__ import annotations

from typing import Any

from modules.onebot_adapter.base_action import BaseAction


class GetGroupMemberInfoAction(BaseAction):
    """get_group_member_info — 返回群成员信息 (最小实现)"""

    async def execute(self, params: dict[str, Any], echo: str | None = None) -> dict[str, Any]:
        gid = params.get('group_id', 0)
        uid = params.get('user_id', 0)
        return self._ok(
            {
                'group_id': gid,
                'user_id': uid,
                'nickname': str(uid),
                'card': '',
                'sex': 'unknown',
                'age': 0,
                'join_time': 0,
                'last_sent_time': 0,
                'level': '0',
                'role': 'member',
                'title': '',
            },
            echo=echo,
        )
