#!/usr/bin/env python
"""Token 管理器 — 异步, 每个机器人独立维护 access_token, 自动续期"""

import asyncio
import logging
import time

from core.network.http_compat import AsyncHttpClient

logger = logging.getLogger('ElainaBot.access')

_TOKEN_URL = 'https://bots.qq.com/app/getAppAccessToken'
_API_BASE = 'https://api.sgroup.qq.com'

_REFRESH_BUFFER = 60  # 提前刷新秒数
_MAX_RETRIES = 3
_RETRY_DELAYS = (3, 6, 12)


class TokenManager:
    """单个机器人的 Token 管理器 (异步)"""

    __slots__ = (
        'appid',
        'secret',
        '_token',
        '_expires_at',
        '_lock',
        '_client',
        '_refresh_task',
        '_closed',
        '_api_base',
    )

    def __init__(self, appid, secret, *, api_base=''):
        self.appid = str(appid)
        self.secret = str(secret)
        self._api_base = api_base.rstrip('/') if api_base else _API_BASE
        # 提前校验: secret 为空或含未解析占位符时立即报错
        if not self.secret or '${' in self.secret:
            raise ValueError(
                f'Bot secret 无效 (appid={self.appid}): '
                f'{"未设置 (请通过 Web 面板配置 appid 和 secret)" if not self.secret else f"含未解析的占位符: {self.secret}"}'
            )
        self._token = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()
        self._client = None  # 延迟创建 AsyncHttpClient
        self._refresh_task = None
        self._closed = False

    @property
    def api_base(self):
        return self._api_base

    @property
    def authorization(self):
        """返回 Authorization 头值"""
        return f'QQBot {self._token}' if self._token else ''

    async def get_token(self):
        """获取当前有效 token, 过期自动刷新"""
        if self._is_valid():
            return self._token
        async with self._lock:
            if self._is_valid():
                return self._token
            await self._refresh()
            return self._token

    async def ensure_token(self):
        """确保已获取 token"""
        await self.get_token()

    async def refresh_token(self):
        """强制刷新 token"""
        async with self._lock:
            await self._refresh()

    def _is_valid(self):
        return self._token and time.time() < self._expires_at - _REFRESH_BUFFER

    async def get_client(self):
        """获取 HTTP 客户端 (延迟创建, Sender 共享此实例)"""
        if self._client is None or self._client.is_closed:
            self._client = AsyncHttpClient(base_url=self._api_base, timeout=30.0)
        return self._client

    _ensure_client = get_client  # 内部兼容

    async def _refresh(self):
        payload = {'appId': self.appid, 'clientSecret': self.secret}
        client = await self._ensure_client()
        last_error = None
        for i in range(_MAX_RETRIES):
            try:
                resp = await client.post(_TOKEN_URL, json=payload, timeout=10)
                data = resp.json()
                if resp.status_code == 200 and 'access_token' in data:
                    self._token = data['access_token']
                    self._expires_at = time.time() + int(data.get('expires_in', 7200))
                    logger.info(f'[{self.appid}] Token 已刷新, 有效期 {data.get("expires_in", "?")}s')
                    return
                last_error = f'HTTP {resp.status_code}: {data}'
            except Exception as e:
                last_error = str(e)
            if i < _MAX_RETRIES - 1:
                await asyncio.sleep(_RETRY_DELAYS[i])
        logger.error(f'[{self.appid}] Token 获取失败: {last_error}')
        raise RuntimeError(f'Token 获取失败 (appid={self.appid}): {last_error}')

    async def start_auto_refresh(self):
        """启动后台自动刷新"""
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = asyncio.create_task(self._auto_refresh_loop())

    async def _auto_refresh_loop(self):
        while not self._closed:
            try:
                ttl = self._expires_at - time.time()
                wait = max(ttl - _REFRESH_BUFFER, 30)
                await asyncio.sleep(wait)
                if self._closed:
                    break
                await self._refresh()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f'[{self.appid}] 自动刷新异常: {e}')
                await asyncio.sleep(30)

    async def close(self):
        self._closed = True
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
            await asyncio.gather(self._refresh_task, return_exceptions=True)
        if self._client and not self._client.is_closed:
            await self._client.aclose()
