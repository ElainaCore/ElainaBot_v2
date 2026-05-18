"""get_version_info — 返回版本信息"""

from __future__ import annotations

from modules.onebot_adapter.base_action import BaseAction


class GetVersionInfoAction(BaseAction):
    """get_version_info — 返回版本信息"""

    async def execute(self, params: dict, echo=None) -> dict:
        return self._ok(
            {
                'app_name': 'Elaina-OneBot-Adapter',
                'app_version': '1.0.0',
                'protocol_version': 'v11',
            },
            echo=echo,
        )
