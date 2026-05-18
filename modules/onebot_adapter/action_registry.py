"""Action 注册表 — Registry 模式

管理 action_name → BaseAction 的映射, 支持批量注册和统一路由。
"""

from __future__ import annotations

from modules.onebot_adapter.action_context import ActionContext
from modules.onebot_adapter.actions import (
    CanSendImageAction,
    CanSendRecordAction,
    GetFriendListAction,
    GetGroupListAction,
    GetGroupMemberInfoAction,
    GetGroupMemberListAction,
    GetLoginInfoAction,
    GetStatusAction,
    GetStrangerInfoAction,
    GetVersionInfoAction,
    SendMessageAction,
)
from modules.onebot_adapter.base_action import BaseAction
from modules.onebot_adapter.response_builder import ResponseBuilder


class ActionRegistry:
    """Action 注册表 (Registry 模式)

    管理 action_name → BaseAction 的映射, 支持批量注册和统一路由。
    """

    def __init__(self, ctx: ActionContext):
        self._ctx = ctx
        self._actions: dict[str, BaseAction] = {}

    def register(self, name: str, action: BaseAction) -> None:
        """注册单个 action 处理器"""
        self._actions[name] = action

    def register_all(self, mapping: dict[str, BaseAction]) -> None:
        """批量注册"""
        self._actions.update(mapping)

    async def dispatch(
        self, action: str, params: dict, echo=None, appid: str = ''
    ) -> dict:
        """路由 action 到对应处理器并执行"""
        self._ctx.current_appid = appid
        handler = self._actions.get(action)
        if handler is None:
            return ResponseBuilder.fail(
                f'不支持的 action: {action}', echo=echo
            )
        return await handler.execute(params, echo)

    @classmethod
    def create_default(cls, ctx: ActionContext) -> 'ActionRegistry':
        """工厂方法: 创建预配置的 Registry (注册所有标准 action)"""
        registry = cls(ctx)
        registry.register_all(
            {
                'send_msg': SendMessageAction(ctx),
                'send_group_msg': SendMessageAction(ctx, force_type='group'),
                'send_private_msg': SendMessageAction(
                    ctx, force_type='private'
                ),
                'get_login_info': GetLoginInfoAction(ctx),
                'get_group_list': GetGroupListAction(ctx),
                'get_friend_list': GetFriendListAction(ctx),
                'get_stranger_info': GetStrangerInfoAction(ctx),
                'get_group_member_info': GetGroupMemberInfoAction(ctx),
                'get_group_member_list': GetGroupMemberListAction(ctx),
                'get_status': GetStatusAction(ctx),
                'get_version_info': GetVersionInfoAction(ctx),
                'can_send_image': CanSendImageAction(ctx),
                'can_send_record': CanSendRecordAction(ctx),
            }
        )
        return registry
