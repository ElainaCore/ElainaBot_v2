#!/usr/bin/env python
"""HTTP 客户端兼容层 — 优先使用 httpx, 未安装时回退到 aiohttp"""

import json as _json

import aiohttp

try:
    import httpx

    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False


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
        max_connections=100,
        max_keepalive=50,
        keepalive_expiry=20.0,
        follow_redirects=True,
    ):
        self._is_httpx = HAS_HTTPX
        if self._is_httpx:
            self._client = httpx.AsyncClient(
                base_url=base_url or '',
                timeout=httpx.Timeout(timeout, connect=10.0, pool=10.0),
                follow_redirects=follow_redirects,
                limits=httpx.Limits(
                    max_connections=max_connections,
                    max_keepalive_connections=max_keepalive,
                    keepalive_expiry=keepalive_expiry,
                ),
            )
        else:
            _timeout = aiohttp.ClientTimeout(total=timeout, connect=10.0)
            _conn = aiohttp.TCPConnector(
                limit=max_connections,
                limit_per_host=max_keepalive,
                keepalive_timeout=int(keepalive_expiry),
                enable_cleanup_closed=True,
            )
            self._client = aiohttp.ClientSession(base_url=base_url or None, timeout=_timeout, connector=_conn)

    @property
    def is_closed(self):
        if self._is_httpx:
            return self._client.is_closed
        return self._client.closed

    async def request(self, method, url, **kwargs):
        if self._is_httpx:
            resp = await self._client.request(method, url, **kwargs)
            return HttpResponse(resp.status_code, resp.content, resp.headers)
        t = kwargs.get('timeout')
        if isinstance(t, int | float):
            kwargs['timeout'] = aiohttp.ClientTimeout(total=t)
        async with self._client.request(method, url, **kwargs) as resp:
            body = await resp.read()
            return HttpResponse(resp.status, body, resp.headers)

    async def get(self, url, **kwargs):
        return await self.request('GET', url, **kwargs)

    async def post(self, url, **kwargs):
        return await self.request('POST', url, **kwargs)

    async def put(self, url, *, content=None, headers=None, timeout=None, **kwargs):
        if self._is_httpx:
            _t = httpx.Timeout(timeout) if isinstance(timeout, int | float) else timeout
            resp = await self._client.put(url, content=content, headers=headers, timeout=_t, **kwargs)
            return HttpResponse(resp.status_code, resp.content, resp.headers)
        _t = aiohttp.ClientTimeout(total=timeout) if isinstance(timeout, int | float) else timeout
        async with self._client.put(url, data=content, headers=headers, timeout=_t, **kwargs) as resp:
            body = await resp.read()
            return HttpResponse(resp.status, body, resp.headers)

    async def aclose(self):
        if self._is_httpx:
            await self._client.aclose()
        else:
            await self._client.close()
