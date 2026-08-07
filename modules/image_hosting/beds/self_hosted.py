"""自身图床: 将图片保存到本机并通过主 HTTP 服务公开读取。"""

import hashlib
import ipaddress
import os
import tempfile
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import urlopen

from ._common import BaseBed, log, run_sync

_DEFAULT_ROUTE = '/api/ext/image-hosting'
_PUBLIC_IP_URL = 'https://api.ipify.org'
_IMAGE_FORMATS = (
    (lambda data: data.startswith(b'\x89PNG\r\n\x1a\n'), 'png'),
    (lambda data: data.startswith(b'\xff\xd8\xff'), 'jpg'),
    (lambda data: data.startswith((b'GIF87a', b'GIF89a')), 'gif'),
    (lambda data: len(data) >= 12 and data[:4] == b'RIFF' and data[8:12] == b'WEBP', 'webp'),
    (lambda data: data.startswith(b'BM'), 'bmp'),
    (lambda data: data.startswith((b'II*\x00', b'MM\x00*')), 'tiff'),
    (lambda data: len(data) >= 12 and data[4:12] in (b'ftypavif', b'ftypavis'), 'avif'),
)
_ALLOWED_EXTENSIONS = frozenset(item[1] for item in _IMAGE_FORMATS)


class Bed(BaseBed):
    name = 'self_hosted'
    display_name = '自身图床'
    priority = 80
    defaults = {
        'enabled': False,
        'public_base_url': '',
        'storage_dir': '',
        'max_file_size': 100 * 1024 * 1024,
        'permanent_cache': True,
    }
    comments = {
        '__desc__': '自身图床 (复用框架 HTTP 服务，无需鉴权即可读取)',
        'enabled': '是否启用自身图床',
        'public_base_url': '公开地址；可填写 IP:端口、域名:端口或域名，留空自动探测公网 IP',
        'storage_dir': '图片存储目录；留空使用模块 data/self_hosted，支持绝对路径',
        'max_file_size': '单张图片最大大小 (字节)，默认 100MB',
        'permanent_cache': '是否允许浏览器/CDN 永久缓存图片映射；关闭时使用 no-store，服务器原图仍会保留',
    }

    __slots__ = ('_storage_dir', '_base_url', '_available')

    def __init__(self, cfg):
        super().__init__(cfg)
        self._storage_dir = ''
        self._base_url = ''
        self._available = False

    def initialize(self):
        if not self._cfg.get('enabled'):
            return
        try:
            self._storage_dir = _resolve_storage_dir(self._cfg.get('storage_dir', ''))
            os.makedirs(self._storage_dir, exist_ok=True)
            self._base_url = _resolve_public_base_url(self._cfg.get('public_base_url', ''))
            self._available = True
            log.info(f'自身图床公开地址: {self._base_url}?filename=<文件名>')
        except (OSError, ValueError) as e:
            log.error(f'自身图床初始化失败: {e}')

    def is_available(self):
        return self._available and bool(self._storage_dir and self._base_url)

    async def upload(self, image_data, filename='image.png'):
        """保存图片并返回公开 URL；失败返回 (False, 原因)。"""
        if not self._cfg.get('enabled'):
            return (False, '自身图床未开启，请在 image_hosting 模块配置中启用')
        if not self.is_available():
            return (False, '自身图床初始化失败')
        return await run_sync(self._upload_sync, image_data, filename)

    def _upload_sync(self, image_data, filename):
        del filename  # 存储名由内容哈希与真实图片格式生成，避免路径注入和重名覆盖。
        try:
            image_bytes = image_data.getvalue() if isinstance(image_data, BytesIO) else image_data
            if not isinstance(image_bytes, bytes) or not image_bytes:
                return (False, '无效的图片数据')

            max_size = int(self._cfg.get('max_file_size', 100 * 1024 * 1024))
            if max_size <= 0 or len(image_bytes) > max_size:
                return (False, f'图片过大: {len(image_bytes)} bytes')

            extension = _detect_extension(image_bytes)
            if extension is None:
                return (False, '不支持的图片格式，仅支持 PNG/JPG/GIF/WebP/BMP/TIFF/AVIF')

            digest = hashlib.sha256(image_bytes).hexdigest()
            stored_name = f'{digest}.{extension}'
            target = os.path.join(self._storage_dir, stored_name)
            if not os.path.isfile(target):
                _write_atomic(target, image_bytes)
            return _build_public_url(self._base_url, stored_name)
        except (OSError, TypeError, ValueError) as e:
            log.warning(f'自身图床保存失败: {e}')
            return (False, str(e))

    def resolve_file(self, filename):
        """将公开文件名解析为本地文件；无效或不存在时返回 None。"""
        if not self.is_available() or not isinstance(filename, str):
            return None
        name, extension = os.path.splitext(filename)
        if len(name) != 64 or any(char not in '0123456789abcdef' for char in name):
            return None
        if extension.removeprefix('.').lower() not in _ALLOWED_EXTENSIONS:
            return None
        path = os.path.realpath(os.path.join(self._storage_dir, filename))
        root = os.path.realpath(self._storage_dir)
        if not path.startswith(root + os.sep) or not os.path.isfile(path):
            return None
        return path

    def response_headers(self):
        """返回公开图片响应头。内容哈希 URL 在图片变化后会自然生成新地址。"""
        cache_control = 'public, max-age=31536000, immutable' if self._cfg.get('permanent_cache', True) else 'no-store'
        return {
            'Cache-Control': cache_control,
            'Access-Control-Allow-Origin': '*',
            'X-Content-Type-Options': 'nosniff',
        }


