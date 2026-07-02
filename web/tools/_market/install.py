"""插件市场 — 安装/卸载/预览/版本对比"""

import ast
import io
import os
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

# 共享单文件插件目录 (位于 plugins/ 下), 仅当 single 插件显式声明 alone=True 时使用
_ALONE_DIR = 'alone'

# 插件类型 (来源于市场清单的 type 字段, 不再依据是否有 path 推断)
TYPE_COMPLETE = 'complete'  # 完整插件: 整仓库 / 仓库内某子目录, 装到 plugins/<name>/
TYPE_SINGLE = 'single'  # 独立插件: 单/多文件, 默认装到专属目录 plugins/<name>/
TYPE_MODULE = 'module'  # 模块: 装到 modules/<name>/


def _canonical_type(item_type):
    """规范化插件类型为 complete / single / module"""
    t = (item_type or '').strip().lower()
    if t in ('module', 'mod'):
        return TYPE_MODULE
    if t in ('single', 'standalone', 'alone'):
        return TYPE_SINGLE
    return TYPE_COMPLETE


# ==================== 版本/已安装 ====================


def _alone_dir():
    """单文件插件目录 plugins/alone/"""
    return os.path.join(_plugins_dir(), _ALONE_DIR)


def _get_installed_alone_names():
    """plugins/alone/ 下的单文件插件 (文件名去掉 .py) 视为已安装插件名"""
    alone_dir = _alone_dir()
    if not os.path.isdir(alone_dir):
        return set()
    return {f[:-3] for f in os.listdir(alone_dir) if f.endswith('.py') and not f.startswith(('.', '_'))}


def _get_installed_names():
    """获取已安装的插件名列表 (独立目录 + plugins/alone/ 下的单文件插件)"""
    plugins_dir = _plugins_dir()
    if not os.path.isdir(plugins_dir):
        return set()
    names = {d for d in os.listdir(plugins_dir) if os.path.isdir(os.path.join(plugins_dir, d)) and not d.startswith(('.', '__'))}
    return names | _get_installed_alone_names()


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
        # 单文件插件: plugins/alone/<name>.py
        return _read_meta_version(os.path.join(_alone_dir(), f'{name}.py'), '__plugin_meta__')
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


def _alone_safe_name(plugin_name, url):
    """alone 单文件插件的安全名: 优先插件名, 否则回退到 URL 文件名。"""
    safe = _safe_name(plugin_name)
    if not safe:
        fname = url.split('/')[-1].split('?')[0]
        safe = _safe_name(fname[:-3] if fname.endswith('.py') else fname) or 'plugin'
    return safe


def _install_py(content, plugin_name, url):
    """单文件插件安装到 plugins/alone/<name>.py。"""
    safe = _alone_safe_name(plugin_name, url)
    alone_dir = _alone_dir()
    os.makedirs(alone_dir, exist_ok=True)
    rel = f'{_ALONE_DIR}/{safe}.py'
    with open(os.path.join(alone_dir, f'{safe}.py'), 'wb') as f:
        f.write(content)
    return {'success': True, 'message': f'已安装到 plugins/{rel}', 'path': f'plugins/{rel}', 'safe': safe}


def _split_paths(path):
    """path 归一为文件路径列表 (兼容数组/逗号串/单字符串)。"""
    items = [str(p) for p in path] if isinstance(path, (list, tuple)) else str(path or '').split(',')
    return [p.strip().strip('/') for p in items if p and p.strip()]


def _alone_dep_dest_name(basename, safe):
    """alone 依赖文件落盘名: requirements.txt→<safe>_requirements.txt, 已命名的原样保留, 其它返回 None。"""
    if basename == 'requirements.txt':
        return f'{safe}_requirements.txt'
    if basename.endswith('_requirements.txt'):
        return basename
    return None


async def _install_alone_extra(github_url, rel_path, branch, safe, mirror):
    """下载 path 里声明的依赖清单到 plugins/alone/。"""
    dest_name = _alone_dep_dest_name(os.path.basename(rel_path), safe)
    if not dest_name:
        return
    try:
        content = await _download_file(_repo_raw_url(github_url, rel_path, branch), mirror=mirror)
    except Exception:
        content = None
    if not content or content[:4] == b'PK\x03\x04' or b'<html' in content[:200].lower():
        return
    try:
        with open(os.path.join(_alone_dir(), dest_name), 'wb') as f:
            f.write(content)
        log.info(f'alone 插件 {safe} 依赖清单已保存: plugins/{_ALONE_DIR}/{dest_name}')
    except Exception as e:
        log.warning(f'保存 {dest_name} 失败: {e}')


def _resolve_subdir(flist, root_prefix, subdir_path):
    """解析仓库内子目录的提取前缀, 找不到返回 None"""
    p = (subdir_path or '').strip('/').replace('\\', '/')
    if not p:
        return root_prefix
    # 候选: path 本身是目录, 或 path 是文件时取其父目录
    candidates = [p]
    if '/' in p:
        candidates.append(p.rsplit('/', 1)[0])
    for cand in candidates:
        if not cand:
            continue
        prefix = f'{root_prefix}{cand}/'
        if any(f.startswith(prefix) for f in flist):
            return prefix
    return None


