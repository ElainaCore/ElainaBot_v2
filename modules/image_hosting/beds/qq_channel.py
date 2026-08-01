"""QQ频道图床"""

import contextlib
import hashlib
import os
import tempfile

from ._common import BaseBed, detect_mime, run_sync


class Bed(BaseBed):
    name = 'qq_channel'
    display_name = 'QQ频道'
    priority = 30
    defaults = {
        'enabled': False,
        'channel_id': '',
    }
    comments = {
        '__desc__': 'QQ频道图床配置',
        'enabled': '是否启用 QQ频道图床',
        'channel_id': '用于上传图片的子频道 ID',
    }

    __slots__ = ()

    def is_available(self):
        return self._cfg.get('enabled') and self._cfg.get('channel_id')

    async def upload(self, image_data, token_manager=None):
        """上传到QQ频道图床, 返回 URL 字符串或 (False, 原因)

        token_manager: TokenManager 实例 (用于获取 access_token)
        """
        if not self._cfg.get('enabled'):
            return (False, 'QQ频道图床未开启, 请在 image_hosting 模块配置中启用')
        channel_id = self._cfg.get('channel_id', '')
        if not channel_id:
            return (False, 'QQ频道图床未配置 channel_id')
        if not isinstance(image_data, bytes):
            return (False, '无效的图片数据')
        if not token_manager:
            return (False, '需要传入 token_manager 以获取 access_token')

        access_token = await token_manager.get_token()
        if not access_token:
            return (False, '获取 access_token 失败')

        return await run_sync(self._upload_sync, image_data, channel_id, access_token)

    def _upload_sync(self, image_data, channel_id, access_token):
        md5hash = hashlib.md5(image_data).hexdigest().upper()
        temp_path = None
        try:
            mime_type = detect_mime(image_data)
            ext = mime_type.split('/')[-1] if '/' in mime_type else 'jpg'

            with tempfile.NamedTemporaryFile(delete=False, suffix=f'.{ext}') as f:
                f.write(image_data)
                temp_path = f.name

            import httpx
            with open(temp_path, 'rb') as fp:
                files = {'file_image': (f'image.{ext}', fp, mime_type)}
                httpx.post(
                    f'https://api.sgroup.qq.com/channels/{channel_id}/messages',
                    files=files,
                    data={'msg_id': '1'},
                    headers={'Authorization': f'QQBot {access_token}'},
                    timeout=30)

            return f'https://gchat.qpic.cn/qmeetpic/0/0-0-{md5hash}/0'
        except Exception as e:
            return (False, str(e))
        finally:
            if temp_path and os.path.exists(temp_path):
                with contextlib.suppress(Exception):
                    os.unlink(temp_path)
