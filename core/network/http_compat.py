#!/usr/bin/env python
"""HTTP 客户端兼容层 — 优先使用 httpx, 未安装时回退到 aiohttp"""

import json as _json

import aiohttp

_MAX_CONNECTIONS = 128
_MAX_KEEPALIVE_CONNECTIONS = 64
_READ_CHUNK_SIZE = 64 * 1024

try:
    import httpx

    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False


class ResponseTooLargeError(ValueError):
    """响应超过调用方允许的字节数，读取已终止。"""


def _check_response_size(size, limit):
    if limit is not None and size > limit:
        raise ResponseTooLargeError(f'响应大小 {size} bytes 超过上限 {limit} bytes')


def _check_content_length(headers, limit):
    if limit is None:
        return
    length = headers.get('content-length', '')
    if length.isdecimal():
        _check_response_size(int(length), limit)


async def _read_body(chunks, limit):
    parts = []
    size = 0
    async for chunk in chunks:
        size += len(chunk)
        _check_response_size(size, limit)
        parts.append(chunk)
    return b''.join(parts)


class HttpResponse:
    """统一 HTTP 响应"""

    __slots__ = ('status_code', 'content', 'headers')

    def __init__(self, status_code, content, headers):
        self.status_code = status_code
        self.content = content
        self.headers = headers

    def json(self):
        return _json.loads(self.content)


class AsyncHttpClient:
    """统一异步 HTTP 客户端 — httpx 优先, 回退 aiohttp"""

    __slots__ = ('_client', '_is_httpx')

    def __init__(
        self,
        *,
        base_url='',
        timeout=30.0,
        follow_redirects=True,
    ):
        self._is_httpx = HAS_HTTPX
        if self._is_httpx:
            self._client = httpx.AsyncClient(
                base_url=base_url or '',
                timeout=httpx.Timeout(timeout, connect=10.0),
                follow_redirects=follow_redirects,
                limits=httpx.Limits(
                    max_connections=_MAX_CONNECTIONS,
                    max_keepalive_connections=_MAX_KEEPALIVE_CONNECTIONS,
                    keepalive_expiry=20.0,
                ),
            )
        else:
            _timeout = aiohttp.ClientTimeout(total=timeout, connect=10.0)
            _conn = aiohttp.TCPConnector(
                limit=_MAX_CONNECTIONS,
                limit_per_host=_MAX_CONNECTIONS,
                keepalive_timeout=20,
                enable_cleanup_closed=True,
            )
            self._client = aiohttp.ClientSession(base_url=base_url or None, timeout=_timeout, connector=_conn)

    @property
    def is_closed(self):
        if self._is_httpx:
            return self._client.is_closed
        return self._client.closed

    async def request(self, method, url, *, max_response_bytes=None, **kwargs):
        if max_response_bytes is not None and (type(max_response_bytes) is not int or max_response_bytes < 0):
            raise ValueError('max_response_bytes 必须是非负整数或 None')
        if self._is_httpx:
            # HTTPX 的 Response 与 BoundAsyncStream 相互引用。request()/aread()
            # 把完整 body 留在该环中，关闭连接后仍需等循环 GC 才能释放大文件。
            # 直接消费流，只让返回的 HttpResponse 持有完整 body。
            async with self._client.stream(method, url, **kwargs) as resp:
                _check_content_length(resp.headers, max_response_bytes)
                body = await _read_body(resp.aiter_bytes(chunk_size=_READ_CHUNK_SIZE), max_response_bytes)
                return HttpResponse(resp.status_code, body, resp.headers)
        t = kwargs.get('timeout')
        if isinstance(t, int | float):
            kwargs['timeout'] = aiohttp.ClientTimeout(total=t)
        async with self._client.request(method, url, **kwargs) as resp:
            _check_content_length(resp.headers, max_response_bytes)
            body = await _read_body(resp.content.iter_chunked(_READ_CHUNK_SIZE), max_response_bytes)
            return HttpResponse(resp.status, body, resp.headers)

    async def get(self, url, **kwargs):
        return await self.request('GET', url, **kwargs)

    async def post(self, url, **kwargs):
        return await self.request('POST', url, **kwargs)

    async def put(self, url, *, content=None, headers=None, timeout=None, **kwargs):
        # 保留 put 的既有语义：httpx 的 None 表示不限制超时。
        kwargs['timeout'] = timeout
        if headers is not None:
            kwargs['headers'] = headers
        kwargs['content' if self._is_httpx else 'data'] = content
        return await self.request('PUT', url, **kwargs)

    async def aclose(self):
        if self._is_httpx:
            await self._client.aclose()
        else:
            await self._client.close()
