"""set_group_ban — 设置或解除群成员禁言。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from modules.onebot_adapter.base_action import BaseAction


class SetGroupBanAction(BaseAction):
    """Translate OneBot duration semantics to the QQ group mute API."""

    async def execute(self, params: dict[str, Any], echo: str | None = None) -> dict[str, Any]:
        self._ctx.log.info(
            'set_group_ban 请求: '
            f'group_id={params.get("group_id")}, '
            f'user_id={params.get("user_id")}, '
            f'duration={params.get("duration")}'
        )
        group_openid = await self._resolve_openid(params.get('group_id'), 'group')
        if not group_openid:
            self._ctx.log.warning(f'set_group_ban 参数错误: 无效 group_id={params.get("group_id")}')
            return self._fail('缺少有效的 group_id', echo=echo)

        member_openid = await self._resolve_openid(params.get('user_id'), 'user')
        if not member_openid:
            self._ctx.log.warning(f'set_group_ban 参数错误: 无效 user_id={params.get("user_id")}')
            return self._fail('缺少有效的 user_id', echo=echo)

        try:
            duration = max(0, int(params.get('duration') or 0))
        except (TypeError, ValueError):
            self._ctx.log.warning(f'set_group_ban 参数错误: 无效 duration={params.get("duration")}')
            return self._fail('duration 必须为整数秒', echo=echo)

        sender = self._ctx.get_sender()
        if not sender:
            self._ctx.log.warning(
                f'set_group_ban 执行失败: group_id={group_openid}, '
                f'user_id={member_openid}, duration={duration}, reason=无可用的消息发送器'
            )
            return self._fail('无可用的消息发送器', echo=echo)

        setting, error = await sender.get_group_restrict_chat_setting(group_openid, return_error=True)
        if setting is None:
            self._ctx.log.warning(
                f'set_group_ban 查询禁言状态失败: group_id={group_openid}, '
                f'user_id={member_openid}, duration={duration}, response={error}'
            )
            return self._platform_fail(error, echo, '查询群禁言状态失败')

        muted_members = {
            str(item.get('member_openid'))
            for item in (setting.get('members') or [])
            if isinstance(item, dict) and item.get('member_openid')
        }
        is_muted = member_openid in muted_members
        if duration == 0 and not is_muted:
            self._ctx.log.info(
                f'set_group_ban 无需操作: group_id={group_openid}, '
                f'user_id={member_openid}, duration=0, reason=成员未被禁言'
            )
            return self._ok({}, echo=echo)

        operation = 'del' if duration == 0 else ('update' if is_muted else 'add')
        member = {
            'op': operation,
            'member_openid': member_openid,
        }
        if duration > 0:
            member['mute_expire_at'] = (datetime.now().astimezone() + timedelta(seconds=duration)).isoformat(timespec='seconds')

        ok, response = await sender.set_group_member_mute(group_openid, [member])
        if not ok:
            self._ctx.log.warning(
                f'set_group_ban 设置失败: op={operation}, group_id={group_openid}, '
                f'user_id={member_openid}, duration={duration}, response={response}'
            )
            return self._platform_fail(response, echo, '设置群禁言失败')
        self._ctx.log.info(
            f'set_group_ban 成功: op={operation}, group_id={group_openid}, '
            f'user_id={member_openid}, duration={duration}'
        )
        return self._ok(response if isinstance(response, dict) else {}, echo=echo)

    async def _resolve_openid(self, raw_id: Any, id_type: str) -> str | None:
        value = str(raw_id or '').strip()
        if not value:
            return None
        if not value.isdigit():
            return value
        if not self._ctx.id_mapper:
            return None
        return await self._ctx.id_mapper.to_openid_by_type(int(value), id_type)

    def _platform_fail(self, response: Any, echo: str | None, fallback: str) -> dict[str, Any]:
        data = response if isinstance(response, dict) else {}
        message = str(data.get('message') or data.get('msg') or response or fallback)
        try:
            retcode = int(data.get('code') or data.get('err_code') or 1)
        except (TypeError, ValueError):
            retcode = 1
        return self._fail(message, echo=echo, retcode=retcode)
