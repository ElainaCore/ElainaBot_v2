"""插件/模块市场 — GitHub 插件库 + 本地插件/模块管理"""

import os
import re
import io
import json
import time
import zipfile
import logging

import aiohttp as _aiohttp
from aiohttp import web

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
_plugin_cache = None   # 缓存的插件列表
_plugin_cache_ts = 0
_PLUGIN_CACHE_TTL = 10 * 60  # 10 分钟


def set_context(base_dir: str, appid: str = '', robot_qq: str = ''):
    global _base_dir
    _base_dir = base_dir


def _plugins_dir():
    return os.path.join(_base_dir, 'plugins')


def _modules_dir():
    return os.path.join(_base_dir, 'modules')


def _ranked_mirror_urls(raw_url):
    """按磁盘缓存排名生成 URL 列表, 缓存为空时用兜底镜像"""
    from web.tools.updater import _load_mirror_cache, _build_mirror_url
    cached = _load_mirror_cache()
    if cached:
        urls = [_build_mirror_url(raw_url, m['mirror'] if isinstance(m, dict) else m) for m in cached]
    else:
        urls = [_build_mirror_url(raw_url, p) for p in _FALLBACK_MIRROR_PREFIXES]
    if raw_url not in urls:
        urls.append(raw_url)
    return urls


async def _try_fetch_json(session, urls, headers, timeout):
    """依次尝试 URL 列表下载 JSON, 成功返回解析结果, 全部失败返回 None"""
    for url in urls:
        try:
            async with session.get(url, headers=headers, timeout=timeout,
                                   ssl=False, allow_redirects=True) as resp:
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
        from web.tools.updater import get_fast_mirrors
        await get_fast_mirrors(force=True)
        async with _aiohttp.ClientSession() as session:
            data = await _try_fetch_json(session, _ranked_mirror_urls(raw_url), headers, timeout)
    if data:
        _plugin_cache, _plugin_cache_ts = data, now
    return data


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
    """将 GitHub 仓库 URL + 仓库内路径转为 raw 下载地址
    https://github.com/user/repo + plugins/hello.py → https://raw.githubusercontent.com/user/repo/main/plugins/hello.py
    """
    m = re.match(r'https?://github\.com/([^/]+)/([^/]+)', repo_url)
    if m:
        user, repo = m.groups()
        return f'https://raw.githubusercontent.com/{user}/{repo}/{branch}/{path.lstrip("/")}'
    return repo_url


def _github_to_archive(url, branch='main'):
    """将 GitHub 仓库 URL 转为 zip 下载地址
    https://github.com/user/repo  →  https://github.com/user/repo/archive/refs/heads/main.zip
    已经是 archive/codeload URL 则直接返回
    """
    if '/archive/' in url or 'codeload.github.com' in url:
        return url
    m = re.match(r'https?://github\.com/([^/]+)/([^/]+)/?$', url.rstrip('/'))
    if m:
        user, repo = m.groups()
        return f'https://github.com/{user}/{repo}/archive/refs/heads/{branch}.zip'
    return url


# ==================== 市场列表 ====================

_SAFE_NAME_RE = re.compile(r'[^\w\- ]')


def _extract_plugins(data):
    """从缓存数据提取插件列表 (兼容 list 和 dict 格式)"""
    return data if isinstance(data, list) else data.get('plugins', [])


def _safe_name(name):
    return _SAFE_NAME_RE.sub('', name).strip()


async def handle_market_list(request: web.Request):
    """获取插件市场列表"""
    search = request.query.get('search', '').lower()
    category = request.query.get('category', '')
    data = await _fetch_plugin_json()
    if data is None:
        return web.json_response({'success': False, 'message': '无法连接插件库, 请检查网络'})

    plugins = _extract_plugins(data)
    if category:
        plugins = [p for p in plugins if p.get('category', '') == category]
    if search:
        plugins = [p for p in plugins
                   if search in p.get('name', '').lower()
                   or search in p.get('description', '').lower()
                   or search in p.get('author', '').lower()]

    # 标记已安装状态 + 版本对比
    installed_plugins = _get_installed_names()
    installed_modules = _get_installed_module_names()
    for p in plugins:
        safe = _safe_name(p.get('name', ''))
        if p.get('type') == 'module':
            p['installed'] = safe in installed_modules
            if p['installed']:
                local_ver = _get_local_module_version(safe)
                p['local_version'] = local_ver
                p['has_update'] = _version_lt(local_ver, p.get('version', ''))
        else:
            p['installed'] = safe in installed_plugins

    return web.json_response({'success': True, 'data': plugins, 'total': len(plugins)})


