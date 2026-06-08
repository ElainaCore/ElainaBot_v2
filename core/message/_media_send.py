"""媒体发送 / 保存 Mixin — URL 下载、本地保存、媒体载荷发送"""

import asyncio
import hashlib
import os

from core.base.logger import FRAMEWORK, get_logger
from core.message._http import (
    _MAX_MEDIA_DOWNLOAD,
    MessageType,
)
from core.message.media import _resolve_upload_ep, upload_media_bytes

log = get_logger(FRAMEWORK, '消息发送')


def _msg_seq():
    import random

    return random.randint(10000, 999999)


class _MediaSendMixin:
    """媒体发送 Mixin"""

    _MEDIA_TYPE_NAMES = {1: '图片', 2: '视频', 3: '语音', 4: '文件'}
    _MEDIA_TYPE_EXTS = {1: '.png', 2: '.mp4', 3: '.mp3', 4: '.dat'}

    def _maybe_auto_recall(self, event, data, delay):
        if delay and data:
            mid = _extract_message_id(data)
            if mid:
                asyncio.create_task(self._auto_recall(event, mid, delay))

    async def _send_media(
        self,
        event,
        data,
        file_type,
        content,
        *,
        file_name=None,
        auto_delete_time=None,
        target_user_id=None,
        target_group_id=None,
        msg_id=None,
    ):
        upload_ep = _resolve_upload_ep(target_group_id, target_user_id, event)
        if not upload_ep:
            return None

        # 网络地址: 直接记录 URL, 不保存到本地
        is_url = isinstance(data, str) and data.startswith(('http://', 'https://'))
        original_url = data if is_url else None

        if is_url:
            try:
                client = await self._ensure_client()
                resp = await client.get(data)
                cl = int(resp.headers.get('content-length', 0))
                if cl > _MAX_MEDIA_DOWNLOAD:
                    log.warning(f'[{self._appid}] 媒体过大 ({cl} bytes), 跳过下载')
                    return None
                body = resp.content
                if len(body) > _MAX_MEDIA_DOWNLOAD:
                    log.warning(f'[{self._appid}] 媒体实际大小超限 ({len(body)} bytes), 丢弃')
                    del body
                    return None
                data = body
            except Exception as e:
                log.warning(f'[{self._appid}] 下载媒体失败: {e}')
                return None
        if not isinstance(data, bytes):
            return None

        type_name = self._MEDIA_TYPE_NAMES.get(file_type, '媒体')
        if original_url:
            media_label = f'[{type_name}]{original_url}'
        else:
            media_label = await self._save_media(data, file_type)

        file_info = await upload_media_bytes(self, data, file_type, upload_ep, file_name=file_name)
        if not file_info:
            return None
        return await self._send_media_payload(
            event,
            file_info,
            content,
            auto_delete_time,
            target_user_id=target_user_id,
            target_group_id=target_group_id,
            msg_id=msg_id,
            media_label=media_label,
        )

    async def _save_media(self, data, file_type):
        """保存到 data/media/, MD5 去重"""
        type_name = self._MEDIA_TYPE_NAMES.get(file_type, '媒体')
        if not self._media_dir:
            return f'[{type_name}]'
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, self._save_media_sync, data, file_type, type_name)
        except Exception as e:
            log.debug(f'[媒体保存] {e}')
            return f'[{type_name}]'

    def _save_media_sync(self, data, file_type, type_name):
        ext = self._MEDIA_TYPE_EXTS.get(file_type, '.dat')
        md5 = hashlib.md5(data).hexdigest()
        filename = f'{md5}{ext}'
        filepath = os.path.join(self._media_dir, filename)
        if not os.path.exists(filepath):
            self._write_file_sync(filepath, data)
        return f'[{type_name}]/api/media/{filename}'

    @staticmethod
    def _read_file_sync(path):
        with open(path, 'rb') as f:
            return f.read()

    @staticmethod
    def _write_file_sync(path, data):
        with open(path, 'wb') as f:
            f.write(data)

    async def _send_media_payload(
        self,
        event,
        file_info,
        content,
        auto_delete_time=None,
        *,
        target_user_id=None,
        target_group_id=None,
        msg_id=None,
        media_label='',
    ):
        proactive = bool(target_user_id or target_group_id)
        payload = {
            'msg_type': MessageType.MSG_TYPE_MEDIA,
            'msg_seq': _msg_seq(),
            'content': content or '',
            'media': {'file_info': file_info},
        }
        if not proactive:
            _set_msg_or_event_id(payload, event)
        elif msg_id:
            payload['msg_id'] = msg_id
        if target_group_id:
            endpoint = f'/v2/groups/{target_group_id}/messages'
        elif target_user_id:
            endpoint = f'/v2/users/{target_user_id}/messages'
        else:
            endpoint = event.reply_endpoint
        if not endpoint:
            return None
        success, data = await self._send_with_error_handling(endpoint, payload, event, content, media_label=media_label)
        if success:
            self._maybe_auto_recall(event, data, auto_delete_time)
        return data

    async def _auto_recall(self, event, message_id, delay):
        try:
            await asyncio.sleep(delay)
            await self.recall(event, message_id)
        except Exception:
            pass


# ==================== 模块级辅助 ====================


def _set_msg_or_event_id(payload, event):
    if event.needs_msg_id and event.message_id:
        payload['msg_id'] = event.message_id
    elif event.needs_event_id:
        payload['event_id'] = event.event_id or ''


def _extract_message_id(data):
    if isinstance(data, dict):
        return data.get('id') or data.get('msg_id') or data.get('message_id')
    return None
