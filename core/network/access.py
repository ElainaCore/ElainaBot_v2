#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Token 管理器 — 异步, 每个机器人独立维护 access_token, 自动续期"""

import time
import asyncio
import logging
import ssl
import aiohttp

logger = logging.getLogger('ElainaBot.access')

_TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
_API_BASE = "https://api.sgroup.qq.com"

_REFRESH_BUFFER = 60   # 提前刷新秒数
_MAX_RETRIES = 3
_RETRY_DELAYS = (3, 6, 12)


class TokenManager:
    """单个机器人的 Token 管理器 (异步)"""

    __slots__ = (
        'appid', 'secret',
        '_token', '_expires_at', '_lock',
        '_ssl_ctx', '_client', '_refresh_task', '_closed',
    )

    def __init__(self, appid, secret):
        self.appid = str(appid)
        self.secret = str(secret)
        self._token = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()
        self._ssl_ctx = ssl.create_default_context()
        self._ssl_ctx.check_hostname = False
        self._ssl_ctx.verify_mode = ssl.CERT_NONE
        self._client = None  # 延迟创建 aiohttp.ClientSession
        self._refresh_task = None
        self._closed = False

    @property
    def api_base(self):
        return _API_BASE

    @property
    def authorization(self):
        """返回 Authorization 头值"""
        return f"QQBot {self._token}" if self._token else ""

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

    async def _ensure_client(self):
        if self._client is None or self._client.closed:
            timeout = aiohttp.ClientTimeout(total=10)
            conn = aiohttp.TCPConnector(ssl=self._ssl_ctx)
            self._client = aiohttp.ClientSession(timeout=timeout, connector=conn)
        return self._client

    async def _refresh(self):
        payload = {"appId": self.appid, "clientSecret": self.secret}
        client = await self._ensure_client()
        last_error = None
        for i in range(_MAX_RETRIES):
            try:
                async with client.post(_TOKEN_URL, json=payload) as resp:
                    data = await resp.json(content_type=None)
                    if resp.status == 200 and 'access_token' in data:
                        self._token = data['access_token']
                        self._expires_at = time.time() + int(data.get('expires_in', 7200))
                        logger.info(f"[{self.appid}] Token 已刷新, 有效期 {data.get('expires_in', '?')}s")
                        return
                    last_error = f"HTTP {resp.status}: {data}"
            except Exception as e:
                last_error = str(e)
            if i < _MAX_RETRIES - 1:
                await asyncio.sleep(_RETRY_DELAYS[i])
        logger.error(f"[{self.appid}] Token 获取失败: {last_error}")
        raise RuntimeError(f"Token 获取失败 (appid={self.appid}): {last_error}")

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
                break
            except Exception as e:
                logger.warning(f"[{self.appid}] 自动刷新异常: {e}")
                await asyncio.sleep(30)

    async def close(self):
        self._closed = True
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
        if self._client and not self._client.closed:
            await self._client.close()
