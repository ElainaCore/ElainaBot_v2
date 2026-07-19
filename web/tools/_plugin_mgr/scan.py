"""插件扫描 — handle_scan_plugins / handle_scan_plugin_dirs"""

import os
from datetime import datetime

from aiohttp import web

from web.tools._plugin_mgr.shared import (
    ENTRY_CANDIDATES,
    find_entry,
    get_pm,
    log,
    plugins_dir,
)
from web.tools._python_source import read_dict_assignment

# ==================== 插件元信息查询 ====================


def _get_plugin_info():
    """从 PluginManager 获取已加载插件的注册命令和描述"""
    pm = get_pm()
    if not pm or not hasattr(pm, 'get_web_plugin_info'):
        return {}
    try:
        return pm.get_web_plugin_info()
    except Exception as e:
        log.error(f'获取插件信息失败: {e}')
        return {}


def _get_plugin_bots_map():
    """从 PluginManager 获取插件机器人绑定配置"""
    pm = get_pm()
    if not pm or not hasattr(pm, 'get_plugin_bots'):
        return {}
    try:
        return pm.get_plugin_bots()
    except Exception:
        return {}


# ==================== 文件扫描辅助 ====================


def _read_file_meta(py_path):
    """从 .py 文件静态解析 __plugin_meta__ 字典"""
    meta = read_dict_assignment(py_path, '__plugin_meta__')
    if meta is None:
        return None
    allowed = {'name', 'version', 'author', 'description'}
    return {k: str(v) for k, v in meta.items() if k in allowed and v}


def _scan_py_files(dir_path, prefix='', read_meta=False):
    """扫描 .py 文件 (只扫描 .py, 不再使用 .py.ban 禁用机制)"""
    files = []
    for fname in sorted(os.listdir(dir_path)):
        if fname.startswith('_'):
            continue
        if not fname.endswith('.py'):
            continue
        fpath = os.path.join(dir_path, fname)
        if not os.path.isfile(fpath):
            continue
        display = f'{prefix}{fname}' if prefix else fname
        stat = os.stat(fpath)
        info = {
            'name': display,
            'path': fpath.replace('\\', '/'),
            'enabled': True,
            'size': stat.st_size,
            'last_modified': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
        }
        if read_meta:
            meta = _read_file_meta(fpath)
            if meta:
                info['meta'] = meta
        files.append(info)
    return files


# ==================== 扫描实现 ====================


def _scan_plugins():
    pdir = plugins_dir()
    result = []
    if not os.path.isdir(pdir):
        return result
    plugin_info_map = _get_plugin_info()
    pm = get_pm()
    disabled_set = pm.get_disabled_plugins() if pm else set()

    for dir_name in os.listdir(pdir):
        plugin_dir = os.path.join(pdir, dir_name)
        if not os.path.isdir(plugin_dir) or dir_name.startswith(('_', '.')):
            continue
        is_system = dir_name == 'system'

        # 持久化禁用状态: 目录级 或 入口文件级
        entry_path = find_entry(plugin_dir)
        if entry_path:
            entry_stem = os.path.basename(entry_path)[:-3]
            persist_disabled = dir_name in disabled_set or f'{dir_name}/{entry_stem}' in disabled_set
        else:
            py_files = [f for f in os.listdir(plugin_dir) if f.endswith('.py') and not f.startswith('_')]
            if not py_files:
                continue
            entry_path = os.path.join(plugin_dir, py_files[0])
            persist_disabled = dir_name in disabled_set

        pinfo = plugin_info_map.get(dir_name, {})
        mtime = datetime.fromtimestamp(os.path.getmtime(entry_path)).strftime('%Y-%m-%d %H:%M:%S')
        is_large = find_entry(plugin_dir) is not None
        loaded = dir_name in (pm.plugins if pm else {})
        enabled = loaded and not persist_disabled

        result.append(
            {
                'name': dir_name,
                'status': 'disabled' if persist_disabled else ('loaded' if loaded else 'unloaded'),
                'path': entry_path.replace('\\', '/'),
                'directory': dir_name,
                'is_system': is_system,
                'is_large': is_large,
                'last_modified': mtime,
                'enabled': enabled,
                'commands': pinfo.get('commands', []),
                'description': pinfo.get('description', ''),
                'meta': pinfo.get('meta', {}),
            }
        )
    result.sort(key=lambda x: (0 if x['status'] == 'loaded' else 1 if x['status'] == 'unloaded' else 2))
    return result


def _scan_plugin_dirs():
    """按目录分组扫描所有 .py 文件 (禁用状态来自持久化)"""
    pdir = plugins_dir()
    dirs = []
    if not os.path.isdir(pdir):
        return dirs
    plugin_info_map = _get_plugin_info()
    bots_map = _get_plugin_bots_map()
    pm = get_pm()
    disabled_set = set(pm.get_disabled_plugins()) if pm else set()

    for dir_name in sorted(os.listdir(pdir)):
        dir_path = os.path.join(pdir, dir_name)
        if not os.path.isdir(dir_path) or dir_name.startswith(('.', '__')):
            continue
        is_system = dir_name == 'system'
        pinfo = plugin_info_map.get(dir_name, {})
        has_entry = any(e in os.listdir(dir_path) for e in ENTRY_CANDIDATES)
        files = _scan_py_files(dir_path, read_meta=not has_entry)

        # 入口文件禁用 = 整体禁用
        entry_file = next((f['name'] for f in files if f['name'] in ENTRY_CANDIDATES), None)
        entry_key = f'{dir_name}/{entry_file[:-3]}' if entry_file else ''
        persist_disabled = dir_name in disabled_set or entry_key in disabled_set

        # 标记文件级别的 enabled
        for f in files:
            fname = f['name'][:-3] if f['name'].endswith('.py') else f['name']
            if persist_disabled or f'{dir_name}/{fname}' in disabled_set:
                f['enabled'] = False

        for f in files:
            fname = f['name']
            if fname.endswith('.py'):
                fname = fname[:-3]
            f['allowed_bots'] = bots_map.get(f'{dir_name}/{fname}', [])

        if has_entry:
            app_dir = os.path.join(dir_path, 'app')
            if os.path.isdir(app_dir):
                sub_files = _scan_py_files(app_dir, prefix='app/')
                for f in sub_files:
                    stem = f['name'][:-3] if f['name'].endswith('.py') else f['name']
                    if persist_disabled or f'{dir_name}/{stem}' in disabled_set:
                        f['enabled'] = False
                files.extend(sub_files)

        if not files:
            continue

        loaded = dir_name in (pm.plugins if pm else {})
        dirs.append(
            {
                'directory': dir_name,
                'is_system': is_system,
                'enabled': loaded and not persist_disabled,
                'is_large': has_entry,
                'files': files,
                'allowed_bots': bots_map.get(dir_name, []),
                'commands': pinfo.get('commands', []),
                'description': pinfo.get('description', ''),
                'meta': pinfo.get('meta', {}),
            }
        )
    return dirs


# ==================== 路由处理器 ====================


async def handle_scan_plugins(request: web.Request):
    return web.json_response({'success': True, 'plugins': _scan_plugins()})


async def handle_scan_plugin_dirs(request: web.Request):
    return web.json_response({'success': True, 'dirs': _scan_plugin_dirs()})
