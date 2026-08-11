"""get_group_member_info — 返回群成员信息 (最小实现)"""

from __future__ import annotations

from typing import Any

from modules.onebot_adapter.base_action import BaseAction


class GetGroupMemberInfoAction(BaseAction):
    """get_group_member_info — 返回群成员信息 (最小实现)"""

    cache_member: dict[str, dict] = {}

    async def execute(self, params: dict[str, Any], echo: str | None = None) -> dict[str, Any]:
        gid = params.get('group_id', 0)
        uid = params.get('user_id', 0)
        mbase = self.get_member_base_info(gid, uid)
        result = {
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
        } | mbase
        return self._ok(result, echo=echo)

    def get_member_base_info(self, group_id: int, user_id: int):
        return self.cache_member.setdefault(str(group_id), {}).get(str(user_id)) or {}

    def set_member_base_info(self, group_id: int, user_id: int, key: str, val: str | int):
        self.cache_member.setdefault(str(group_id), {}).setdefault(str(user_id), {})[key] = val