def _extract_zip_subset(content, plugin_name, subdir_path=''):
    """从仓库 zip 解压到 plugins/<name>/, subdir_path 非空时仅解压该子目录"""
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
            root_prefix = (list(roots)[0] + '/') if len(roots) == 1 else ''

            strip_prefix = _resolve_subdir(flist, root_prefix, subdir_path)
            if strip_prefix is None:
                return {'success': False, 'message': f'仓库内未找到: {subdir_path}'}
            selected = [f for f in flist if f.startswith(strip_prefix) and not f.endswith('/')]

            os.makedirs(dest_dir, exist_ok=True)
            extracted = []
            for fp in selected:
                if '__pycache__' in fp or '/.git/' in fp:
                    continue
                rel = fp[len(strip_prefix) :] if fp.startswith(strip_prefix) else fp
                if not rel:
                    continue
                dest = os.path.join(dest_dir, rel)
                os.makedirs(os.path.dirname(dest) or dest_dir, exist_ok=True)
                with zf.open(fp) as src, open(dest, 'wb') as dst:
                    dst.write(src.read())
                extracted.append(rel)
            if not extracted:
                return {'success': False, 'message': '未找到要安装的文件'}
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
    """安装/更新模块: 官方仓库只提取 modules/<name>/ 子目录, 第三方整仓库安装"""
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


async def _auto_enable_plugin(reload_name):
    """安装后自动加载插件; reload_name 为插件目录名 (single 共享安装时为 'alone')"""
    if not reload_name:
        return
    try:
        from core.application import get_app

        app = get_app()
        if not app or not app.plugin_manager:
            return
        await app.plugin_manager.reload(reload_name)
        log.info(f'插件 {reload_name} 已自动启用')
    except Exception as e:
        log.warning(f'插件自动启用失败 [{reload_name}]: {e}')


async def _install_complete(github_url, plugin_name, subdir_path='', branch='main', mirror=None):
    """完整插件: 拉取仓库 zip, 解压整仓库或指定子目录到 plugins/<name>/ (支持一仓库多插件)"""
    url = _github_to_archive(github_url, branch)
    label = f' [子目录 {subdir_path}]' if subdir_path else ''
    log.info(f'完整插件安装: {_safe_name(plugin_name)} ← {url}{label}')
    content = await _download_file(url, mirror=mirror)
    if content is None:
        return {'success': False, 'message': '下载失败, 请检查网络或镜像'}
    if content[:4] != b'PK\x03\x04':
        return {'success': False, 'message': '下载内容不是有效的 zip 文件'}
    return _extract_zip_subset(content, plugin_name, subdir_path=subdir_path)


async def _install_single(github_url, plugin_name, path='', branch='main', alone=True, mirror=None):
    """独立插件安装: alone=True 装到共享 plugins/alone/, alone=False 装到专属目录; 返回 (result, reload_target)。"""
    safe = _safe_name(plugin_name) or 'plugin'

    # 共享 alone 目录: .py 为插件主体, path 里其余文件当依赖清单
    if alone:
        files = _split_paths(path)
        py_files = [f for f in files if f.lower().endswith('.py')]
        main = py_files[0] if py_files else (files[0] if files else '')  # 无 .py 时退回首个文件/raw
        url = _repo_raw_url(github_url, main, branch) if main else _convert_github_url(github_url)
        content = await _download_file(url, mirror=mirror)
        if content is None:
            return {'success': False, 'message': '文件下载失败, 请检查路径或网络'}, None
        result = _install_py(content, plugin_name, url)
        if result.get('success'):
            for extra in files:
                if extra != main:
                    await _install_alone_extra(github_url, extra, branch, result.get('safe', ''), mirror)
        return result, _ALONE_DIR

    # 专属目录: path 单字符串, 误传数组取首个
    if isinstance(path, (list, tuple)):
        _lst = _split_paths(path)
        path = _lst[0] if _lst else ''
    p = (path or '').strip('/').replace('\\', '/')
    # 根级单文件: 直接下载, 避免整仓库 zip
    if p and '/' not in p and p.endswith('.py'):
        url = _repo_raw_url(github_url, p, branch)
        content = await _download_file(url, mirror=mirror)
        if content is None:
            return {'success': False, 'message': '文件下载失败, 请检查路径或网络'}, None
        dest_dir = os.path.join(_plugins_dir(), safe)
        os.makedirs(dest_dir, exist_ok=True)
        fname = os.path.basename(p)
        with open(os.path.join(dest_dir, fname), 'wb') as f:
            f.write(content)
        log.info(f'独立插件安装: {safe}/{fname}')
        return {'success': True, 'message': f'已安装到 plugins/{safe}/{fname}', 'path': f'plugins/{safe}', 'files': 1}, safe

    # 子目录或整仓库: zip 解压 (自动带上同目录 html 等附属文件)
    return await _install_complete(github_url, plugin_name, subdir_path=p, branch=branch, mirror=mirror), safe