async def handle_market_categories(request: web.Request):
    """获取插件分类列表"""
    data = await _fetch_plugin_json()
    if data is None:
        return web.json_response({'success': False, 'message': '无法连接插件库'})
    cats = sorted(set(p.get('category', '未分类') for p in _extract_plugins(data)))
    return web.json_response({'success': True, 'data': cats})


async def handle_market_detail(request: web.Request):
    """获取插件详情"""
    body = await request.json()
    name = body.get('name', '')
    data = await _fetch_plugin_json()
    if data is None:
        return web.json_response({'success': False, 'message': '无法连接插件库'})
    match = next((p for p in _extract_plugins(data) if p.get('name') == name), None)
    return (web.json_response({'success': True, 'data': match}) if match
            else web.json_response({'success': False, 'message': '插件不存在'}))


async def handle_market_refresh(request: web.Request):
    """强制刷新插件库缓存"""
    data = await _fetch_plugin_json(force=True)
    if data is None:
        return web.json_response({'success': False, 'message': '刷新失败, 无法连接插件库'})
    total = len(_extract_plugins(data))
    return web.json_response({'success': True, 'message': f'已刷新, 共 {total} 个插件'})


# ==================== 预览/安装 ====================

async def handle_market_preview(request: web.Request):
    body = await request.json()
    url = body.get('url', '')
    if not url:
        return web.json_response({'success': False, 'message': '缺少 URL'}, status=400)

    url = _convert_github_url(url)
    try:
        content = await _download_file(url)
        if content is None:
            return web.json_response({'success': False, 'message': '下载失败'})

        if b'<!doctype html' in content[:100].lower() or b'<html' in content[:100].lower():
            return web.json_response({'success': False, 'message': '下载链接无效'})

        if content[:4] == b'PK\x03\x04':
            return _preview_zip(content)

        is_py = url.endswith('.py') or any(k in content[:500] for k in [b'import ', b'def ', b'class '])
        if is_py:
            code = content.decode('utf-8', errors='replace')
            fname = url.split('/')[-1].split('?')[0]
            if not fname.endswith('.py'):
                fname = 'plugin.py'
            return web.json_response({'success': True, 'type': 'py', 'filename': fname,
                                      'content': code, 'size': len(code)})
        return web.json_response({'success': False, 'message': '不支持的文件类型'})
    except Exception as e:
        return web.json_response({'success': False, 'message': str(e)})


async def handle_market_install(request: web.Request):
    """安装插件/模块
    请求体:
        name     — 名称 (用作 plugins/<name> 或 modules/<name>)
        type     — 类型: plugin(默认) / module
        url      — 下载地址
        github   — 等同于 url
        path     — 仓库内文件路径 (单文件插件用)
        branch   — 分支名, 默认 main
    """
    body = await request.json()
    github_url = body.get('github', '') or body.get('url', '') or body.get('download_url', '')
    item_name = body.get('name', 'unknown')
    item_type = body.get('type', 'plugin')
    file_path = body.get('path', '')
    branch = body.get('branch', 'main')
    if not github_url:
        return web.json_response({'success': False, 'message': '缺少下载地址'}, status=400)

    try:
        # 模块安装: 从仓库 zip 中提取 modules/<name>/ 子目录
        if item_type == 'module':
            return web.json_response(
                await _install_module(github_url, item_name, branch))

        # 插件安装: 有 path → 从仓库下载单个文件
        if file_path:
            url = _repo_raw_url(github_url, file_path, branch)
            log.info(f"插件安装 (单文件): {item_name} ← {url}")
            content = await _download_file(url)
            if content is None:
                return web.json_response({'success': False, 'message': '文件下载失败, 请检查路径或网络'})
            return web.json_response(_install_py(content, item_name, url))

        # 插件安装: 无 path → 拉取整个仓库 zip
        is_repo = bool(re.match(r'https?://github\.com/[^/]+/[^/]+/?$', github_url.rstrip('/')))
        if is_repo:
            url = _github_to_archive(github_url, branch)
            log.info(f"插件安装 (仓库): {item_name} ← {url}")
        else:
            url = _convert_github_url(github_url)

        content = await _download_file(url)
        if content is None:
            return web.json_response({'success': False, 'message': '下载失败, 请检查网络或镜像'})

        if content[:4] == b'PK\x03\x04':
            return web.json_response(_install_zip(content, item_name))

        is_py = url.endswith('.py') or any(k in content[:500] for k in [b'import ', b'def ', b'class '])
        if is_py:
            return web.json_response(_install_py(content, item_name, url))
        return web.json_response({'success': False, 'message': '不支持的文件类型'})
    except Exception as e:
        log.error(f"安装失败 [{item_name}]: {e}")
        return web.json_response({'success': False, 'message': str(e)})


