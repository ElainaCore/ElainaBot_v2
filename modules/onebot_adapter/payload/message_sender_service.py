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

    @classmethod
    async def send(
        cls,
        sender: MessageSender,
        group_id: int | str | None,
        user_id: int | str | None,
        payload: str | dict[str, Any],
        image_bytes: bytes | str | None,
        msg_id: int | str | None,
    ) -> tuple[bool, Any, dict[str, Any]]:
        """统一发送入口

        Returns:
            (ok, data, send_payload) — 成功为接口响应, 失败为错误对象
        """
        target = group_id or user_id
        prefix = 'groups' if group_id else 'users'

        if image_bytes:
            return await cls._send_media(sender, target, prefix, payload, image_bytes, msg_id)
        return await cls._send_text(sender, group_id, user_id, target, payload, msg_id)

    @classmethod
    async def _send_media(
        cls,
        sender: MessageSender,
        target: int | str,
        prefix: str,
        payload: str | dict[str, Any],
        image_bytes: str | bytes,
        msg_id: int | str | None,
    ) -> tuple[bool, Any, dict[str, Any]]:
        file_info = await upload_media_bytes(sender, image_bytes, 1, f'/v2/{prefix}/{target}/files')
        if not file_info:
            return False, '图片上传失败', {}
        media_payload: dict[str, Any] = {
            'msg_type': MessageType.MSG_TYPE_MEDIA,
            'msg_seq': random.randint(10000, 999999),
            'content': payload or '',
            'media': {'file_info': file_info},
        }
        if msg_id:
            media_payload['msg_id'] = msg_id
        ok, data = await sender.post_json(f'/v2/{prefix}/{target}/messages', media_payload)
        return ok, data, media_payload

    @classmethod
    async def _send_text(
        cls,
        sender: MessageSender,
        group_id: int | str | None,
        user_id: int | str | None,
        target: int | str,
        payload: str | dict[str, Any],
        msg_id: int | str | None,
    ) -> tuple[bool, Any, dict[str, Any]]:
        kwargs = PayloadConverter.convert(payload)
        if group_id:
            ok, data, send_payload = await sender.send_to_group(target, msg_id=msg_id, **kwargs)
        else:
            ok, data, send_payload = await sender.send_to_user(target, msg_id=msg_id, **kwargs)
        return ok, data, send_payload
