"""delete_msg — 撤回消息

对应 sender.py 的 recall() 模式。

策略:
  - 接受 message_id (OneBot 标准) + 可选的 group_id/user_id 提示
  - 有 group_id/user_id 时直接构造 recall 端点
  - 无提示时依次尝试群聊和私聊端点撤回
"""

from __future__ import annotations

from typing import Any

from modules.onebot_adapter.action_context import ActionContext
from modules.onebot_adapter.base_action import BaseAction


class DeleteMessageAction(BaseAction):
    """delete_msg — 撤回已发送的消息

    sender.py 对应模式: recall()

    OneBot 参数:
      - message_id: int | str  (必填) 消息 ID
      - group_id:  int | None  (可选) 群号提示, 优先使用此端点
      - user_id:   int | None  (可选) 用户号提示
    """

    def __init__(self, ctx: ActionContext) -> None:
        super().__init__(ctx)

    async def execute(self, params: dict[str, Any], echo: str | None = None) -> dict[str, Any]:
        message_id = params.get('message_id')
        if not message_id:
            return self._fail('缺少 message_id', echo=echo)

        sender = self._ctx.get_sender()
        if not sender:
            return self._fail('无可用的消息发送器', echo=echo)

        group_id = params.get('group_id')
        user_id = params.get('user_id')

        # 构造候选端点列表
        endpoints: list[str] = []
        if group_id:
            real_gid = await self._resolve_id(group_id, 'group')
            if real_gid:
                endpoints.append(f'/v2/groups/{real_gid}/messages/{message_id}')
        if user_id:
            real_uid = await self._resolve_id(user_id, 'user')
            if real_uid:
                endpoints.append(f'/v2/users/{real_uid}/messages/{message_id}')

        # 无提示时依次尝试
        if not endpoints:
            endpoints = [
                f'/v2/groups/{message_id}',      # 占位, 实际需替换
                f'/v2/users/{message_id}',
            ]
            # 无具体目标 ID 时无法正确构造端点 — 记录日志并失败
            self._ctx.log.warning(f'delete_msg: 未提供 group_id/user_id, 无法构造撤回端点')
            return self._fail('需要提供 group_id 或 user_id 以定位消息', echo=echo, retcode=1)

        # 依次尝试每个端点
        for ep in endpoints:
            ok, data = await sender.delete(ep)
            if ok:
                self._ctx.log.info(f'delete_msg 成功: {message_id} via {ep}')
                return self._ok({'message_id': message_id}, echo=echo)
            self._ctx.log.debug(f'delete_msg 端点尝试失败: {ep} -> {data}')

        self._ctx.log.warning(f'delete_msg 全部端点失败: {message_id}')
        return self._fail('撤回失败: 消息不存在或权限不足', echo=echo, retcode=1)

    async def _resolve_id(self, raw_id: int | str, id_type: str) -> int | str | None:
        """将 QQ 号反查为 openid (如果 id_mapper 可用)"""
        if isinstance(raw_id, int) and self._ctx.id_mapper:
            return await self._ctx.id_mapper.to_openid_by_type(raw_id, id_type)
        return raw_id
