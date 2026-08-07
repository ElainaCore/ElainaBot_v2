"""CNB 图床：将图片上传到公开 CNB 仓库的资源附件。"""

from __future__ import annotations

import os
from urllib.parse import quote, unquote, urlsplit

import httpx

from ._common import BaseBed, guess_content_type, log


class Bed(BaseBed):
    name = 'cnb'
    display_name = 'CNB'
    priority = 45
    defaults = {
        'enabled': False,
        'repo': '',
        'token': '',
        'api_base': 'https://api.cnb.cool',
        'asset_base': 'https://cnb.cool',
        'max_file_size': 100 * 1024 * 1024,
        'verify_public_url': True,
        'timeout': 30,
    }
    comments = {
        '__desc__': 'CNB 图床 (图片存入公开仓库的附件资源；不提交到 Git 分支)',
        'enabled': '是否启用 CNB 图床',
        'repo': '目标仓库 slug，格式为组织名/仓库名；仓库必须公开，否则 QQ/CDN 无法匿名读取图片',
        'token': '访问令牌：上传需要 repo-code:rw；列表需要 repo-manage:r；删除需要 repo-manage:rw',
        'api_base': 'CNB OpenAPI 地址',
        'asset_base': '公开资源地址，默认 https://cnb.cool',
        'max_file_size': '单张图片最大大小 (字节)，默认 100MB',
        'verify_public_url': '上传后校验公开资源 URL 是否可匿名读取',
        'timeout': 'CNB API 单次请求超时秒数',
    }

    __slots__ = ('_available', '_transport')

    def __init__(self, cfg):
        super().__init__(cfg)
        self._available = False
        self._transport = None

    def initialize(self):
        if not self._cfg.get('enabled'):
            return
        try:
            _repo_path(self._cfg.get('repo', ''))
            if not str(self._cfg.get('token', '')).strip():
                raise ValueError('未配置 token')
        except (TypeError, ValueError) as exc:
            log.warning(f'CNB 配置不完整，已跳过: {exc}')
            return
        self._available = True
        log.info(f"CNB 图床已启用: {self._cfg.get('repo')}")

    def is_available(self):
        return self._available and bool(self._cfg.get('enabled'))

    async def upload(self, image_data, filename='image.png', **kwargs):
        """上传资源，成功返回包含 asset_url 的 dict，失败返回 (False, 原因)。"""
        del kwargs
        if not self.is_available():
            return (False, 'CNB 图床未开启或配置不完整')
        if not isinstance(image_data, bytes) or not image_data:
            return (False, '无效的图片数据')
        max_size = int(self._cfg.get('max_file_size', 100 * 1024 * 1024))
        if max_size <= 0 or len(image_data) > max_size:
            return (False, f'图片过大: {len(image_data)} bytes')

        safe_name = os.path.basename(str(filename or 'image.png')) or 'image.png'
        try:
            async with self._client() as client:
                response = await client.post(
                    self._api_url('upload/imgs'),
                    headers=self._headers(),
                    json={
                        'name': safe_name,
                        'size': len(image_data),
                        'ext': {'content_type': guess_content_type(safe_name)},
                    },
                )
                _raise_for_status(response, '申请上传地址')
                slot = response.json()

                upload_url = str(slot.get('upload_url', ''))
                assets = slot.get('assets') or {}
                asset_path = str(assets.get('path', ''))
                if not upload_url or not asset_path:
                    raise RuntimeError('CNB 上传响应缺少 upload_url 或 assets.path')

                upload = await client.put(upload_url, content=image_data, headers=slot.get('form') or {})
                _raise_for_status(upload, '上传文件')

                asset_url = self._asset_url(asset_path)
                verification = None
                if self._cfg.get('verify_public_url', True):
                    check = await client.get(asset_url, headers={'Range': 'bytes=0-0'})
                    if check.status_code not in (200, 206):
                        raise RuntimeError(f'公开资源校验失败 (HTTP {check.status_code})')
                    verification = {'status': check.status_code, 'content_type': check.headers.get('content-type', '')}

            return {
                'success': True,
                'url': asset_url,
                'asset_url': asset_url,
                'asset_path': asset_path,
                'asset_id': assets.get('id'),
                'filename': safe_name,
                'file_size': len(image_data),
                'content_type': assets.get('content_type') or guess_content_type(safe_name),
                'verification': verification,
            }
        except Exception as exc:
            log.warning(f'CNB 上传失败: {exc}')
            return (False, str(exc))

    async def upload_url(self, image_data, filename='image.png', **kwargs):
        """上传并只返回公开 URL，失败返回 (False, 原因)。"""
        result = await self.upload(image_data, filename, **kwargs)
        if isinstance(result, tuple):
            return result
        return result.get('asset_url') or (False, 'CNB 未返回资源 URL')

    async def list_assets(self, limit=10):
        """列出仓库资源附件，并为每条记录补充公开 URL。"""
        if not self.is_available():
            return []
        page_size = min(max(int(limit or 10), 1), 100)
        try:
            async with self._client() as client:
                response = await client.get(
                    self._api_url('list-assets'),
                    headers=self._headers(),
                    params={'page': 1, 'page_size': page_size},
                )
                _raise_for_status(response, '读取资源列表', permission='repo-manage:r')
                records = response.json()
            result = []
            for record in records[:page_size] if isinstance(records, list) else []:
                item = dict(record)
                if item.get('path'):
                    item['url'] = self._asset_url(item['path'])
                result.append(item)
            return result
        except Exception as exc:
            log.warning(f'CNB 资源列表读取失败: {exc}')
            return []

    async def delete(self, resource):
        """按资源 ID、记录 dict、资源路径或公开 URL 删除附件。"""
        if not self.is_available():
            return False
        try:
            asset_id = await self._resolve_asset_id(resource)
            if asset_id is None:
                return False
            async with self._client() as client:
                response = await client.delete(
                    self._api_url(f'assets/{quote(str(asset_id), safe="")}'),
                    headers=self._headers(),
                )
                _raise_for_status(response, '删除资源', permission='repo-manage:rw')
            return True
        except Exception as exc:
            log.warning(f'CNB 资源删除失败: {exc}')
            return False

    async def _resolve_asset_id(self, resource):
        if isinstance(resource, dict):
            return resource.get('id') or resource.get('asset_id')
        if isinstance(resource, int) or str(resource).isdigit():
            return resource

        target = _resource_path(str(resource))
        for record in await self.list_assets(limit=100):
            if _resource_path(str(record.get('path', ''))) == target:
                return record.get('id')
        return None

    def _client(self):
        return httpx.AsyncClient(
            timeout=float(self._cfg.get('timeout', 30)),
            follow_redirects=True,
            transport=self._transport,
        )

    def _api_url(self, operation):
        repo = quote(_repo_path(self._cfg.get('repo', '')), safe='/')
        return f"{str(self._cfg.get('api_base', 'https://api.cnb.cool')).rstrip('/')}/{repo}/-/{operation}"

    def _asset_url(self, path):
        base = str(self._cfg.get('asset_base', 'https://cnb.cool')).rstrip('/')
        return f'{base}/{quote(unquote(str(path)).lstrip("/"), safe="/%-._~")}'

    def _headers(self):
        return {
            'Accept': 'application/json',
            'Authorization': f"Bearer {str(self._cfg.get('token', '')).strip()}",
        }


def _repo_path(value):
    repo = str(value or '').strip().strip('/')
    parts = repo.split('/')
    if len(parts) < 2 or any(not part or part in ('.', '..') for part in parts):
        raise ValueError('repo 必须为组织名/仓库名格式')
    return '/'.join(parts)


def _resource_path(value):
    parsed = urlsplit(value)
    return unquote(parsed.path if parsed.scheme and parsed.netloc else value).rstrip('/')


def _raise_for_status(response, operation, permission=None):
    if response.status_code < 400:
        return
    detail = ''
    try:
        payload = response.json()
        detail = payload.get('message') or payload.get('msg') or str(payload)
    except Exception:
        detail = response.text[:300]
    suffix = f'，请确认 token 具有 {permission} 权限' if response.status_code == 403 and permission else ''
    raise RuntimeError(f'{operation}失败 (HTTP {response.status_code}): {detail}{suffix}')