async def handle_market_install(request: web.Request):
    """安装插件/模块"""
    body = await request.json()
    github_url = body.get('github', '') or body.get('url', '') or body.get('download_url', '')
    item_name = body.get('name', 'unknown')
    item_type = _canonical_type(body.get('type', ''))
    file_path = body.get('path', '')
    alone = bool(body.get('alone', True))
    branch = body.get('branch', 'main')
    mirror = body.get('mirror', '') or _load_market_mirror()
    if not github_url:
        return web.json_response({'success': False, 'message': '缺少下载地址'}, status=400)

    try:
        # 模块: 从仓库 zip 提取 modules/<name>/ 子目录
        if item_type == TYPE_MODULE:
            return web.json_response(await _install_module(github_url, item_name, branch, mirror=mirror))

        # 独立插件 (single)
        if item_type == TYPE_SINGLE:
            result, reload_target = await _install_single(github_url, item_name, path=file_path, branch=branch, alone=alone, mirror=mirror)
            if result.get('success'):
                await _auto_enable_plugin(reload_target)
            return web.json_response(result)

        # 完整插件 (complete): 整仓库 / 仓库内子目录
        result = await _install_complete(github_url, item_name, subdir_path=file_path, branch=branch, mirror=mirror)
        if result.get('success'):
            await _auto_enable_plugin(_safe_name(item_name))
        return web.json_response(result)
    except Exception as e:
        log.error(f'安装失败 [{item_name}]: {e}')
        return web.json_response({'success': False, 'message': str(e)})


# ==================== 卸载 ====================


def _remove_dir_keep_data(dest_dir):
    """删除目录中除 data/ 外的全部文件和子目录"""
    import shutil

    for item in os.listdir(dest_dir):
        if item == 'data':
            continue
        p = os.path.join(dest_dir, item)
        if os.path.isdir(p):
            shutil.rmtree(p)
        else:
            os.remove(p)


async def _unload_plugin_runtime(plugin_name):
    """从运行时卸载插件"""
    try:
        from core.application import get_app

        app = get_app()
        if app and app.plugin_manager:
            await app.plugin_manager.unload(plugin_name)
    except Exception:
        pass


async def handle_market_uninstall(request: web.Request):
    """卸载已安装的插件/模块"""
    body = await request.json()
    item_name = body.get('name', '')
    item_type = _canonical_type(body.get('type', ''))
    keep_data = body.get('keep_data', False)
    if not item_name:
        return web.json_response({'success': False, 'message': '缺少名称'}, status=400)

    safe = _safe_name(item_name)
    if not safe:
        return web.json_response({'success': False, 'message': '无效名称'}, status=400)

    if item_type == TYPE_MODULE:
        dest_dir = os.path.join(_modules_dir(), safe)
        label = f'modules/{safe}'
    else:
        if safe == 'system':
            return web.json_response({'success': False, 'message': '系统插件不可卸载'})
        dest_dir = os.path.join(_plugins_dir(), safe)
        label = f'plugins/{safe}'
        # 单文件插件: 删除单个 .py 文件
        if not os.path.isdir(dest_dir):
            alone_py = os.path.join(_alone_dir(), f'{safe}.py')
            if os.path.isfile(alone_py):
                try:
                    await _unload_plugin_runtime(_ALONE_DIR)
                    os.remove(alone_py)
                    # 一并清理该插件的依赖清单
                    alone_req = os.path.join(_alone_dir(), f'{safe}_requirements.txt')
                    if os.path.isfile(alone_req):
                        os.remove(alone_req)
                    log.info(f'plugins/{_ALONE_DIR}/{safe}.py 已卸载')
                    return web.json_response({'success': True, 'message': f'已卸载 plugins/{_ALONE_DIR}/{safe}.py'})
                except Exception as e:
                    return web.json_response({'success': False, 'message': f'删除失败: {e}'})

    if not os.path.isdir(dest_dir):
        return web.json_response({'success': False, 'message': f'{label} 不存在'})

    import shutil

    try:
        await _unload_plugin_runtime(safe)
        if keep_data and os.path.isdir(os.path.join(dest_dir, 'data')):
            _remove_dir_keep_data(dest_dir)
            log.info(f'{label} 已卸载 (保留 data/)')
            return web.json_response({'success': True, 'message': f'已卸载 {label} (保留数据)'})
        else:
            shutil.rmtree(dest_dir)
            log.info(f'{label} 已卸载')
            return web.json_response({'success': True, 'message': f'已卸载 {label}'})
    except Exception as e:
        return web.json_response({'success': False, 'message': f'删除失败: {e}'})
