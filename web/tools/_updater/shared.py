"""框架更新 — 常量, GitHub URL, 镜像列表, 缓存读写"""

import json
import logging
import os

log = logging.getLogger('ElainaBot.web.updater')

GITHUB_REPO = 'ElainaCore/ElainaBot_v2'
GITHUB_API_URL = f'https://api.github.com/repos/{GITHUB_REPO}'
GITHUB_DOWNLOAD_URL = f'https://github.com/{GITHUB_REPO}/archive/main.zip'
GITHUB_SHA_URL = f'https://codeload.github.com/{GITHUB_REPO}/zip/{{version}}'

# GitHub API 代理 (能代理 api.github.com 请求)
GITHUB_API_MIRRORS = [
    f'https://api.github.com/repos/{GITHUB_REPO}',  # 直连
    f'https://ghproxy.cc/https://api.github.com/repos/{GITHUB_REPO}',
    f'https://gh-proxy.com/https://api.github.com/repos/{GITHUB_REPO}',
    f'https://ghproxy.net/https://api.github.com/repos/{GITHUB_REPO}',
    f'https://mirror.ghproxy.com/https://api.github.com/repos/{GITHUB_REPO}',
    f'https://gh.api.99988866.xyz/https://api.github.com/repos/{GITHUB_REPO}',
]


GITHUB_FILE_MIRRORS = [
    'https://github.chenc.dev/',
    'https://ghproxy.cfd/',
    'https://github.tbedu.top/',
    'https://ghproxy.cc/',
    'https://gh.monlor.com/',
    'https://cdn.akaere.online/',
    'https://gh.idayer.com/',
    'https://gh.llkk.cc/',
    'https://ghpxy.hwinzniej.top/',
    'https://github-proxy.memory-echoes.cn/',
    'https://git.yylx.win/',
    'https://gitproxy.mrhjx.cn/',
    'https://gh.fhjhy.top/',
    'https://gp.zkitefly.eu.org/',
    'https://gh-proxy.com/',
    'https://ghfile.geekertao.top/',
    'https://j.1lin.dpdns.org/',
    'https://ghproxy.imciel.com/',
    'https://github-proxy.teach-english.tech/',
    'https://gh.927223.xyz/',
    'https://github.ednovas.xyz/',
    'https://ghf.xn--eqrr82bzpe.top/',
    'https://gh.dpik.top/',
    'https://gh.jasonzeng.dev/',
    'https://gh.xxooo.cf/',
    'https://gh.bugdey.us.kg/',
    'https://ghm.078465.xyz/',
    'https://j.1win.ggff.net/',
    'https://tvv.tw/',
    'https://gitproxy.127731.xyz/',
    'https://gh.inkchills.cn/',
    'https://ghproxy.cxkpro.top/',
    'https://gh.sixyin.com/',
    'https://github.geekery.cn/',
    'https://git.669966.xyz/',
    'https://gh.5050net.cn/',
    'https://gh.felicity.ac.cn/',
    'https://github.dpik.top/',
    'https://ghp.keleyaa.com/',
    'https://gh.wsmdn.dpdns.org/',
    'https://ghproxy.monkeyray.net/',
    'https://fastgit.cc/',
    'https://gh.catmak.name/',
    'https://gh.noki.icu/',
]

# ==================== 镜像缓存  ====================


def _mirror_cache_path():
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        'data',
        'mirror_cache.json',
    )


def _save_mirror_cache(mirrors):
    try:
        path = _mirror_cache_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({'mirrors': mirrors}, f)
    except Exception:
        pass


def _load_mirror_cache():
    """读取磁盘缓存 (永久有效, 全失败时由调用方重新测速)"""
    try:
        path = _mirror_cache_path()
        if not os.path.isfile(path):
            return []
        with open(path, encoding='utf-8') as f:
            return json.load(f).get('mirrors', [])
    except Exception:
        return []


def clear_mirror_cache():
    """删除磁盘缓存, 强制下次重新测速"""
    try:
        path = _mirror_cache_path()
        if os.path.isfile(path):
            os.remove(path)
    except Exception:
        pass


def _build_mirror_url(original_url, mirror):
    """拼接镜像 URL"""
    if not mirror:
        return original_url
    return mirror.rstrip('/') + '/' + original_url


# 默认跳过的路径
DEFAULT_SKIP = ['config/', 'data/', 'plugins/', 'modules/', '.git/', '__pycache__/', 'tests/', '.github/', '.gitignore', 'README.md']
# 白名单: 即使父目录在 skip 列表, 这些路径仍然正常更新
DEFAULT_WHITELIST = ['plugins/system/']
