"""可选模块: 统一图床服务 (腾讯云COS / B站 / QQ频道 / ChatGLM / Ukaka / 星野 / Nature)

各图床可在配置中独立开关, 未开启或未配置时返回明确提示。
同步 SDK 通过 run_in_executor 包装为异步 API。

用法 (插件中):
    hosting = bot.module_manager.get("image_hosting")
    if hosting:
        url = await hosting.upload_cos(image_bytes, "test.png", user_id="abc")
        url = await hosting.upload_bilibili(image_bytes)
        url = await hosting.upload_qq(image_bytes)
        url = await hosting.upload_chatglm(image_bytes)
        url = await hosting.upload_ukaka(image_bytes)
        url = await hosting.upload_xingye(image_bytes)
        url = await hosting.upload_nature(image_bytes)

配置 (modules/image_hosting/data/config.yaml):
    cos:
        enabled: false
        region: ap-guangzhou
        secret_id: ""
        secret_key: ""
        bucket_name: ""
        domain: ""
        upload_path_prefix: "elaina/"
        max_file_size: 104857600
    bilibili:
        enabled: false
        csrf_token: ""
        sessdata: ""
        bucket: "openplatform"
    qq_channel:
        enabled: false
        channel_id: ""
    chatglm:
        enabled: false
    ukaka:
        enabled: false
    xingye:
        enabled: false
    nature:
        enabled: false
"""

__module_meta__ = {
    'name': '图床服务',
    'description': '统一图床上传 (COS / B站 / QQ频道 / ChatGLM / Ukaka / 星野 / Nature)',
    'version': '1.2.0',
    'author': 'ElainaBot',
}

import asyncio
import contextlib
import hashlib
import hmac
import mimetypes
import os
import re
import tempfile
from base64 import b64decode as _d
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from io import BytesIO

from core.base.logger import EXTENSION, get_logger

log = get_logger(EXTENSION, "图床服务")

_instance = None
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='img_host')

_NATURE_SECRET_ID = _d(b'QUtJRHJiOFRiZlhBWnJ5cVRzMnlnQlNWSkdzSFRROGR0d21O').decode()
_NATURE_SECRET_KEY = _d(b'UFphTnhLV2ZjTHAzNHJQanJ1dGtXRnlaQ2N5REdCMGQ=').decode()
_NATURE_BUCKET = 'sgame-data-service-1252931805'
_NATURE_REGION = 'ap-nanjing'
_NATURE_CDN = 'https://download.nature.qq.com'
_NATURE_PATH_PREFIX = 'SnsShare/SocialProfile'

_DEFAULTS = {
    'cos': {
        'enabled': False,
        'region': 'ap-guangzhou',
        'secret_id': '',
        'secret_key': '',
        'bucket_name': '',
        'domain': '',
        'upload_path_prefix': 'elaina/',
        'max_file_size': 100 * 1024 * 1024,
    },
    'bilibili': {
        'enabled': False,
        'csrf_token': '',
        'sessdata': '',
        'bucket': 'openplatform',
    },
    'qq_channel': {
        'enabled': False,
        'channel_id': '',
    },
    'chatglm': {
        'enabled': False,
    },
    'ukaka': {
        'enabled': False,
    },
    'xingye': {
        'enabled': False,
    },
    'nature': {
        'enabled': False,
    },
}

_COMMENTS = {
    'cos': {
        '__desc__': '腾讯云 COS 对象存储配置',
        'enabled': '是否启用 COS 图床',
        'region': '存储桶所在地域, 如 ap-guangzhou',
        'secret_id': '腾讯云 API SecretId',
        'secret_key': '腾讯云 API SecretKey',
        'bucket_name': '存储桶名称, 如 mybucket-1250000000',
        'domain': '自定义域名, 留空使用默认域名',
        'upload_path_prefix': '上传路径前缀',
        'max_file_size': '最大文件大小 (字节), 默认 100MB',
    },
    'bilibili': {
        '__desc__': 'B站图床配置',
        'enabled': '是否启用 B站图床',
        'csrf_token': 'B站 Cookie 中的 bili_jct 值',
        'sessdata': 'B站 Cookie 中的 SESSDATA 值',
        'bucket': '上传 bucket, 一般无需修改',
    },
    'qq_channel': {
        '__desc__': 'QQ频道图床配置',
        'enabled': '是否启用 QQ频道图床',
        'channel_id': '用于上传图片的子频道 ID',
    },
    'chatglm': {
        '__desc__': '智谱 ChatGLM 图床 (免费, 无需配置)',
        'enabled': '是否启用 ChatGLM 图床',
    },
    'ukaka': {
        '__desc__': 'Ukaka 图床 (免费, 无需配置)',
        'enabled': '是否启用 Ukaka 图床',
    },
    'xingye': {
        '__desc__': '星野图床 (免费, 无需配置)',
        'enabled': '是否启用星野图床',
    },
    'nature': {
        '__desc__': 'Nature 图床 (腾讯 COS 直传, 密钥内置, 仅图片)',
        'enabled': '是否启用 Nature 图床',
    },
}

