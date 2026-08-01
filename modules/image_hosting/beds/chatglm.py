"""智谱 ChatGLM 图床 (免费, 无需配置)"""

from ._common import UA, BaseBed, detect_mime, run_sync


class Bed(BaseBed):
    name = 'chatglm'
    display_name = 'ChatGLM'
    priority = 40
    defaults = {
        'enabled': False,
    }
    comments = {
        '__desc__': '智谱 ChatGLM 图床 (免费, 无需配置)',
        'enabled': '是否启用 ChatGLM 图床',
    }

    __slots__ = ()

    async def upload(self, image_data):
        """上传到智谱ChatGLM图床, 返回 URL 字符串或 (False, 原因)"""
        if not self.is_available():
            return (False, 'ChatGLM 图床未开启, 请在 image_hosting 模块配置中启用')
        if not isinstance(image_data, bytes) or len(image_data) > 20 * 1024 * 1024:
            return (False, '无效数据或超过20MB限制')
        return await run_sync(self._upload_sync, image_data)

    def _upload_sync(self, image_data):
        try:
            import httpx
            mime = detect_mime(image_data)
            ext = mime.split('/')[-1] if '/' in mime else 'jpg'
            resp = httpx.post(
                'https://chatglm.cn/chatglm/backend-api/assistant/file_upload',
                files={'file': (f'image.{ext}', image_data, mime)},
                headers={
                    'User-Agent': UA,
                    'Accept-Encoding': 'gzip, deflate, br',
                },
                timeout=30)
            if resp.status_code == 200:
                url = resp.json().get('result', {}).get('file_url', '')
                if url:
                    return url
            return (False, f'ChatGLM 上传失败 (HTTP {resp.status_code})')
        except Exception as e:
            return (False, str(e))
