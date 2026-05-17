"""HTTP 请求层 — Token 自动重试、API 基础方法"""

import asyncio
import json
import time

from core.base.logger import FRAMEWORK, get_logger
from core.network.http_compat import HAS_HTTPX, AsyncHttpClient

log = get_logger(FRAMEWORK, "消息发送")

# ==================== 常量 ====================

MSG_TYPE_TEXT = 0
MSG_TYPE_MARKDOWN = 2
MSG_TYPE_ARK = 3
MSG_TYPE_MEDIA = 7


class MessageType:
    """消息类型枚举 (别名, 兼容 Enum 用法)"""

    MSG_TYPE_TEXT = MSG_TYPE_TEXT
    MSG_TYPE_MARKDOWN = MSG_TYPE_MARKDOWN
    MSG_TYPE_ARK = MSG_TYPE_ARK
    MSG_TYPE_MEDIA = MSG_TYPE_MEDIA


_API_BASE = "https://api.sgroup.qq.com"

_IGNORE_ERROR_CODES = frozenset({11293, 40054002, 40054003})
_TOKEN_EXPIRED_CODE = 11244
_MAX_MEDIA_DOWNLOAD = 100 * 1024 * 1024  # 100MB 下载上限, 防止 OOM


class _HttpMixin:
    """HTTP 请求层 Mixin"""

    async def _ensure_client(self):
        if self._client is None or self._client.is_closed:
            self._client = AsyncHttpClient(base_url=self._base_url, timeout=30.0)
            log.info(
                f"[{self._appid}] HTTP客户端初始化: {'httpx' if HAS_HTTPX else 'aiohttp'}"
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _request(self, method, endpoint, **kwargs):
        client = await self._ensure_client()
        extra_headers = kwargs.pop("headers", None)
        for attempt in range(2):
            token = await self._token_mgr.get_token()
            headers = dict(extra_headers) if extra_headers else {}
            headers["Authorization"] = f"QQBot {token}"
            if "json" in kwargs:
                headers.setdefault("Content-Type", "application/json")
            try:
                _t = time.time()
                resp = await client.request(method, endpoint, headers=headers, **kwargs)
                body = resp.content
                _dt = (time.time() - _t) * 1000
                if _dt > 1500:
                    log.warning(
                        f"[{self._appid}] API {_dt:.0f}ms {method} {endpoint} -> {resp.status_code}"
                    )
                if resp.status_code >= 400:
                    try:
                        err = json.loads(body)
                    except Exception:
                        err = {
                            "message": body.decode(errors="replace"),
                            "code": resp.status_code,
                        }
                    if err.get("code") == _TOKEN_EXPIRED_CODE and attempt == 0:
                        await self._token_mgr.refresh_token()
                        await asyncio.sleep(0.1)
                        continue
                    return False, err
                if body:
                    return True, json.loads(body)
                return True, {}
            except Exception as e:
                return False, {"message": str(e), "code": -1}
        return False, {"message": "max retries", "code": -1}

    async def post_json(self, endpoint, payload):
        return await self._request("POST", endpoint, json=payload)

    async def put(self, endpoint, **kwargs):
        return await self._request("PUT", endpoint, **kwargs)

    async def delete(self, endpoint):
        return await self._request("DELETE", endpoint)
