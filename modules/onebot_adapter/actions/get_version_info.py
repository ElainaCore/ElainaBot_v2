"""get_version_info — 返回版本信息"""

from __future__ import annotations

from typing import Any

from modules.onebot_adapter.base_action import BaseAction


class GetVersionInfoAction(BaseAction):
    """get_version_info — 返回版本信息"""

    async def execute(self, params: dict[str, Any], echo: str | None = None) -> dict[str, Any]:
        return self._ok(
            {
                'app_name': 'Elaina-OneBot-Adapter',
                'app_version': '1.5.0',
                'protocol_version': 'v11',
            },
            echo=echo,
        )
