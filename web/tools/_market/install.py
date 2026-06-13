"""插件市场 — 安装/卸载/预览/版本对比"""

import ast
import io
import os
import re
import zipfile

from aiohttp import web

from web.tools._market.fetch import (
    _download_file,
)
from web.tools._market.shared import (
    _convert_github_url,
    _github_to_archive,
    _load_market_mirror,
    _modules_dir,
    _plugins_dir,
    _repo_raw_url,
    _safe_name,
    log,
)

# ==================== 版本/已安装 ====================


def _get_installed_names():
    """获取已安装的插件目录名列表"""
    plugins_dir = _plugins_dir()
    if not os.path.isdir(plugins_dir):
        return set()
    return {d for d in os.listdir(plugins_dir) if os.path.isdir(os.path.join(plugins_dir, d)) and not d.startswith(('.', '__'))}


def _get_installed_module_names():
    """获取已安装的模块目录名列表"""
    modules_dir = _modules_dir()
    if not os.path.isdir(modules_dir):
        return set()
    return {d for d in os.listdir(modules_dir) if os.path.isdir(os.path.join(modules_dir, d)) and not d.startswith(('.', '__'))}


_PLUGIN_ENTRY_NAMES = ('index.py', 'app.py', 'main.py')


def _read_meta_version(py_path, meta_var):
    """从单个 .py 文件解析 <meta_var>['version'] (静态 AST, 不执行代码)"""
    if not os.path.isfile(py_path):
        return ''
    try:
        with open(py_path, encoding='utf-8') as f:
            tree = ast.parse(f.read())
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == meta_var:
                meta = ast.literal_eval(node.value)
                if isinstance(meta, dict):
                    return str(meta.get('version', ''))
    except Exception:
        pass
    return ''


def _get_local_module_version(name):
    """读取本地模块的 __module_meta__['version']"""
    return _read_meta_version(os.path.join(_modules_dir(), name, 'main.py'), '__module_meta__')


def _get_local_plugin_version(name):
    """读取本地插件的 __plugin_meta__['version'] (入口文件优先, 否则扫描目录内其它 .py)"""
    pdir = os.path.join(_plugins_dir(), name)
    if not os.path.isdir(pdir):
        return ''
    for entry in _PLUGIN_ENTRY_NAMES:
        ver = _read_meta_version(os.path.join(pdir, entry), '__plugin_meta__')
        if ver:
            return ver
    try:
        for f in sorted(os.listdir(pdir)):
            if f.endswith('.py') and not f.startswith('_') and f not in _PLUGIN_ENTRY_NAMES:
                ver = _read_meta_version(os.path.join(pdir, f), '__plugin_meta__')
                if ver:
                    return ver
    except OSError:
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


# ==================== 预览 ====================


def _preview_zip(content):
    try:
        with zipfile.ZipFile(io.BytesIO(content), 'r') as zf:
            py_files = [f for f in zf.namelist() if f.endswith('.py') and not f.startswith('__') and '/__pycache__/' not in f]
            files = []
            for pf in py_files[:10]:
                try:
                    fc = zf.read(pf).decode('utf-8', errors='replace')
                    files.append({'name': pf, 'content': fc[:5000], 'size': len(fc)})
                except Exception:
                    pass
            return web.json_response(
                {
                    'success': True,
                    'type': 'zip',
                    'files': files,
                    'total_files': len(py_files),
                }
            )
    except Exception as e:
        return web.json_response({'success': False, 'message': str(e)})


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
            return web.json_response(
                {
                    'success': True,
                    'type': 'py',
                    'filename': fname,
                    'content': code,
                    'size': len(code),
                }
            )
        return web.json_response({'success': False, 'message': '不支持的文件类型'})
    except Exception as e:
        return web.json_response({'success': False, 'message': str(e)})


# ==================== 安装 ====================


