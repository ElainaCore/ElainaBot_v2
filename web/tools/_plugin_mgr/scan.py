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


def _scan_py_files(dir_path, prefix=''):
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
        files.append(
            {
                'name': display,
                'path': fpath.replace('\\', '/'),
                'enabled': True,
                'size': stat.st_size,
                'last_modified': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
            }
        )
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

        # 持久化禁用状态优先
        persist_disabled = dir_name in disabled_set
        entry_path = find_entry(plugin_dir)
        if not entry_path:
            py_files = [f for f in os.listdir(plugin_dir) if f.endswith('.py') and not f.startswith('_')]
            if not py_files:
                continue
            entry_path = os.path.join(plugin_dir, py_files[0])

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
    disabled_set = pm.get_disabled_plugins() if pm else set()

    for dir_name in sorted(os.listdir(pdir)):
        dir_path = os.path.join(pdir, dir_name)
        if not os.path.isdir(dir_path) or dir_name.startswith(('.', '__')):
            continue
        is_system = dir_name == 'system'
        persist_disabled = dir_name in disabled_set
        pinfo = plugin_info_map.get(dir_name, {})
        files = _scan_py_files(dir_path)

        # 标记文件级别的 enabled (持久化禁用则全部标记为 disabled)
        if persist_disabled:
            for f in files:
                f['enabled'] = False

        for f in files:
            fname = f['name']
            if fname.endswith('.py'):
                fname = fname[:-3]
            f['allowed_bots'] = bots_map.get(f'{dir_name}/{fname}', [])

        has_entry = any(f['name'] in ENTRY_CANDIDATES for f in files)
        if has_entry:
            app_dir = os.path.join(dir_path, 'app')
            if os.path.isdir(app_dir):
                sub_files = _scan_py_files(app_dir, prefix='app/')
                if persist_disabled:
                    for f in sub_files:
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