# ==================== 下载辅助 ====================

async def _download_file(url, timeout=60):
    """按镜像排名下载, 全失败重新测速后再试"""
    is_gh = 'github.com' in url or 'githubusercontent.com' in url
    urls = _ranked_mirror_urls(url) if is_gh else [url]
    async with _aiohttp.ClientSession() as session:
        for u in urls:
            try:
                async with session.get(u, timeout=_aiohttp.ClientTimeout(total=timeout),
                                       ssl=False, allow_redirects=True,
                                       headers={'User-Agent': 'ElainaBot/1.0'}) as resp:
                    if resp.status == 200:
                        return await resp.read()
            except Exception:
                continue
    # 全失败 → 重新测速后再试
    if is_gh:
        from web.tools.updater import get_fast_mirrors
        await get_fast_mirrors(force=True)
        for u in _ranked_mirror_urls(url):
            try:
                async with _aiohttp.ClientSession() as session:
                    async with session.get(u, timeout=_aiohttp.ClientTimeout(total=timeout),
                                           ssl=False, allow_redirects=True,
                                           headers={'User-Agent': 'ElainaBot/1.0'}) as resp:
                        if resp.status == 200:
                            return await resp.read()
            except Exception:
                continue
    return None


# ==================== 安装辅助 ====================

def _preview_zip(content):
    try:
        with zipfile.ZipFile(io.BytesIO(content), 'r') as zf:
            py_files = [f for f in zf.namelist()
                        if f.endswith('.py') and not f.startswith('__') and '/__pycache__/' not in f]
            files = []
            for pf in py_files[:10]:
                try:
                    fc = zf.read(pf).decode('utf-8', errors='replace')
                    files.append({'name': pf, 'content': fc[:5000], 'size': len(fc)})
                except Exception:
                    pass
            return web.json_response({'success': True, 'type': 'zip', 'files': files,
                                      'total_files': len(py_files)})
    except Exception as e:
        return web.json_response({'success': False, 'message': str(e)})


def _install_py(content, plugin_name, url):
    plugins_dir = _plugins_dir()
    fname = url.split('/')[-1].split('?')[0]
    if not fname.endswith('.py'):
        fname = f"{plugin_name}.py"
    safe = _safe_name(plugin_name) or fname.replace('.py', '')
    dest_dir = os.path.join(plugins_dir, safe)
    os.makedirs(dest_dir, exist_ok=True)
    with open(os.path.join(dest_dir, fname), 'wb') as f:
        f.write(content)
    return {'success': True, 'message': f'已安装到 plugins/{safe}/{fname}'}


def _install_zip(content, plugin_name):
    """解压 zip 到 plugins/<plugin_name>/, 自动去除 GitHub archive 的根目录"""
    plugins_dir = _plugins_dir()
    safe = _safe_name(plugin_name) or 'unknown'
    dest_dir = os.path.join(plugins_dir, safe)
    try:
        with zipfile.ZipFile(io.BytesIO(content), 'r') as zf:
            flist = zf.namelist()
            if not flist:
                return {'success': False, 'message': '空压缩包'}
            # GitHub archive zip 总有一个根目录 (如 repo-main/), 自动去除
            roots = {f.split('/')[0] for f in flist if '/' in f and f.split('/')[0]}
            strip_root = len(roots) == 1
            root_prefix = list(roots)[0] + '/' if strip_root else ''
            os.makedirs(dest_dir, exist_ok=True)
            extracted = []
            for fp in flist:
                if fp.endswith('/') or '__pycache__' in fp or '/.git/' in fp:
                    continue
                rel = fp[len(root_prefix):] if strip_root and fp.startswith(root_prefix) else fp
                if not rel:
                    continue
                dest = os.path.join(dest_dir, rel)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with zf.open(fp) as src, open(dest, 'wb') as dst:
                    dst.write(src.read())
                extracted.append(rel)
            py_count = sum(1 for f in extracted if f.endswith('.py'))
            total = len(extracted)
            log.info(f"插件 {safe} 安装完成: {total} 个文件 ({py_count} 个 .py)")
            return {'success': True,
                    'message': f'已安装到 plugins/{safe}/ ({total} 个文件, {py_count} 个 Python)',
                    'path': f'plugins/{safe}',
                    'files': total}
    except Exception as e:
        return {'success': False, 'message': str(e)}


