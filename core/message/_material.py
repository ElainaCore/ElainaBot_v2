"""Markdown 图片素材管理（QQ Bot API 2026-10-21）。"""

import asyncio
import hashlib
import os
import re
from pathlib import Path
from urllib.parse import quote, urlsplit

from core.message._http import _describe_exception

MAX_IMAGE_SIZE = 10 * 1024 * 1024
MAX_MEDIA_IMAGES = 100
_IMAGE_TYPES = {
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.webp': 'image/webp',
}


def _image_type(data):
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return '.png'
    if data.startswith(b'\xff\xd8\xff'):
        return '.jpg'
    if data.startswith((b'GIF87a', b'GIF89a')):
        return '.gif'
    if data.startswith(b'RIFF') and data[8:12] == b'WEBP':
        return '.webp'
    return ''


def _read_image(path):
    with open(path, 'rb') as source:
        return source.read(MAX_IMAGE_SIZE + 1)


class _MaterialMixin:
    """素材 API；查询/上传可用 return_error=True 获取原始失败响应。"""

    async def prepare_media_image(self, file_name, file_size, content_type, md5, sha1):
        """申请图片上传任务，返回 (是否成功, 响应 JSON)。"""
        if not isinstance(file_name, str) or not file_name.strip():
            return False, self._api_error('file_name 不能为空')
        expected = _IMAGE_TYPES.get(Path(file_name).suffix.lower())
        if not expected or expected != content_type:
            return False, self._api_error('文件扩展名与 Content-Type 必须匹配 png/jpeg/gif/webp')
        if isinstance(file_size, bool) or not isinstance(file_size, int) or not 0 < file_size <= MAX_IMAGE_SIZE:
            return False, self._api_error('图片大小必须为 1 字节至 10 MB')
        if not isinstance(md5, str) or not re.fullmatch(r'[0-9a-fA-F]{32}', md5):
            return False, self._api_error('md5 必须为 32 位十六进制字符串')
        if not isinstance(sha1, str) or not re.fullmatch(r'[0-9a-fA-F]{40}', sha1):
            return False, self._api_error('sha1 必须为 40 位十六进制字符串')
        return await self.post_json('/v2/media-images/prepare', {
            'file_name': file_name,
            'file_size': file_size,
            'content_type': content_type,
            'md5': md5.lower(),
            'sha1': sha1.lower(),
        })

    async def complete_media_image(self, upload_id, md5):
        """确认直传完成，返回 image_id、url、ttl 和临时 preview_url。"""
        upload_id = str(upload_id or '').strip()
        if not upload_id:
            return False, self._api_error('upload_id 不能为空')
        if not isinstance(md5, str) or not re.fullmatch(r'[0-9a-fA-F]{32}', md5):
            return False, self._api_error('md5 必须为 32 位十六进制字符串')
        return await self.post_json('/v2/media-images/complete', {
            'upload_id': upload_id,
            'md5': md5.lower(),
        })

    async def upload_media_image(self, image, *, file_name=None, content_type=None, return_error=False):
        """一键上传图片；满 100 张时先删除最早上传的 1 张。失败返回 None。"""
        async with self._material_upload_lock:
            data, error = await self._upload_media_image(image, file_name, content_type)
        return (data, error) if return_error else data

    async def _ensure_media_image_capacity(self):
        data, error = await self.get_media_images(return_error=True)
        if error:
            return error
        images = list(data.get('images') or [])
        try:
            total = int(data.get('total', len(images)))
        except (TypeError, ValueError):
            return self._api_error('素材列表 total 无效')
        if total < 0:
            return self._api_error('素材列表 total 无效')
        if total < MAX_MEDIA_IMAGES:
            return None
        cursor = data.get('next_cursor', '')
        seen = set()
        while cursor:
            if cursor in seen or len(seen) >= 100:
                return self._api_error('素材列表分页游标未推进或超过上限')
            seen.add(cursor)
            data, error = await self.get_media_images(cursor=cursor, return_error=True)
            if error:
                return error
            images.extend(data.get('images') or [])
            cursor = data.get('next_cursor', '')
        try:
            oldest = min(images, key=lambda image: int(image['created_at']))
            image_id = oldest['image_id']
        except (KeyError, TypeError, ValueError):
            return self._api_error('素材列表缺少有效的 image_id 或 created_at，无法清理最早的图片')
        success, error = await self.delete_media_image(image_id)
        return None if success else error

    async def _upload_media_image(self, image, file_name, content_type):
        if isinstance(image, str | os.PathLike):
            path = Path(image)
            file_name = file_name or path.name
            try:
                image = await asyncio.to_thread(_read_image, path)
            except (OSError, ValueError) as exc:
                return None, self._api_error(f'读取图片失败: {exc}')
        if not isinstance(image, bytes | bytearray | memoryview):
            return None, self._api_error('image 必须为图片 bytes 或本地文件路径')
        size = image.nbytes if isinstance(image, memoryview) else len(image)
        if not 0 < size <= MAX_IMAGE_SIZE:
            return None, self._api_error('图片大小必须为 1 字节至 10 MB')
        image = bytes(image)
        extension = _image_type(image)
        if not extension:
            return None, self._api_error('只支持 PNG、JPEG、GIF、WebP 图片')
        file_name = file_name or f'image{extension}'
        detected_type = _IMAGE_TYPES[extension]
        content_type = content_type or detected_type
        if (not isinstance(file_name, str)
                or _IMAGE_TYPES.get(Path(file_name).suffix.lower()) != detected_type
                or content_type != detected_type):
            return None, self._api_error('文件扩展名、Content-Type 与图片格式必须一致')

        def hashes():
            return (
                hashlib.md5(image, usedforsecurity=False).hexdigest(),
                hashlib.sha1(image, usedforsecurity=False).hexdigest(),
            )

        md5, sha1 = await asyncio.to_thread(hashes)
        error = await self._ensure_media_image_capacity()
        if error:
            return None, error
        success, prepared = await self.prepare_media_image(file_name, len(image), content_type, md5, sha1)
        if not success:
            return None, prepared
        upload_id = prepared.get('upload_id') if isinstance(prepared, dict) else None
        url = prepared.get('presigned_url') if isinstance(prepared, dict) else None
        try:
            parsed_url = urlsplit(url) if isinstance(url, str) else None
        except ValueError:
            parsed_url = None
        if not upload_id or not parsed_url or parsed_url.scheme != 'https' or not parsed_url.netloc:
            return None, self._api_error('素材上传响应缺少 upload_id 或有效的 presigned_url')
        try:
            client = await self._ensure_client()
            # COS 接收二进制并可能返回空正文；直传不经过附加 QQBot 鉴权和解析 JSON 的 _request。
            response = await client.put(url, content=image, headers={'Content-Type': content_type}, timeout=60)
        except Exception as exc:
            return None, _describe_exception(exc, 'PUT', '素材图片直传')
        if not 200 <= response.status_code < 300:
            return None, {'code': response.status_code, 'message': f'素材图片直传失败 (HTTP {response.status_code})'}
        etag = response.headers.get('ETag', response.headers.get('etag', ''))
        if etag and str(etag).strip('"').lower() != md5:
            return None, self._api_error('素材图片直传回执 MD5 与原文件不一致')
        success, data = await self.complete_media_image(upload_id, md5)
        if not success:
            return None, data
        if (not isinstance(data, dict) or not data.get('image_id')
                or not str(data.get('url', '')).startswith('qqbot-media://image/v1/')):
            return None, self._api_error('素材上传响应缺少 image_id 或有效的 qqbot-media 引用')
        return data, None

    async def get_media_images(self, *, cursor='', return_error=False):
        """分页查询有效图片素材，next_cursor 为空时已到末页。"""
        params = {'cursor': str(cursor)} if cursor else {}
        success, data = await self.get_json('/v2/media-images', params=params)
        if not success:
            return (None, data) if return_error else None
        if (not isinstance(data, dict) or not isinstance(data.get('images', []), list)
                or not isinstance(data.get('next_cursor', ''), str)):
            error = self._api_error('素材列表响应格式无效')
            return (None, error) if return_error else None
        data.setdefault('images', [])
        data.setdefault('next_cursor', '')
        return (data, None) if return_error else data

    async def delete_media_image(self, image_id):
        """删除素材并释放额度，返回 (是否成功, 响应 JSON)。"""
        image_id = str(image_id or '').strip()
        if not image_id:
            return False, self._api_error('image_id 不能为空')
        return await self.delete(f'/v2/media-images/{quote(image_id, safe="")}/delete')
