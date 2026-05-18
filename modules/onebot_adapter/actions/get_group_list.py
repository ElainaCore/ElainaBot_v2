"""get_group_list — 返回已缓存的群列表"""

from __future__ import annotations

from typing import Any

from modules.onebot_adapter.base_action import BaseAction


class GetGroupListAction(BaseAction):
    """get_group_list — 返回已缓存的群列表"""

    async def execute(self, params: dict[str, Any], echo: str | None = None) -> dict[str, Any]:
        groups = []
        for (_openid, _type), qq_id in self._ctx.id_mapper._cache_fwd.items():
            if _type == 'group':
                groups.append(
                    {
                        'group_id': qq_id,
                        'group_name': f'群{qq_id}',
                        'member_count': 0,
                        'max_member_count': 0,
                    }
                )
        return self._ok(groups, echo=echo)
