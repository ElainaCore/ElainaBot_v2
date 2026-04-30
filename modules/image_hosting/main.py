"""可选模块: 统一图床服务 (腾讯云COS / B站 / QQ频道)

各图床可在配置中独立开关, 未开启或未配置时返回明确提示。
同步 SDK 通过 run_in_executor 包装为异步 API。

用法 (插件中):
    hosting = bot.module_manager.get("image_hosting")
    if hosting:
        url = await hosting.upload_cos(image_bytes, "test.png", user_id="abc")
        url = await hosting.upload_bilibili(image_bytes)
        url = await hosting.upload_qq(image_bytes)

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
"""

import os
import re
import asyncio
import hashlib
import mimetypes
import tempfile
from datetime import datetime
from io import BytesIO
from core.base.logger import get_logger, EXTENSION

log = get_logger(EXTENSION, "图床服务")

_instance = None

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
}

_DIM_PATTERN = re.compile(r'_(\d+)x(\d+)\.[^.]+$')


# ==================== 模块入口 ====================

async def setup(ctx):
    global _instance
    cfg = ctx.ensure_config(_DEFAULTS, comments=_COMMENTS)
    _instance = ImageHosting(cfg, ctx)
    _instance.initialize()
    return _instance


async def teardown():
    global _instance
    _instance = None


# ==================== 统一图床服务 ====================

class ImageHosting:
    """统一图床上传 (COS / B站 / QQ频道), 各图床可独立开关"""

    __slots__ = ('_cfg', '_ctx', '_cos_client', '_cos_available')

    def __init__(self, cfg, ctx):
        self._cfg = cfg
        self._ctx = ctx
        self._cos_client = None
        self._cos_available = False

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
            log.info(f"COS 已就绪 [{cos_cfg['region']}/{cos_cfg['bucket_name']}]")
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

    def status(self):
        """返回各图床状态 dict"""
        return {
            'cos': self.is_cos_available(),
            'bilibili': bool(self.is_bilibili_available()),
            'qq_channel': bool(self.is_qq_available()),
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
            None, self._upload_cos_sync, file_data, filename, user_id, custom_path)

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
        return await loop.run_in_executor(None, self._delete_cos_sync, cos_key)

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
            None, self._upload_bilibili_sync, image_data, csrf_token, sessdata, bili_cfg)

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
            return (False, f'B站上传失败 (HTTP {resp.status_code})')
        except Exception as e:
            return (False, str(e))
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except Exception:
                    pass

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
            None, self._upload_qq_sync, image_data, channel_id, access_token)

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
                try:
                    os.unlink(temp_path)
                except Exception:
                    pass


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


def parse_dimensions_from_filename(filename):
    """从文件名提取 _WxH 尺寸"""
    m = _DIM_PATTERN.search(filename)
    return (int(m.group(1)), int(m.group(2))) if m else None
