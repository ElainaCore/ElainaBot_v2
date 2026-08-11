"""get_login_info — 返回机器人 QQ 号"""

from __future__ import annotations

from typing import Any

from modules.onebot_adapter.base_action import BaseAction


class GetLoginInfoAction(BaseAction):
    """get_login_info — 返回机器人 QQ 号"""

    async def execute(self, params: dict[str, Any], echo: str | None = None) -> dict[str, Any]:
        qq = self._ctx.qq_map.get(self._ctx.current_appid, self._ctx.default_qq) or self._ctx.default_qq
        return self._ok({'user_id': qq, 'nickname': 'ElainaBot'}, echo=echo)
