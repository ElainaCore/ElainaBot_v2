"""Nature 图床 (腾讯 COS 直传, 密钥内置, 仅图片)"""

import hashlib
import hmac
import os
from base64 import b64decode as _d
from datetime import datetime

from ._common import BaseBed, run_sync

_SECRET_ID = _d(b'QUtJRHJiOFRiZlhBWnJ5cVRzMnlnQlNWSkdzSFRROGR0d21O').decode()
_SECRET_KEY = _d(b'UFphTnhLV2ZjTHAzNHJQanJ1dGtXRnlaQ2N5REdCMGQ=').decode()
_BUCKET = 'sgame-data-service-1252931805'
_REGION = 'ap-nanjing'
_CDN = 'https://download.nature.qq.com'
_PATH_PREFIX = 'SnsShare/SocialProfile'


class Bed(BaseBed):
    name = 'nature'
    display_name = 'Nature'
    priority = 60
    defaults = {
        'enabled': True,
    }
    comments = {
        '__desc__': 'Nature 图床 (腾讯 COS 直传, 密钥内置, 仅图片; 临时图片可用, 不建议持久化)',
        'enabled': '是否启用 Nature 图床 (默认开启)',
    }

    __slots__ = ()

    async def upload(self, image_data):
        """上传到 Nature 图床 (腾讯COS直传), 返回 URL 字符串或 (False, 原因)"""
        if not self.is_available():
            return (False, 'Nature 图床未开启, 请在 image_hosting 模块配置中启用')
        if not isinstance(image_data, bytes) or len(image_data) > 100 * 1024 * 1024:
            return (False, '无效数据或超过100MB限制')
        return await run_sync(self._upload_sync, image_data)

    def _upload_sync(self, image_data):
        try:
            import httpx
            mime, ext = _detect_nature_mime(image_data)
            if not mime:
                return (False, '仅支持 PNG/JPG/WebP/GIF 格式')
            content_type = 'image/jpeg' if mime == 'image/gif' else mime

            ts = int(datetime.now().timestamp())
            rand = os.urandom(4).hex()
            upload_path = f'{_PATH_PREFIX}/{ts}_{rand}.{ext}'
            host = f'{_BUCKET}.cos.{_REGION}.myqcloud.com'

            sign_time = f'{ts};{ts + 3600}'
            sign_key = hmac.new(
                _SECRET_KEY.encode(), sign_time.encode(), 'sha1').hexdigest()
            fmt = f'put\n/{upload_path}\n\nhost={host}\n'
            sts = f'sha1\n{sign_time}\n{hashlib.sha1(fmt.encode()).hexdigest()}\n'
            sig = hmac.new(sign_key.encode(), sts.encode(), 'sha1').hexdigest()
            auth = (f'q-sign-algorithm=sha1&q-ak={_SECRET_ID}'
                    f'&q-sign-time={sign_time}&q-key-time={sign_time}'
                    f'&q-header-list=host&q-url-param-list=&q-signature={sig}')

            resp = httpx.put(
                f'https://{host}/{upload_path}', content=image_data,
                headers={'Host': host, 'Content-Type': content_type,
                         'Authorization': auth},
                timeout=30)
            if resp.status_code == 200:
                return f'{_CDN}/{upload_path}'
            return (False, f'Nature 上传失败 (HTTP {resp.status_code})')
        except Exception as e:
            return (False, str(e))


def _detect_nature_mime(data):
    """检测图片类型 (仅 PNG/JPG/WebP/GIF), 返回 (mime, ext)"""
    if data[:8].startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png', 'png'
    if data[:3] == b'\xff\xd8\xff':
        return 'image/jpeg', 'jpg'
    if len(data) >= 12 and data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp', 'webp'
    if data[:3] == b'GIF':
        return 'image/gif', 'jpg'
    return None, None
