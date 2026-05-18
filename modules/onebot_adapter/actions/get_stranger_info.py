"""get_stranger_info — 返回陌生人信息 (最小实现)"""

from __future__ import annotations

from typing import Any

from modules.onebot_adapter.base_action import BaseAction


class GetStrangerInfoAction(BaseAction):
    """get_stranger_info — 返回陌生人信息 (最小实现)"""

    async def execute(self, params: dict[str, Any], echo: str | None = None) -> dict[str, Any]:
        uid = params.get('user_id', 0)
        return self._ok(
            {
                'user_id': uid,
                'nickname': str(uid),
                'sex': 'unknown',
                'age': 0,
            },
            echo=echo,
        )