def _detect_extension(data):
    for matches, extension in _IMAGE_FORMATS:
        if matches(data):
            return extension
    return None


def _resolve_storage_dir(value):
    default_root = Path(__file__).resolve().parent.parent / 'data'
    raw = str(value or '').strip()
    path = Path(raw).expanduser() if raw else default_root / 'self_hosted'
    if not path.is_absolute():
        path = default_root / path
    return str(path.resolve())


def _server_port():
    try:
        from core.base.config import cfg

        return int(cfg.get('settings', 'server.port', 5200))
    except (AttributeError, TypeError, ValueError):
        return 5200


def _detect_public_ip():
    try:
        with urlopen(_PUBLIC_IP_URL, timeout=3) as response:  # noqa: S310 - 固定的 HTTPS 地址。
            address = response.read(64).decode('ascii').strip()
        parsed = ipaddress.ip_address(address)
        if parsed.is_global:
            return parsed.compressed
    except (OSError, UnicodeError, ValueError) as e:
        raise ValueError('无法探测公网 IP，请填写 public_base_url') from e
    raise ValueError('公网 IP 探测结果无效，请填写 public_base_url')


def _resolve_public_base_url(value):
    authority = str(value or '').strip()
    if not authority:
        authority = f'{_detect_public_ip()}:{_server_port()}'
    elif '://' in authority or '/' in authority:
        raise ValueError('public_base_url 仅支持 IP:端口、域名:端口或域名')
    elif ':' not in authority:
        try:
            ipaddress.ip_address(authority)
        except ValueError:
            authority = f'{authority}:{_server_port()}'
        else:
            raise ValueError('IP 地址必须填写端口')

    host, separator, port = authority.rpartition(':')
    if not separator or not host or not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError('public_base_url 仅支持 IP:端口、域名:端口或域名')
    return f'http://{authority}{_DEFAULT_ROUTE}'


def _build_public_url(base_url, filename):
    """将文件名作为查询参数拼入框架扩展路由 URL。"""
    parsed = urlsplit(base_url)
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key != 'filename']
    query.append(('filename', filename))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ''))


def _write_atomic(target, data):
    fd, temp_path = tempfile.mkstemp(prefix='.upload-', dir=os.path.dirname(target))
    try:
        with os.fdopen(fd, 'wb') as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, target)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
