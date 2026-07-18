"""统一消息发送服务 — Strategy 模式

封装发送路径的选择策略, 对应 sender.py 的全部 send 模式:
  - 含图片   → upload_media_bytes(type=1) → MSG_TYPE_MEDIA 发送
  - 含语音   → upload_media_bytes(type=3) → MSG_TYPE_MEDIA 发送
  - 含视频   → upload_media_bytes(type=2) → MSG_TYPE_MEDIA 发送
  - 含文件   → upload_media_bytes(type=4) 或 URL 直传 → MSG_TYPE_MEDIA 发送
  - 纯文本   → send_to_group / send_to_user
  - Markdown → MSG_TYPE_MARKDOWN 发送, 支持 buttons
  - 按钮     → 通过 keyboard 参数传递
  - 回复引用 → message_reference 参数传递
"""

from __future__ import annotations

from typing import Any

from core.message._http import MessageType
from core.message.sender import MessageSender
from modules.onebot_adapter.payload.payload_converter import PayloadConverter
from modules.onebot_adapter.payload.segment_parser import ParsedMessage


class MessageSenderService:
    """统一消息发送服务: 纯文本 / 图片 / 语音 / 视频 / 文件 / Markdown / 按钮"""

    @classmethod
    async def send(
        cls,
        sender: MessageSender,
        group_id: int | str | None,
        user_id: int | str | None,
        parsed: ParsedMessage,
        msg_id: int | str | None = None,
    ) -> tuple[bool, Any, dict[str, Any]]:
        """统一发送入口 — 根据 ParsedMessage 选择策略

        Returns:
            (ok, data, send_payload)
        """
        target = group_id or user_id
        prefix = 'groups' if group_id else 'users'

        # 1. 媒体文件 (语音/视频/文件) — 需要先上传再发送
        if parsed.media_type and parsed.media_type != 1:
            # voice=3, video=2, file=4
            return await cls._send_media(sender, target, prefix, parsed, msg_id, group_id=group_id, user_id=user_id)

        # 2. 图片 — 上传后以 MSG_TYPE_MEDIA 发送
        if parsed.image_data:
            return await cls._send_media(sender, target, prefix, parsed, msg_id, group_id=group_id, user_id=user_id)

        # 3. Markdown
        if parsed.msg_type == 'markdown' and parsed.markdown_content:
            return await cls._send_markdown(sender, group_id, user_id, target, parsed, msg_id)

        # 4. 纯文本 (可能带 buttons)
        return await cls._send_text(sender, group_id, user_id, target, parsed, msg_id)

    # ==================== 文本发送 ====================

    @classmethod
    async def _send_text(
        cls, sender: MessageSender, group_id: int | str | None, user_id: int | str | None, target: int | str, parsed: ParsedMessage, msg_id: int | str | None
    ) -> tuple[bool, Any, dict[str, Any]]:
        content = parsed.text_content or '[空的文本消息]'
        return await cls.send_msg_common(sender, group_id, user_id, target, parsed, msg_id, content)

    # ==================== Markdown 发送 ====================

    @classmethod
    async def _send_markdown(
        cls, sender: MessageSender, group_id: int | str | None, user_id: int | str | None, target: int | str, parsed: ParsedMessage, msg_id: int | str | None
    ) -> tuple[bool, Any, dict[str, Any]]:
        content = parsed.markdown_content or parsed.text_content
        return await cls.send_msg_common(sender, group_id, user_id, target, parsed, msg_id, content, msg_type=MessageType.MSG_TYPE_MARKDOWN)

    @classmethod
    async def send_msg_common(
        cls,
        sender: MessageSender,
        group_id: int | str | None,
        user_id: int | str | None,
        target: int | str,
        parsed: ParsedMessage,
        msg_id: int | str | None,
        content: str,
        msg_type: int | None = None,
    ):
        kwargs: dict[str, Any] = PayloadConverter.convert(content)
        if msg_type is not None:
            kwargs['msg_type'] = msg_type
        if parsed.buttons:
            kwargs['buttons'] = parsed.buttons
        if parsed.message_reference:
            kwargs['message_reference'] = parsed.message_reference
        kwargs['msg_id'] = msg_id
        func = sender.send_to_group if group_id else sender.send_to_user
        ok, data, send_payload = await func(target, **kwargs)
        return ok, data, send_payload

    # ==================== 媒体发送 ====================

    @classmethod
    async def _send_media(
        cls,
        sender: MessageSender,
        target: int | str,
        prefix: str,
        parsed: ParsedMessage,
        msg_id: int | str | None,
        *,
        group_id: int | str | None = None,
        user_id: int | str | None = None,
    ) -> tuple[bool, Any, dict[str, Any]]:
        """统一媒体发送: image(1)/video(2)/voice(3)/file(4)"""
        media_type = parsed.media_type or 1
        media_data = parsed.media_data
        if not media_data:
            return False, '媒体数据为空', {}
        ctn = parsed.text_content
        data = await sender._send_media(
            sender,
            media_data,
            media_type,
            ctn,
            msg_id=msg_id,
            target_group_id=group_id,
            target_user_id=user_id,
        )
        error = sender.error if hasattr(sender, 'error') else None
        return data is not None, data or error, data