_DIM_PATTERN = re.compile(r'_(\d+)x(\d+)\.[^.]+$')


# ==================== 模块入口 ====================

async def setup(ctx):
    global _instance, _executor
    _executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='img_host')
    cfg = ctx.ensure_config(_DEFAULTS, comments=_COMMENTS)
    _instance = ImageHosting(cfg, ctx)
    _instance.initialize()
    return _instance


async def teardown():
    global _instance
    _instance = None
    _executor.shutdown(wait=False)


# ==================== 统一图床服务 ====================

class ImageHosting:
    """统一图床上传 (COS / B站 / QQ频道 / ChatGLM / Ukaka / 星野 / Nature)"""

    __slots__ = ('_cfg', '_ctx', '_cos_client', '_cos_available', '_sign_url', '_sign_origin', '_ua')

    def __init__(self, cfg, ctx):
        self._cfg = cfg
        self._ctx = ctx
        self._cos_client = None
        self._cos_available = False
        self._sign_url = 'https://bed-sign.vercel.0013107.xyz/sign'
        self._sign_origin = 'https://bed.vercel.0013107.xyz'
        self._ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36'

    def initialize(self):
        """初始化各图床 (仅 COS 需要预初始化)"""
        cos_cfg = self._cfg.get('cos', {})
        if cos_cfg.get('enabled'):
            self._init_cos(cos_cfg)
        bili_cfg = self._cfg.get('bilibili', {})
        qq_cfg = self._cfg.get('qq_channel', {})
        status = []
        status.append(f"COS={'✅' if self._cos_available else '❌'}")
        status.append(f"B站={'✅' if bili_cfg.get('enabled') and bili_cfg.get('csrf_token') and bili_cfg.get('sessdata') else '❌'}")
        status.append(f"QQ频道={'✅' if qq_cfg.get('enabled') and qq_cfg.get('channel_id') else '❌'}")
        status.append(f"ChatGLM={'✅' if self._cfg.get('chatglm', {}).get('enabled') else '❌'}")
        status.append(f"Ukaka={'✅' if self._cfg.get('ukaka', {}).get('enabled') else '❌'}")
        status.append(f"星野={'✅' if self._cfg.get('xingye', {}).get('enabled') else '❌'}")
        status.append(f"Nature={'✅' if self._cfg.get('nature', {}).get('enabled') else '❌'}")
        log.info(f"图床状态: {' | '.join(status)}")

    def _init_cos(self, cos_cfg):
        try:
            from qcloud_cos import CosConfig, CosS3Client
        except ImportError:
            log.error("qcloud-cos-v5 未安装 (pip install cos-python-sdk-v5)")
            return
        if not all([cos_cfg.get('secret_id'), cos_cfg.get('secret_key'),
                    cos_cfg.get('bucket_name'), cos_cfg.get('region')]):
            log.warning("COS 配置不完整, 已跳过")
            return
        try:
            config = CosConfig(
                Region=cos_cfg['region'],
                SecretId=cos_cfg['secret_id'],
                SecretKey=cos_cfg['secret_key'],
                Scheme='https')
            self._cos_client = CosS3Client(config)
            self._cos_available = True
        except Exception as e:
            log.error(f"COS 初始化失败: {e}")

    # ==================== 状态查询 ====================

    def is_cos_available(self):
        return self._cos_available and self._cos_client is not None

    def is_bilibili_available(self):
        bili = self._cfg.get('bilibili', {})
        return bili.get('enabled') and bili.get('csrf_token') and bili.get('sessdata')

    def is_qq_available(self):
        qq = self._cfg.get('qq_channel', {})
        return qq.get('enabled') and qq.get('channel_id')

    def is_chatglm_available(self):
        return self._cfg.get('chatglm', {}).get('enabled', False)

    def is_ukaka_available(self):
        return self._cfg.get('ukaka', {}).get('enabled', False)

    def is_xingye_available(self):
        return self._cfg.get('xingye', {}).get('enabled', False)

    def is_nature_available(self):
        return self._cfg.get('nature', {}).get('enabled', False)

    def status(self):
        """返回各图床状态 dict"""
        return {
            'cos': self.is_cos_available(),
            'bilibili': bool(self.is_bilibili_available()),
            'qq_channel': bool(self.is_qq_available()),
            'chatglm': self.is_chatglm_available(),
            'ukaka': self.is_ukaka_available(),
            'xingye': self.is_xingye_available(),
            'nature': self.is_nature_available(),
        }

    # ==================== COS 上传 ====================

    async def upload_cos(self, file_data, filename, user_id=None, custom_path=None):
        """上传到腾讯云COS, 返回 dict 或 (False, 原因)"""
        cos_cfg = self._cfg.get('cos', {})
        if not cos_cfg.get('enabled'):
            return (False, 'COS 图床未开启, 请在 image_hosting 模块配置中启用')
        if not self.is_cos_available():
            return (False, 'COS 图床未配置完整或初始化失败')
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            _executor, self._upload_cos_sync, file_data, filename, user_id, custom_path)

    async def upload_cos_url(self, file_data, filename, user_id=None, custom_path=None):
        """只返回 URL 字符串, 失败返回 (False, 原因)"""
        result = await self.upload_cos(file_data, filename, user_id, custom_path)
        if isinstance(result, tuple):
            return result
        return result['file_url'] if result else (False, '上传失败')

    def _upload_cos_sync(self, file_data, filename, user_id, custom_path):
        cos_cfg = self._cfg['cos']
        try:
            file_bytes = file_data.getvalue() if isinstance(file_data, BytesIO) else file_data
            if not isinstance(file_bytes, bytes):
                return (False, '无效的文件数据')
            if len(file_bytes) > int(cos_cfg.get('max_file_size', 100 * 1024 * 1024)):
                return (False, f'文件过大: {len(file_bytes)} bytes')

            dim = _get_image_dimensions(file_bytes) or (300, 300)
            cos_key = self._gen_cos_key(filename, custom_path, user_id, dim)

            self._cos_client.put_object(
                Bucket=cos_cfg['bucket_name'],
                Body=BytesIO(file_bytes),
                Key=cos_key,
                ContentType=_guess_content_type(filename))

            base_url = self._cos_base_url()
            return {
                'success': True,
                'cos_key': cos_key,
                'file_url': f"{base_url}/{cos_key}",
                'filename': os.path.basename(cos_key),
                'file_size': len(file_bytes),
                'width': dim[0], 'height': dim[1],
                'px': f'#{dim[0]}px #{dim[1]}px',
            }
        except Exception as e:
            log.error(f"COS 上传失败: {e}")
            return (False, str(e))

    async def delete_cos(self, cos_key):
        if not self.is_cos_available():
            return False
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_executor, self._delete_cos_sync, cos_key)

    def _delete_cos_sync(self, cos_key):
        try:
            self._cos_client.delete_object(
                Bucket=self._cfg['cos']['bucket_name'], Key=cos_key)
            return True
        except Exception as e:
            log.warning(f"COS 删除失败 [{cos_key}]: {e}")
            return False

    def _cos_base_url(self):
        cos_cfg = self._cfg['cos']
        if cos_cfg.get('domain'):
            return f"https://{cos_cfg['domain']}"
        return f"https://{cos_cfg['bucket_name']}.cos.{cos_cfg['region']}.myqcloud.com"

    def _gen_cos_key(self, filename, custom_path, user_id, dim):
        cos_cfg = self._cfg['cos']
        if dim and not _DIM_PATTERN.search(filename):
            name, ext = os.path.splitext(filename)
            filename = f"{name}_{dim[0]}x{dim[1]}{ext}"
        if custom_path:
            custom_path = custom_path.replace('\\', '/')
            if '/' in custom_path:
                return f"{custom_path.rsplit('/', 1)[0]}/{filename}"
            return filename
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        prefix = cos_cfg.get('upload_path_prefix', 'mlog/')
        return f"{prefix}{user_id + '/' if user_id else ''}{ts}/{filename}".replace('\\', '/')

    # ==================== B站图床 ====================

    async def upload_bilibili(self, image_data):
        """上传到B站图床, 返回 URL 字符串或 (False, 原因)"""
        bili_cfg = self._cfg.get('bilibili', {})
        if not bili_cfg.get('enabled'):
            return (False, 'B站图床未开启, 请在 image_hosting 模块配置中启用')
        csrf_token = bili_cfg.get('csrf_token', '')
        sessdata = bili_cfg.get('sessdata', '')
        if not csrf_token or not sessdata:
            return (False, 'B站图床未配置 csrf_token 或 sessdata')
        if not isinstance(image_data, bytes) or len(image_data) > 20 * 1024 * 1024:
            return (False, '无效数据或超过20MB限制')
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            _executor, self._upload_bilibili_sync, image_data, csrf_token, sessdata, bili_cfg)

    def _upload_bilibili_sync(self, image_data, csrf_token, sessdata, bili_cfg):
        temp_path = None
        try:
            mime_type = _detect_mime(image_data)
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
                    data={'bucket': bili_cfg.get('bucket', 'openplatform'), 'csrf': csrf_token},
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

    # ==================== QQ频道图床 ====================

    async def upload_qq(self, image_data, token_manager=None):
        """上传到QQ频道图床, 返回 URL 字符串或 (False, 原因)

        token_manager: TokenManager 实例 (用于获取 access_token)
        """
        qq_cfg = self._cfg.get('qq_channel', {})
        if not qq_cfg.get('enabled'):
            return (False, 'QQ频道图床未开启, 请在 image_hosting 模块配置中启用')
        channel_id = qq_cfg.get('channel_id', '')
        if not channel_id:
            return (False, 'QQ频道图床未配置 channel_id')
        if not isinstance(image_data, bytes):
            return (False, '无效的图片数据')
        if not token_manager:
            return (False, '需要传入 token_manager 以获取 access_token')

        access_token = await token_manager.get_token()
        if not access_token:
            return (False, '获取 access_token 失败')

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            _executor, self._upload_qq_sync, image_data, channel_id, access_token)

    def _upload_qq_sync(self, image_data, channel_id, access_token):
        md5hash = hashlib.md5(image_data).hexdigest().upper()
        temp_path = None
        try:
            mime_type = _detect_mime(image_data)
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


    # ==================== ChatGLM 图床 ====================

    async def upload_chatglm(self, image_data):
        """上传到智谱ChatGLM图床, 返回 URL 字符串或 (False, 原因)"""
        if not self.is_chatglm_available():
            return (False, 'ChatGLM 图床未开启, 请在 image_hosting 模块配置中启用')
        if not isinstance(image_data, bytes) or len(image_data) > 20 * 1024 * 1024:
            return (False, '无效数据或超过20MB限制')
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_executor, self._upload_chatglm_sync, image_data)

    def _upload_chatglm_sync(self, image_data):
        try:
            import httpx
            mime = _detect_mime(image_data)
            ext = mime.split('/')[-1] if '/' in mime else 'jpg'
            resp = httpx.post(
                'https://chatglm.cn/chatglm/backend-api/assistant/file_upload',
                files={'file': (f'image.{ext}', image_data, mime)},
                headers={
                    'User-Agent': self._ua,
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

    # ==================== Ukaka 图床 ====================

    async def upload_ukaka(self, image_data):
        """上传到Ukaka图床, 返回 URL 字符串或 (False, 原因)"""
        if not self.is_ukaka_available():
            return (False, 'Ukaka 图床未开启, 请在 image_hosting 模块配置中启用')
        if not isinstance(image_data, bytes) or len(image_data) > 20 * 1024 * 1024:
            return (False, '无效数据或超过20MB限制')
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_executor, self._upload_signed_sync, image_data, 'ukaka')

    # ==================== 星野图床 ====================

    async def upload_xingye(self, image_data):
        """上传到星野图床, 返回 URL 字符串或 (False, 原因)"""
        if not self.is_xingye_available():
            return (False, '星野图床未开启, 请在 image_hosting 模块配置中启用')
        if not isinstance(image_data, bytes) or len(image_data) > 20 * 1024 * 1024:
            return (False, '无效数据或超过20MB限制')
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_executor, self._upload_signed_sync, image_data, 'xingye')

    def _upload_signed_sync(self, image_data, module):
        """Ukaka / 星野 共用签名上传逻辑"""
        try:
            import httpx
            mime = _detect_mime(image_data)
            ext = mime.split('/')[-1] if '/' in mime else 'jpg'
            filename = f'image.{ext}'
            sign_headers = {
                'Accept': '*/*',
                'Origin': self._sign_origin,
                'Referer': f'{self._sign_origin}/',
                'User-Agent': self._ua,
            }
            sign_resp = httpx.get(
                self._sign_url,
                params={'module': module, 'filename': filename, 'mimeType': mime},
                headers=sign_headers, timeout=15)
            sign_data = sign_resp.json()

            upload_url = sign_data.get('url')
            resource_url = sign_data.get('resourceUrl')
            if not upload_url or not resource_url:
                return (False, f'{module} 签名返回数据不完整')

            if module == 'xingye':
                ct = (sign_data.get('header') or {}).get('Content-Type', mime)
                resp = httpx.put(upload_url, content=image_data,
                                 headers={'Content-Type': ct, 'User-Agent': self._ua},
                                 timeout=30)
            else:
                body = sign_data.get('body', {})
                files_dict = {}
                data_dict = {k: str(v) for k, v in body.items()
                             if k != 'file' and v is not None and v != ''}
                files_dict['file'] = (filename, image_data, mime)
                resp = httpx.post(upload_url, data=data_dict, files=files_dict,
                                  headers={'User-Agent': self._ua}, timeout=30)

            if resp.status_code < 300:
                return resource_url
            return (False, f'{module} 上传失败 (HTTP {resp.status_code})')
        except Exception as e:
            return (False, str(e))

    # ==================== Nature 图床 ====================

    async def upload_nature(self, image_data):
        """上传到 Nature 图床 (腾讯COS直传), 返回 URL 字符串或 (False, 原因)"""
        if not self.is_nature_available():
            return (False, 'Nature 图床未开启, 请在 image_hosting 模块配置中启用')
        if not isinstance(image_data, bytes) or len(image_data) > 100 * 1024 * 1024:
            return (False, '无效数据或超过100MB限制')
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_executor, self._upload_nature_sync, image_data)

    def _upload_nature_sync(self, image_data):
        try:
            import httpx
            mime, ext = _detect_nature_mime(image_data)
            if not mime:
                return (False, '仅支持 PNG/JPG/WebP/GIF 格式')
            content_type = 'image/jpeg' if mime == 'image/gif' else mime

            ts = int(datetime.now().timestamp())
            rand = os.urandom(4).hex()
            upload_path = f'{_NATURE_PATH_PREFIX}/{ts}_{rand}.{ext}'
            host = f'{_NATURE_BUCKET}.cos.{_NATURE_REGION}.myqcloud.com'

            sign_time = f'{ts};{ts + 3600}'
            sign_key = hmac.new(
                _NATURE_SECRET_KEY.encode(), sign_time.encode(), 'sha1').hexdigest()
            fmt = f'put\n/{upload_path}\n\nhost={host}\n'
            sts = f'sha1\n{sign_time}\n{hashlib.sha1(fmt.encode()).hexdigest()}\n'
            sig = hmac.new(sign_key.encode(), sts.encode(), 'sha1').hexdigest()
            auth = (f'q-sign-algorithm=sha1&q-ak={_NATURE_SECRET_ID}'
                    f'&q-sign-time={sign_time}&q-key-time={sign_time}'
                    f'&q-header-list=host&q-url-param-list=&q-signature={sig}')

            resp = httpx.put(
                f'https://{host}/{upload_path}', content=image_data,
                headers={'Host': host, 'Content-Type': content_type,
                         'Authorization': auth},
                timeout=30)
            if resp.status_code == 200:
                return f'{_NATURE_CDN}/{upload_path}'
            return (False, f'Nature 上传失败 (HTTP {resp.status_code})')
        except Exception as e:
            return (False, str(e))


# ==================== 辅助函数 ====================

def _guess_content_type(filename):
    ct, _ = mimetypes.guess_type(filename)
    return ct or 'application/octet-stream'


def _get_image_dimensions(file_bytes):
    """从 bytes 读取图片尺寸 -> (w, h) 或 None"""
    try:
        from PIL import Image
        with Image.open(BytesIO(file_bytes)) as img:
            return img.size
    except Exception:
        return None


def _detect_mime(data):
    """检测图片 MIME 类型"""
    try:
        import magic
        return magic.Magic(mime=True).from_buffer(data)
    except Exception:
        return 'image/jpeg'


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


def parse_dimensions_from_filename(filename):
    """从文件名提取 _WxH 尺寸"""
    m = _DIM_PATTERN.search(filename)
    return (int(m.group(1)), int(m.group(2))) if m else None
