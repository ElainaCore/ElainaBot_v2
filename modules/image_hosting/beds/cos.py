"""腾讯云 COS 图床"""

import os
from datetime import datetime
from io import BytesIO

from ._common import (
    DIM_PATTERN,
    BaseBed,
    get_image_dimensions,
    guess_content_type,
    log,
    run_sync,
)


class Bed(BaseBed):
    name = 'cos'
    display_name = 'COS'
    priority = 10
    defaults = {
        'enabled': False,
        'region': 'ap-guangzhou',
        'secret_id': '',
        'secret_key': '',
        'bucket_name': '',
        'domain': '',
        'upload_path_prefix': 'elaina/',
        'max_file_size': 100 * 1024 * 1024,
    }
    comments = {
        '__desc__': '腾讯云 COS 对象存储配置',
        'enabled': '是否启用 COS 图床',
        'region': '存储桶所在地域, 如 ap-guangzhou',
        'secret_id': '腾讯云 API SecretId',
        'secret_key': '腾讯云 API SecretKey',
        'bucket_name': '存储桶名称, 如 mybucket-1250000000',
        'domain': '自定义域名, 留空使用默认域名',
        'upload_path_prefix': '上传路径前缀',
        'max_file_size': '最大文件大小 (字节), 默认 100MB',
    }

    __slots__ = ('_client', '_available')

    def __init__(self, cfg):
        super().__init__(cfg)
        self._client = None
        self._available = False

    def initialize(self):
        if not self._cfg.get('enabled'):
            return
        try:
            from qcloud_cos import CosConfig, CosS3Client
        except ImportError:
            log.error("qcloud-cos-v5 未安装 (pip install cos-python-sdk-v5)")
            return
        if not all([self._cfg.get('secret_id'), self._cfg.get('secret_key'),
                    self._cfg.get('bucket_name'), self._cfg.get('region')]):
            log.warning("COS 配置不完整, 已跳过")
            return
        try:
            config = CosConfig(
                Region=self._cfg['region'],
                SecretId=self._cfg['secret_id'],
                SecretKey=self._cfg['secret_key'],
                Scheme='https')
            self._client = CosS3Client(config)
            self._available = True
        except Exception as e:
            log.error(f"COS 初始化失败: {e}")

    def is_available(self):
        return self._available and self._client is not None

    async def upload(self, file_data, filename='image.png', user_id=None, custom_path=None):
        """上传到腾讯云COS, 返回 dict 或 (False, 原因)"""
        if not self._cfg.get('enabled'):
            return (False, 'COS 图床未开启, 请在 image_hosting 模块配置中启用')
        if not self.is_available():
            return (False, 'COS 图床未配置完整或初始化失败')
        return await run_sync(self._upload_sync, file_data, filename, user_id, custom_path)

    async def upload_url(self, file_data, filename='image.png', user_id=None, custom_path=None):
        """只返回 URL 字符串, 失败返回 (False, 原因)"""
        result = await self.upload(file_data, filename, user_id, custom_path)
        if isinstance(result, tuple):
            return result
        return result['file_url'] if result else (False, '上传失败')

    def _upload_sync(self, file_data, filename, user_id, custom_path):
        try:
            file_bytes = file_data.getvalue() if isinstance(file_data, BytesIO) else file_data
            if not isinstance(file_bytes, bytes):
                return (False, '无效的文件数据')
            if len(file_bytes) > int(self._cfg.get('max_file_size', 100 * 1024 * 1024)):
                return (False, f'文件过大: {len(file_bytes)} bytes')

            dim = get_image_dimensions(file_bytes) or (300, 300)
            cos_key = self._gen_key(filename, custom_path, user_id, dim)

            self._client.put_object(
                Bucket=self._cfg['bucket_name'],
                Body=BytesIO(file_bytes),
                Key=cos_key,
                ContentType=guess_content_type(filename))

            base_url = self._base_url()
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

    async def delete(self, cos_key):
        if not self.is_available():
            return False
        return await run_sync(self._delete_sync, cos_key)

    def _delete_sync(self, cos_key):
        try:
            self._client.delete_object(
                Bucket=self._cfg['bucket_name'], Key=cos_key)
            return True
        except Exception as e:
            log.warning(f"COS 删除失败 [{cos_key}]: {e}")
            return False

    def _base_url(self):
        if self._cfg.get('domain'):
            return f"https://{self._cfg['domain']}"
        return f"https://{self._cfg['bucket_name']}.cos.{self._cfg['region']}.myqcloud.com"

    def _gen_key(self, filename, custom_path, user_id, dim):
        if dim and not DIM_PATTERN.search(filename):
            name, ext = os.path.splitext(filename)
            filename = f"{name}_{dim[0]}x{dim[1]}{ext}"
        if custom_path:
            custom_path = custom_path.replace('\\', '/')
            if '/' in custom_path:
                return f"{custom_path.rsplit('/', 1)[0]}/{filename}"
            return filename
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        prefix = self._cfg.get('upload_path_prefix', 'mlog/')
        return f"{prefix}{user_id + '/' if user_id else ''}{ts}/{filename}".replace('\\', '/')
