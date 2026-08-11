"""set_group_add_request - review a QBot group join request."""

from __future__ import annotations

from typing import Any

from modules.onebot_adapter.action_context import ActionContext
from modules.onebot_adapter.base_action import BaseAction
from modules.onebot_adapter.lib.group_join_request_flag import GroupJoinRequestFlagCodec


class SetGroupAddRequestAction(BaseAction):
    """Bridge the OneBot approval action to QBot's group management API."""

    def __init__(self, ctx: ActionContext) -> None:
        super().__init__(ctx)

    async def execute(self, params: dict[str, Any], echo: str | None = None) -> dict[str, Any]:
        if params.get('sub_type') != 'add':
            return self._fail('QBot 仅支持处理成员入群申请', echo=echo)

        request = GroupJoinRequestFlagCodec.decode(params.get('flag'))
        if request is None:
            return self._fail('无效或已不兼容的入群申请标识', echo=echo)

        sender = self._ctx.get_sender()
        if sender is None:
            return self._fail('无可用的消息发送器', echo=echo)

        if 'approve' not in params:
            return self._fail('缺少 approve', echo=echo)
        approve = params['approve']
        if isinstance(approve, str):
            normalized = approve.strip().lower()
            if normalized in {'1', 'true', 'yes', 'on'}:
                approve = True
            elif normalized in {'0', 'false', 'no', 'off'}:
                approve = False
            else:
                return self._fail('approve 必须是布尔值', echo=echo)
        elif not isinstance(approve, bool):
            if approve in {0, 1}:
                approve = bool(approve)
            else:
                return self._fail('approve 必须是布尔值', echo=echo)
        op = 'approve' if approve else 'decline'
        reason = str(params.get('reason') or '')
        success, response = await sender.review_group_join_request(
            request['group_openid'],
            request['member_openid'],
            op,
            join_request_id=request['join_request_id'],
            reject_reason=reason,
        )
        if success:
            return self._ok({}, echo=echo)

        detail = (response.get('message') or response.get('msg')) if isinstance(response, dict) else response
        return self._fail(f'处理入群申请失败: {detail or "未知错误"}', echo=echo)