# ==================== 卸载 ====================

async def handle_market_uninstall(request: web.Request):
    """卸载已安装的插件/模块"""
    body = await request.json()
    item_name = body.get('name', '')
    item_type = body.get('type', 'plugin')
    if not item_name:
        return web.json_response({'success': False, 'message': '缺少名称'}, status=400)

    safe = _safe_name(item_name)
    if not safe:
        return web.json_response({'success': False, 'message': '无效名称'}, status=400)

    if item_type == 'module':
        dest_dir = os.path.join(_modules_dir(), safe)
        label = f'modules/{safe}'
    else:
        dest_dir = os.path.join(_plugins_dir(), safe)
        label = f'plugins/{safe}'
        if safe == 'system':
            return web.json_response({'success': False, 'message': '系统插件不可卸载'})

    if not os.path.isdir(dest_dir):
        return web.json_response({'success': False, 'message': f'{label} 不存在'})

    import shutil
    try:
        shutil.rmtree(dest_dir)
        log.info(f"{label} 已卸载")
        return web.json_response({'success': True, 'message': f'已卸载 {label}'})
    except Exception as e:
        return web.json_response({'success': False, 'message': f'删除失败: {e}'})


def _get_installed_names():
    """获取已安装的插件目录名列表"""
    plugins_dir = _plugins_dir()
    if not os.path.isdir(plugins_dir):
        return set()
    return {d for d in os.listdir(plugins_dir)
            if os.path.isdir(os.path.join(plugins_dir, d)) and not d.startswith(('.', '__'))}


def _get_installed_module_names():
    """获取已安装的模块目录名列表"""
    modules_dir = _modules_dir()
    if not os.path.isdir(modules_dir):
        return set()
    return {d for d in os.listdir(modules_dir)
            if os.path.isdir(os.path.join(modules_dir, d)) and not d.startswith(('.', '__'))}


def _get_local_module_version(name):
    """读取本地模块的 __module_meta__['version']"""
    import ast
    entry = os.path.join(_modules_dir(), name, 'main.py')
    if not os.path.isfile(entry):
        return ''
    try:
        with open(entry, 'r', encoding='utf-8') as f:
            tree = ast.parse(f.read())
        for node in ast.iter_child_nodes(tree):
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id == '__module_meta__'):
                meta = ast.literal_eval(node.value)
                return meta.get('version', '')
    except Exception:
        pass
    return ''


def _version_lt(local, remote):
    """简单版本号对比: local < remote 则有更新"""
    if not local or not remote:
        return False
    try:
        lp = [int(x) for x in local.split('.')]
        rp = [int(x) for x in remote.split('.')]
        return lp < rp
    except (ValueError, AttributeError):
        return local != remote


def _clean_module_dir(dest_dir):
    """清理模块目录 (保留 data/ 用户配置)"""
    if not os.path.isdir(dest_dir):
        return
    import shutil
    for item in os.listdir(dest_dir):
        if item == 'data':
            continue
        p = os.path.join(dest_dir, item)
        if os.path.isdir(p):
            shutil.rmtree(p)
        else:
            os.remove(p)


async def _install_module(github_url, module_name, branch='main'):
    """安装/更新模块
    两种模式自动判断:
      1. 官方模块: 仓库含 modules/<name>/ → 只提取该子目录
      2. 第三方模块: 整个仓库就是模块 → 全部装到 modules/<name>/
    """
    safe = _safe_name(module_name) or 'unknown'
    url = _github_to_archive(github_url, branch)
    log.info(f"模块安装: {safe} ← {url}")

    content = await _download_file(url)
    if content is None:
        return {'success': False, 'message': '下载失败, 请检查网络或镜像'}
    if content[:4] != b'PK\x03\x04':
        return {'success': False, 'message': '下载内容不是有效的 zip 文件'}

    try:
        with zipfile.ZipFile(io.BytesIO(content), 'r') as zf:
            flist = zf.namelist()
            # GitHub archive 根目录 (repo-branch/)
            roots = {f.split('/')[0] for f in flist if '/' in f and f.split('/')[0]}
            root_prefix = (list(roots)[0] + '/') if len(roots) == 1 else ''

            # 尝试匹配 modules/<name>/ (官方/框架内模块)
            mod_prefix = f'{root_prefix}modules/{safe}/'
            mod_files = [f for f in flist if f.startswith(mod_prefix) and not f.endswith('/')]

            if not mod_files:
                # 判断是否为框架仓库 (精确匹配官方仓库)
                is_framework = 'ElainaCore/ElainaBot_v2' in github_url
                if is_framework:
                    return {'success': False, 'message': f'框架仓库中未找到 modules/{safe}/'}
                # 第三方模块: 整个仓库就是模块内容
                mod_prefix = root_prefix
                mod_files = [f for f in flist if f.startswith(mod_prefix) and not f.endswith('/')]

            if not mod_files:
                return {'success': False, 'message': '仓库内容为空'}

            dest_dir = os.path.join(_modules_dir(), safe)
            _clean_module_dir(dest_dir)
            os.makedirs(dest_dir, exist_ok=True)

            extracted = []
            for fp in mod_files:
                if '__pycache__' in fp or '/.git/' in fp:
                    continue
                rel = fp[len(mod_prefix):]
                if not rel:
                    continue
                # 保留用户已有的 data/ 配置
                if rel.startswith('data/'):
                    dest = os.path.join(dest_dir, rel)
                    if os.path.exists(dest):
                        continue
                dest = os.path.join(dest_dir, rel)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with zf.open(fp) as src, open(dest, 'wb') as dst:
                    dst.write(src.read())
                extracted.append(rel)

            log.info(f"模块 {safe} 安装完成: {len(extracted)} 个文件")
            return {'success': True,
                    'message': f'已更新 modules/{safe}/ ({len(extracted)} 个文件)',
                    'path': f'modules/{safe}', 'files': len(extracted)}
    except Exception as e:
        return {'success': False, 'message': str(e)}


