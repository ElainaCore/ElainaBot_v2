"""插件市场 — 全局状态, 镜像配置, URL/目录辅助"""

import json
import logging
import os
import re

from web.tools._updater.shared import _build_mirror_url, _load_mirror_cache

log = logging.getLogger('ElainaBot.web.market')

# ==================== GitHub 插件库配置 ====================
PLUGIN_REPO = 'ElainaCore/Elaina-plugins'
_PLUGIN_JSON_RAW = f'https://raw.githubusercontent.com/{PLUGIN_REPO}/main/plugins.json'
_FALLBACK_MIRROR_PREFIXES = [
    'https://ghproxy.cc/',
    'https://gh-proxy.com/',
    'https://gh.llkk.cc/',
    'https://gh.idayer.com/',
]

_base_dir = ''


def set_context(base_dir: str, appid: str = '', robot_qq: str = ''):
    global _base_dir
    _base_dir = base_dir


# ==================== 市场镜像偏好 ====================


def _market_mirror_path():
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        'data',
        'market_mirror.json',
    )


def _load_market_mirror():
    try:
        p = _market_mirror_path()
        if os.path.isfile(p):
            with open(p, encoding='utf-8') as f:
                return json.load(f).get('mirror', '')
    except (OSError, ValueError):
        pass
    return ''


def _save_market_mirror(mirror):
    try:
        p = _market_mirror_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w', encoding='utf-8') as f:
            json.dump({'mirror': mirror}, f)
    except (OSError, TypeError, ValueError):
        pass


def _plugins_dir():
    return os.path.join(_base_dir, 'plugins')


def _modules_dir():
    return os.path.join(_base_dir, 'modules')


# ==================== URL 辅助 ====================


def _ranked_mirror_urls(raw_url):
    """按磁盘缓存排名生成 URL 列表, 缓存为空时用兜底镜像"""
    cached = _load_mirror_cache()
    if cached:
        urls = [_build_mirror_url(raw_url, m['mirror'] if isinstance(m, dict) else m) for m in cached]
    else:
        urls = [_build_mirror_url(raw_url, p) for p in _FALLBACK_MIRROR_PREFIXES]
    if raw_url not in urls:
        urls.append(raw_url)
    return urls


def _convert_github_url(url):
    """将 GitHub blob URL 转为 raw URL"""
    if 'raw.githubusercontent.com' in url or '/raw/' in url:
        return url
    m = re.match(r'https?://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.+)', url)
    if m:
        user, repo, branch, path = m.groups()
        return f'https://raw.githubusercontent.com/{user}/{repo}/{branch}/{path}'
    return url


def _repo_raw_url(repo_url, path, branch='main'):
    """将 GitHub 仓库 URL + 仓库内路径转为 raw 下载地址"""
    m = re.match(r'https?://github\.com/([^/]+)/([^/]+)', repo_url)
    if m:
        user, repo = m.groups()
        return f'https://raw.githubusercontent.com/{user}/{repo}/{branch}/{path.lstrip("/")}'
    return repo_url


def _github_to_archive(url, branch='main'):
    """将 GitHub 仓库 URL 转为 zip 下载地址"""
    if '/archive/' in url or 'codeload.github.com' in url:
        return url
    m = re.match(r'https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?$', url.rstrip('/'))
    if m:
        user, repo = m.groups()
        return f'https://github.com/{user}/{repo}/archive/refs/heads/{branch}.zip'
    return url


_SAFE_NAME_RE = re.compile(r'[^\w\- ]')


def _safe_name(name):
    return _SAFE_NAME_RE.sub('', name).strip()
