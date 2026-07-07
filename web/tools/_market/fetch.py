"""插件市场 — GitHub 下载, 镜像排名"""

import json
import time

import aiohttp as _aiohttp

from web.tools._market.shared import (
    PLUGIN_REPO,
    _ranked_mirror_urls,
)

_plugin_cache = None  # 缓存的插件列表
_plugin_cache_ts = 0
_PLUGIN_CACHE_TTL = 10 * 60  # 10 分钟


async def _try_fetch_json(session, urls, headers, timeout):
    """依次尝试 URL 列表下载 JSON, 成功返回解析结果, 全部失败返回 None"""
    for url in urls:
        try:
            async with session.get(url, headers=headers, timeout=timeout, ssl=False, allow_redirects=True) as resp:
                if resp.status == 200:
                    body = await resp.read()
                    if body[:1] in (b'[', b'{'):
                        return json.loads(body)
        except Exception:
            continue
    return None


async def _fetch_plugin_json(force=False):
    """从 GitHub 获取 plugins.json, 按镜像排名依次尝试"""
    global _plugin_cache, _plugin_cache_ts
    now = time.time()
    if not force and _plugin_cache and (now - _plugin_cache_ts) < _PLUGIN_CACHE_TTL:
        return _plugin_cache

    raw_url = f'https://raw.githubusercontent.com/{PLUGIN_REPO}/main/plugins.json'
    headers = {'User-Agent': 'ElainaBot/1.0'}
    timeout = _aiohttp.ClientTimeout(total=10)
    async with _aiohttp.ClientSession() as session:
        data = await _try_fetch_json(session, _ranked_mirror_urls(raw_url), headers, timeout)
    if not data:
        from web.tools._updater.mirror import get_fast_mirrors

        await get_fast_mirrors(force=True)
        async with _aiohttp.ClientSession() as session:
            data = await _try_fetch_json(session, _ranked_mirror_urls(raw_url), headers, timeout)
    if data:
        _plugin_cache, _plugin_cache_ts = data, now
    return data


def _is_zip_url(url):
    """URL 是否指向 zip 文件 (仓库 archive / codeload / .zip)"""
    base = url.split('?')[0].lower()
    return base.endswith('.zip') or '/archive/' in base or 'codeload.github.com' in base


def _is_valid_body(url, body):
    """校验下载内容: zip URL 必须是 zip 签名, 其它拒绝 HTML 页面 (镜像常以 200 返回错误页)"""
    if not body:
        return False
    if _is_zip_url(url):
        return body[:4] == b'PK\x03\x04'
    return not body[:512].lstrip().lower().startswith((b'<!doctype', b'<html'))


async def _fetch_once(session, url, timeout):
    """单次 GET, 200 返回 body, 否则 None"""
    try:
        async with session.get(
            url,
            timeout=_aiohttp.ClientTimeout(total=timeout),
            ssl=False,
            allow_redirects=True,
            headers={'User-Agent': 'ElainaBot/1.0'},
        ) as resp:
            if resp.status == 200:
                return await resp.read()
    except Exception:
        pass
    return None


async def _download_file(url, timeout=60, mirror=None):
    """按镜像排名下载并校验内容, 全失败重新测速后再试; mirror 非空时优先使用指定镜像"""
    is_gh = 'github.com' in url or 'githubusercontent.com' in url
    if mirror and is_gh:
        from web.tools._updater.shared import _build_mirror_url

        urls = [_build_mirror_url(url, mirror)] + _ranked_mirror_urls(url)
    else:
        urls = _ranked_mirror_urls(url) if is_gh else [url]
    async with _aiohttp.ClientSession() as session:
        for u in urls:
            body = await _fetch_once(session, u, timeout)
            if body is not None and _is_valid_body(url, body):
                return body
    # 全失败 → 重新测速后再试
    if is_gh:
        from web.tools._updater.mirror import get_fast_mirrors

        await get_fast_mirrors(force=True)
        async with _aiohttp.ClientSession() as session:
            for u in _ranked_mirror_urls(url):
                body = await _fetch_once(session, u, timeout)
                if body is not None and _is_valid_body(url, body):
                    return body
    return None


def _extract_plugins(data):
    """从缓存数据提取插件列表 (兼容 list 和 dict 格式)"""
    return data if isinstance(data, list) else data.get('plugins', [])
