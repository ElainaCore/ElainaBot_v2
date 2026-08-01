"""B站图床"""

import contextlib
import os
import tempfile

from ._common import BaseBed, detect_mime, run_sync


class Bed(BaseBed):
    name = 'bilibili'
    display_name = 'B站'
    priority = 20
    defaults = {
        'enabled': False,
        'csrf_token': '',
        'sessdata': '',
        'bucket': 'openplatform',
    }
    comments = {
        '__desc__': 'B站图床配置',
        'enabled': '是否启用 B站图床',
        'csrf_token': 'B站 Cookie 中的 bili_jct 值',
        'sessdata': 'B站 Cookie 中的 SESSDATA 值',
        'bucket': '上传 bucket, 一般无需修改',
    }

    __slots__ = ()

    def is_available(self):
        return self._cfg.get('enabled') and self._cfg.get('csrf_token') and self._cfg.get('sessdata')

    async def upload(self, image_data):
        """上传到B站图床, 返回 URL 字符串或 (False, 原因)"""
        if not self._cfg.get('enabled'):
            return (False, 'B站图床未开启, 请在 image_hosting 模块配置中启用')
        csrf_token = self._cfg.get('csrf_token', '')
        sessdata = self._cfg.get('sessdata', '')
        if not csrf_token or not sessdata:
            return (False, 'B站图床未配置 csrf_token 或 sessdata')
        if not isinstance(image_data, bytes) or len(image_data) > 20 * 1024 * 1024:
            return (False, '无效数据或超过20MB限制')
        return await run_sync(self._upload_sync, image_data, csrf_token, sessdata)

    def _upload_sync(self, image_data, csrf_token, sessdata):
        temp_path = None
        try:
            mime_type = detect_mime(image_data)
            ext = mime_type.split('/')[-1] if '/' in mime_type else 'jpg'
            filename = f'image.{ext}'

            with tempfile.NamedTemporaryFile(delete=False, suffix=f'.{ext}') as f:
                f.write(image_data)
                temp_path = f.name

            import httpx
            with open(temp_path, 'rb') as fp:
                files = {'file': (filename, fp, mime_type)}
                resp = httpx.post(
                    'https://api.bilibili.com/x/upload/web/image',
                    files=files,
                    data={'bucket': self._cfg.get('bucket', 'openplatform'), 'csrf': csrf_token},
                    headers={
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                        'Cookie': f'SESSDATA={sessdata}; bili_jct={csrf_token}',
                    },
                    timeout=30)

            if resp.status_code == 200:
                data = resp.json()
                if data.get('code') == 0:
                    url = data.get('data', {}).get('location', '')
                    if url:
                        return url.replace('http://', 'https://') if url.startswith('http://') else url
                    return (False, 'B站返回成功但 location 为空')
                return (False, f"B站业务错误: code={data.get('code')} msg={data.get('message', '')}")
            return (False, f'B站上传失败 (HTTP {resp.status_code})')
        except Exception as e:
            return (False, str(e))
        finally:
            if temp_path and os.path.exists(temp_path):
                with contextlib.suppress(Exception):
                    os.unlink(temp_path)
