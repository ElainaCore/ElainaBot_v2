"""星野图床 (免费, 无需配置)"""

from ._common import UA, BaseBed, detect_mime, run_sync

_SIGN_URL = 'https://bed-sign.vercel.0013107.xyz/sign'
_SIGN_ORIGIN = 'https://bed.vercel.0013107.xyz'


class Bed(BaseBed):
    name = 'xingye'
    display_name = '星野'
    priority = 50
    defaults = {
        'enabled': False,
    }
    comments = {
        '__desc__': '星野图床 (免费, 无需配置)',
        'enabled': '是否启用星野图床',
    }

    __slots__ = ()

    async def upload(self, image_data):
        """上传到星野图床, 返回 URL 字符串或 (False, 原因)"""
        if not self.is_available():
            return (False, '星野图床未开启, 请在 image_hosting 模块配置中启用')
        if not isinstance(image_data, bytes) or len(image_data) > 20 * 1024 * 1024:
            return (False, '无效数据或超过20MB限制')
        return await run_sync(self._upload_sync, image_data, 'xingye')

    def _upload_sync(self, image_data, module):
        """星野签名上传逻辑"""
        try:
            import httpx
            mime = detect_mime(image_data)
            ext = mime.split('/')[-1] if '/' in mime else 'jpg'
            filename = f'image.{ext}'
            sign_headers = {
                'Accept': '*/*',
                'Origin': _SIGN_ORIGIN,
                'Referer': f'{_SIGN_ORIGIN}/',
                'User-Agent': UA,
            }
            sign_resp = httpx.get(
                _SIGN_URL,
                params={'module': module, 'filename': filename, 'mimeType': mime},
                headers=sign_headers, timeout=15)
            sign_data = sign_resp.json()

            upload_url = sign_data.get('url')
            resource_url = sign_data.get('resourceUrl')
            if not upload_url or not resource_url:
                return (False, f'{module} 签名返回数据不完整')

            ct = (sign_data.get('header') or {}).get('Content-Type', mime)
            resp = httpx.put(upload_url, content=image_data,
                             headers={'Content-Type': ct, 'User-Agent': UA},
                             timeout=30)

            if resp.status_code < 300:
                # OSS 默认域名直链会强制下载, 附加图片处理参数使其内联显示
                return f'{resource_url}?x-oss-process=image/format,jpg'
            return (False, f'{module} 上传失败 (HTTP {resp.status_code})')
        except Exception as e:
            return (False, str(e))
