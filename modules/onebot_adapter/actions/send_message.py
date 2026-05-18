"""send_msg / send_group_msg / send_private_msg — Command 模式

策略:
  - 自动根据 message_type 或 group_id/user_id 有无判断群/私聊
  - 通过 IDMapper 将 QQ 号反查为 openid
  - 支持纯文本 / 图片 / Markdown
"""

from __future__ import annotations

from typing import Any

from modules.onebot_adapter.action_context import ActionContext
from modules.onebot_adapter.base_action import BaseAction
from modules.onebot_adapter.payload import MessageSenderService, SegmentParser


class SendMessageAction(BaseAction):
    """send_msg / send_group_msg / send_private_msg

    通过 force_type 参数区分三种变体:
      - ''               → send_msg (自动判断)
      - 'group'          → send_group_msg
      - 'private'        → send_private_msg
    """

    _force_type: str = ''

    def __init__(self, ctx: ActionContext, force_type: str = '') -> None:
        super().__init__(ctx)
        self._force_type = force_type

    async def execute(self, params: dict[str, Any], echo: str | None = None) -> dict[str, Any]:
        msg_type = self._force_type or params.get('message_type', '')
        group_id = params.get('group_id')
        user_id = params.get('user_id')

        if not msg_type:
            msg_type = 'group' if group_id else 'private'

        payload, image_bytes = SegmentParser.parse(params.get('message', ''))
        if not payload and not image_bytes:
            return self._fail('消息内容为空', echo=echo)

        sender = self._ctx.get_sender()
        if not sender:
            return self._fail('无可用的消息发送器', echo=echo)

        is_group = msg_type == 'group' and group_id
        raw_id = group_id if is_group else user_id
        if not raw_id:
            return self._fail('缺少 group_id 或 user_id', echo=echo)

        id_type = 'group' if is_group else 'user'
        if isinstance(raw_id, int):
            real_id = await self._ctx.id_mapper.to_openid_by_type(int(raw_id), id_type)
        else:
            real_id = raw_id
        if not real_id:
            return self._fail(f'未知{"群号" if is_group else "用户"}: {raw_id}', echo=echo)

        label = str(payload)[:200] if payload else '[image]'
        self._ctx.log.info(f'{"群" if is_group else "私聊"} {raw_id}: {label}')

        gid = real_id if is_group else None
        uid = None if is_group else real_id
        msg_id_ref = self._ctx.find_msg_id(real_id)

        ok, data = await MessageSenderService.send(
            sender,
            gid,
            uid,
            payload,
            image_bytes,
            msg_id_ref,
        )

        await self._ctx.log_send(
            'group' if is_group else 'private',
            real_id,
            label,
            ok,
            data,
        )

        if ok:
            return self._ok({'message_id': hash(str(data)) & 0x7FFFFFFF}, echo=echo)

        self._ctx.log.warning(f'{"群" if is_group else "私聊"} {raw_id} 发送失败: {data}')
        return self._fail(str(data), echo=echo)