# ==================== 本地插件管理 ====================

async def handle_local_plugins(request: web.Request):
    plugins_dir = _plugins_dir()
    plugins = []
    if not os.path.isdir(plugins_dir):
        return web.json_response({'success': True, 'plugins': []})
    for item in os.listdir(plugins_dir):
        item_path = os.path.join(plugins_dir, item)
        if item.startswith(('.', '__')):
            continue
        if os.path.isdir(item_path):
            for f in os.listdir(item_path):
                if f.endswith('.py') and not f.startswith('__'):
                    plugins.append({'name': f'{item}/{f[:-3]}', 'type': 'file',
                                    'files': [f], 'path': f'{item}/{f}'})
        elif item.endswith('.py'):
            plugins.append({'name': item[:-3], 'type': 'file',
                            'files': [item], 'path': item})
    return web.json_response({'success': True, 'plugins': plugins})


async def handle_local_plugin_read(request: web.Request):
    body = await request.json()
    path = body.get('path', '')
    if not path or '..' in path:
        return web.json_response({'success': False, 'message': '无效路径'}, status=400)
    full = os.path.join(_plugins_dir(), path)
    if os.path.isfile(full) and full.endswith('.py'):
        with open(full, 'r', encoding='utf-8') as f:
            content = f.read()
        return web.json_response({'success': True, 'type': 'single',
                                  'files': [{'name': os.path.basename(path), 'path': path,
                                             'content': content, 'size': len(content)}]})
    if os.path.isdir(full):
        files = []
        for root, dirs, fnames in os.walk(full):
            dirs[:] = [d for d in dirs if not d.startswith(('__', '.'))]
            for fn in fnames:
                if fn.startswith(('__', '.')):
                    continue
                fp = os.path.join(root, fn)
                rel = os.path.relpath(fp, _plugins_dir())
                if fn.endswith('.py'):
                    with open(fp, 'r', encoding='utf-8') as f:
                        c = f.read()
                    files.append({'name': fn, 'path': rel, 'content': c, 'size': len(c), 'editable': True})
                else:
                    files.append({'name': fn, 'path': rel, 'size': os.path.getsize(fp), 'editable': False})
        return web.json_response({'success': True, 'type': 'folder', 'files': files})
    return web.json_response({'success': False, 'message': '不存在'}, status=404)


async def handle_local_plugin_save(request: web.Request):
    body = await request.json()
    files = body.get('files', [])
    if not files:
        return web.json_response({'success': False, 'message': '没有文件'}, status=400)
    saved, errors = [], []
    for fi in files:
        fp, content = fi.get('path', ''), fi.get('content')
        if not fp or content is None or '..' in fp or not fp.endswith('.py'):
            errors.append(f'{fp}: 无效')
            continue
        full = os.path.join(_plugins_dir(), fp)
        try:
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, 'w', encoding='utf-8') as f:
                f.write(content)
            saved.append(fp)
        except Exception as e:
            errors.append(f'{fp}: {e}')
    return web.json_response({
        'success': bool(saved),
        'message': f'已保存 {len(saved)} 个文件' + (f', {len(errors)} 个失败' if errors else ''),
        'saved': saved, 'errors': errors,
    })
