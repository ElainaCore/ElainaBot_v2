"""统一消息发送服务 — Strategy 模式

封装发送路径的选择策略:
  - 含图片 → 通过 upload_media_bytes 上传后以 MSG_TYPE_MEDIA 发送
  - 纯文本 → 通过 send_to_group / send_to_user 发送
"""

from __future__ import annotations

import random
from typing import Any

from core.message._http import MessageType
from core.message.media import upload_media_bytes
from core.message.sender import MessageSender
from modules.onebot_adapter.payload.payload_converter import PayloadConverter


class MessageSenderService:
    """统一消息发送服务: 纯文本 / 图片 / 图文混合"""

    @staticmethod
    async def send(
        sender: MessageSender,
        group_id: int | str | None,
        user_id: int | str | None,
        payload: str | dict[str, Any],
        image_bytes: bytes | None,
        msg_id: int | str | None,
    ) -> tuple[bool, Any]:
        """统一发送入口

        Returns:
            (ok: bool, data: Any) — 成功为 (True, resp_data), 失败为 (False, error_msg)
        """
        target = group_id or user_id
        prefix = 'groups' if group_id else 'users'

        if image_bytes:
            return await MessageSenderService._send_media(sender, target, prefix, payload, image_bytes, msg_id)

        return await MessageSenderService._send_text(sender, group_id, user_id, target, payload, msg_id)

    @staticmethod
    async def _send_media(
        sender: MessageSender,
        target: int | str,
        prefix: str,
        payload: str | dict[str, Any],
        image_bytes: bytes,
        msg_id: int | str | None,
    ) -> tuple[bool, Any]:
        file_info = await upload_media_bytes(sender, image_bytes, 1, f'/v2/{prefix}/{target}/files')
        if not file_info:
            return False, '图片上传失败'
        media_payload: dict[str, Any] = {
            'msg_type': MessageType.MSG_TYPE_MEDIA,
            'msg_seq': random.randint(10000, 999999),
            'content': payload or '',
            'media': {'file_info': file_info},
        }
        if msg_id:
            media_payload['msg_id'] = msg_id
        return await sender.post_json(f'/v2/{prefix}/{target}/messages', media_payload)

    @staticmethod
    async def _send_text(
        sender: MessageSender,
        group_id: int | str | None,
        user_id: int | str | None,
        target: int | str,
        payload: str | dict[str, Any],
        msg_id: int | str | None,
    ) -> tuple[bool, Any]:
        kwargs = PayloadConverter.convert(payload)
        if group_id:
            ok, data, _ = await sender.send_to_group(target, msg_id=msg_id, **kwargs)
        else:
            ok, data, _ = await sender.send_to_user(target, msg_id=msg_id, **kwargs)
        return ok, data
