"""send_msg / send_group_msg / send_private_msg — Command 模式

策略:
  - 自动根据 message_type 或 group_id/user_id 有无判断群/私聊
  - 通过 IDMapper 将 QQ 号反查为 openid
  - 通过 SegmentParser 解析 OneBot 消息段 → ParsedMessage
  - 通过 MessageSenderService 选择发送策略 (对应 sender.py 的全部 send 模式):
      text / image / voice / video / file / markdown / buttons / reply
"""

from __future__ import annotations

from typing import Any

from modules.onebot_adapter.action_context import ActionContext
from modules.onebot_adapter.base_action import BaseAction
from modules.onebot_adapter.payload import MessageSenderService, ParsedMessage, SegmentParser


class SendMessageAction(BaseAction):
    """send_msg / send_group_msg / send_private_msg

    通过 force_type 参数区分三种变体:
      - ''               → send_msg (自动判断)
      - 'group'          → send_group_msg
      - 'private'        → send_private_msg

    sender.py 发送模式映射:
      - 纯文本      → send_to_group / send_to_user  (MSG_TYPE_TEXT)
      - 图片 (+文本) → upload_media_bytes(type=1) → MSG_TYPE_MEDIA
      - 语音 (+文本) → upload_media_bytes(type=3) → MSG_TYPE_MEDIA
      - 视频 (+文本) → upload_media_bytes(type=2) → MSG_TYPE_MEDIA
      - 文件 (+文本) → upload_media_bytes(type=4) → MSG_TYPE_MEDIA
      - Markdown     → send_to_group/send_to_user (MSG_TYPE_MARKDOWN)
      - 按钮         → keyboard 参数 → build_keyboard()
      - 回复引用     → message_reference 参数
    """

    _force_type: str = ''

    def __init__(self, ctx: ActionContext, force_type: str = '') -> None:
        super().__init__(ctx)
        self._force_type = force_type

    async def execute(self, params: dict[str, Any], echo: str | None = None) -> dict[str, Any]:
        """执行 send_msg / send_group_msg / send_private_msg"""
        # ---- 1. 确定目标 ----
        msg_type = self._force_type or params.get('message_type', '')
        group_id = params.pop('group_id', None)
        user_id = params.pop('user_id', None)

        if not msg_type:
            msg_type = 'group' if group_id else 'private'

        is_group = msg_type == 'group' and bool(group_id)
        raw_id = group_id if is_group else user_id
        if not raw_id:
            return self._fail('缺少 group_id 或 user_id', echo=echo)

        # ---- 2. 解析消息段 → ParsedMessage ----
        message = params.pop('message', '')
        parsed = SegmentParser.parse(message)

        # 空消息保护
        if parsed.msg_type == 'text' and not parsed.text_content and not parsed.has_media:
            parsed.text_content = '[发送了一条空消息]'

        # ---- 3. 获取 sender ----
        sender = self._ctx.get_sender()
        if not sender:
            return self._fail('无可用的消息发送器', echo=echo)

        # ---- 4. ID 映射 ----
        id_type = 'group' if is_group else 'user'
        if isinstance(raw_id, int):
            real_id = await self._ctx.id_mapper.to_openid_by_type(int(raw_id), id_type)
        else:
            real_id = raw_id
        if not real_id:
            return self._fail(f'未知{"群号" if is_group else "用户"}: {raw_id}', echo=echo)

        # ---- 5. 日志 ----
        label = self._build_send_label(parsed)
        self._ctx.log.info(f'{"群" if is_group else "私聊"} {raw_id}: {label}')

        # ---- 6. 发送 ----
        gid = real_id if is_group else None
        uid = None if is_group else real_id

        ok, data, send_payload = await MessageSenderService.send(sender, gid, uid, parsed, **params)

        # ---- 7. 记录日志 ----
        await self._ctx.log_send('group' if is_group else 'private', real_id, label, ok, data, send_payload)

        # ---- 8. 响应 ----
        if ok:
            message_id = data.get('id') or data.get('msg_id') or data.get('message_id') if isinstance(data, dict) else ''
            return self._ok({'message_id': message_id or (hash(str(data)) & 0x7FFFFFFF)}, echo=echo)

        self._ctx.log.warning(f'{"群" if is_group else "私聊"} {raw_id} 发送失败: {data}')
        retcode = (data.get('err_code') or data.get('code') or 1) if isinstance(data, dict) else 1
        return self._fail(str(data), echo=echo, retcode=retcode)

    # ==================== 辅助 ====================

    @staticmethod
    def _build_send_label(parsed: ParsedMessage) -> str:
        """构建发送日志标签"""
        if parsed.image_data:
            return '[图片]'
        if parsed.voice_data:
            return '[语音]'
        if parsed.video_data:
            return '[视频]'
        if parsed.file_data:
            name = parsed.file_name or ''
            return f'[文件]{name}'
        if parsed.msg_type == 'markdown':
            return '[Markdown]'
        return parsed.text_content[:200] if parsed.text_content else '[空]'