def _install_py(content, plugin_name, url):
    plugins_dir = _plugins_dir()
    fname = url.split('/')[-1].split('?')[0]
    if not fname.endswith('.py'):
        fname = f'{plugin_name}.py'
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
                rel = fp[len(root_prefix) :] if strip_root and fp.startswith(root_prefix) else fp
                if not rel:
                    continue
                dest = os.path.join(dest_dir, rel)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with zf.open(fp) as src, open(dest, 'wb') as dst:
                    dst.write(src.read())
                extracted.append(rel)
            py_count = sum(1 for f in extracted if f.endswith('.py'))
            total = len(extracted)
            log.info(f'插件 {safe} 安装完成: {total} 个文件 ({py_count} 个 .py)')
            return {
                'success': True,
                'message': f'已安装到 plugins/{safe}/ ({total} 个文件, {py_count} 个 Python)',
                'path': f'plugins/{safe}',
                'files': total,
            }
    except Exception as e:
        return {'success': False, 'message': str(e)}


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


async def _install_module(github_url, module_name, branch='main', mirror=None):
    """安装/更新模块
    两种模式自动判断:
      1. 官方模块: 仓库含 modules/<name>/ → 只提取该子目录
      2. 第三方模块: 整个仓库就是模块 → 全部装到 modules/<name>/
    """
    safe = _safe_name(module_name) or 'unknown'
    url = _github_to_archive(github_url, branch)
    log.info(f'模块安装: {safe} ← {url}')

    content = await _download_file(url, mirror=mirror)
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
                    return {
                        'success': False,
                        'message': f'框架仓库中未找到 modules/{safe}/',
                    }
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
                rel = fp[len(mod_prefix) :]
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

            log.info(f'模块 {safe} 安装完成: {len(extracted)} 个文件')
            return {
                'success': True,
                'message': f'已更新 modules/{safe}/ ({len(extracted)} 个文件)',
                'path': f'modules/{safe}',
                'files': len(extracted),
            }
    except Exception as e:
        return {'success': False, 'message': str(e)}


async def handle_market_install(request: web.Request):
    """安装插件/模块"""
    body = await request.json()
    github_url = body.get('github', '') or body.get('url', '') or body.get('download_url', '')
    item_name = body.get('name', 'unknown')
    item_type = body.get('type', 'plugin')
    file_path = body.get('path', '')
    branch = body.get('branch', 'main')
    mirror = body.get('mirror', '') or _load_market_mirror()
    if not github_url:
        return web.json_response({'success': False, 'message': '缺少下载地址'}, status=400)

    try:
        # 模块安装: 从仓库 zip 中提取 modules/<name>/ 子目录
        if item_type == 'module':
            return web.json_response(await _install_module(github_url, item_name, branch, mirror=mirror))

        # 插件安装: 有 path → 从仓库下载单个文件
        if file_path:
            url = _repo_raw_url(github_url, file_path, branch)
            log.info(f'插件安装 (单文件): {item_name} ← {url}')
            content = await _download_file(url, mirror=mirror)
            if content is None:
                return web.json_response({'success': False, 'message': '文件下载失败, 请检查路径或网络'})
            return web.json_response(_install_py(content, item_name, url))

        # 插件安装: 无 path → 拉取整个仓库 zip
        is_repo = bool(re.match(r'https?://github\.com/[^/]+/[^/]+/?$', github_url.rstrip('/')))
        if is_repo:
            url = _github_to_archive(github_url, branch)
            log.info(f'插件安装 (仓库): {item_name} ← {url}')
        else:
            url = _convert_github_url(github_url)

        content = await _download_file(url, mirror=mirror)
        if content is None:
            return web.json_response({'success': False, 'message': '下载失败, 请检查网络或镜像'})

        if content[:4] == b'PK\x03\x04':
            return web.json_response(_install_zip(content, item_name))

        is_py = url.endswith('.py') or any(k in content[:500] for k in [b'import ', b'def ', b'class '])
        if is_py:
            return web.json_response(_install_py(content, item_name, url))
        return web.json_response({'success': False, 'message': '不支持的文件类型'})
    except Exception as e:
        log.error(f'安装失败 [{item_name}]: {e}')
        return web.json_response({'success': False, 'message': str(e)})


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
        log.info(f'{label} 已卸载')
        return web.json_response({'success': True, 'message': f'已卸载 {label}'})
    except Exception as e:
        return web.json_response({'success': False, 'message': f'删除失败: {e}'})
