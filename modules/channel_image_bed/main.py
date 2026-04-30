#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""可选模块: 频道图床

通过 QQ 频道 API 上传图片获取 URL, 提供图床功能。
插件按需使用: bed = module_manager.get("channel_image_bed")

模块目录: modules/channel_image_bed/
无额外依赖 (使用框架自带 aiohttp)

用法 (插件中):
    bed = bot.module_manager.get("channel_image_bed")
    url = await bed.upload(image_bytes, "test.png")
"""

import json
import asyncio
import aiohttp
from core.base.logger import get_logger, EXTENSION

log = get_logger(EXTENSION, "频道图床")

_API_BASE = "https://api.sgroup.qq.com"
_MAX_RETRIES = 2
_RETRY_DELAYS = (1, 3)

_instance = None


# ==================== 模块入口 ====================

async def setup(ctx):
    """模块启用: 返回 ChannelImageBed 实例"""
    global _instance
    _comments = {
        'channel_id': '用于上传图片的子频道 ID (必填)',
        'timeout': '请求超时时间 (秒)',
        'max_retries': '上传失败最大重试次数',
    }
    cfg = ctx.ensure_config({
        'channel_id': '',
        'timeout': 30,
        'max_retries': 2,
    }, comments=_comments)
    if not cfg.get('channel_id'):
        log.warning("未配置 channel_id, 请在 data/config.yaml 中填写频道 ID")
    _instance = ChannelImageBed(cfg)
    log.info("✅ 已启用")
    return _instance


async def teardown():
    """模块禁用"""
    global _instance
    if _instance:
        await _instance.close()
        _instance = None


class ChannelImageBed:
    """频道图床 - 通过频道 API 上传图片获取 URL"""

    __slots__ = ('_channel_id', '_base_url', '_timeout', '_max_retries',
                 '_session', '_token_mgr')

    def __init__(self, cfg):
        self._channel_id = cfg.get('channel_id', '')
        self._base_url = _API_BASE
        self._timeout = aiohttp.ClientTimeout(total=cfg.get('timeout', 30))
        self._max_retries = cfg.get('max_retries', _MAX_RETRIES)
        self._session = None
        self._token_mgr = None

    def bind_token_manager(self, token_manager):
        """绑定 TokenManager (由 BotInstance 调用)"""
        self._token_mgr = token_manager

    async def _get_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def _get_headers(self):
        headers = {}
        if self._token_mgr:
            token = await self._token_mgr.get_token()
            headers['Authorization'] = f"QQBot {token}"
        return headers

    # ---------- 核心方法 ----------

    async def upload(self, image_data, filename='image.png', content_type='image/png'):
        """上传图片到频道, 返回图片 URL

        Args:
            image_data:   图片二进制数据 (bytes)
            filename:     文件名
            content_type: MIME 类型

        Returns:
            str: 图片 URL, 失败返回 None
        """
        if not self._channel_id:
            log.error("未配置 channel_id")
            return None

        session = await self._get_session()
        headers = await self._get_headers()
        url = f"{self._base_url}/channels/{self._channel_id}/messages"

        form = aiohttp.FormData()
        form.add_field('content', ' ')
        form.add_field('file_image', image_data,
                        filename=filename, content_type=content_type)

        for attempt in range(self._max_retries + 1):
            try:
                async with session.post(url, headers=headers, data=form) as resp:
                    if resp.status < 300:
                        data = await resp.json()
                        # 从返回的消息附件中提取图片 URL
                        attachments = data.get('attachments', [])
                        if attachments:
                            return attachments[0].get('url', '')
                        log.warning("上传成功但未返回附件 URL")
                        return None
                    elif resp.status >= 500 and attempt < self._max_retries:
                        text = await resp.text()
                        log.warning(f"上传重试 {attempt+1}/{self._max_retries}: {resp.status} {text[:100]}")
                        await asyncio.sleep(_RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)])
                    else:
                        text = await resp.text()
                        log.error(f"上传失败: {resp.status} {text[:200]}")
                        return None
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt < self._max_retries:
                    log.warning(f"上传重试 {attempt+1}/{self._max_retries}: {e}")
                    await asyncio.sleep(_RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)])
                else:
                    log.error(f"上传异常: {e}")
                    return None
        return None

    async def upload_from_url(self, image_url):
        """从 URL 下载图片后上传到频道图床

        Args:
            image_url: 图片 URL

        Returns:
            str: 图床 URL, 失败返回 None
        """
        session = await self._get_session()
        try:
            async with session.get(image_url) as resp:
                if resp.status != 200:
                    log.error(f"下载图片失败: {resp.status} {image_url}")
                    return None
                data = await resp.read()
                ct = resp.content_type or 'image/png'
                # 从 URL 提取文件名
                fname = image_url.rsplit('/', 1)[-1].split('?')[0] or 'image.png'
                return await self.upload(data, fname, ct)
        except Exception as e:
            log.error(f"下载图片异常: {e}")
            return None

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
