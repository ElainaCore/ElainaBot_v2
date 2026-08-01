"""插件市场 — 市场列表/分类/详情/刷新/镜像 API"""

from aiohttp import web

import web.tools._market.fetch as _fetch_mod
from web.tools._market.fetch import _extract_plugins, _fetch_plugin_json
from web.tools._market.install import (
    TYPE_MODULE,
    _canonical_type,
    _get_installed_module_names,
    _get_installed_names,
    _get_local_module_version,
    _get_local_plugin_version,
    _version_lt,
)
from web.tools._market.shared import (
    _load_market_mirror,
    _safe_name,
    _save_market_mirror,
)
from web.tools._updater.mirror import _test_one_mirror
from web.tools._updater.shared import GITHUB_FILE_MIRRORS, _load_mirror_cache


async def handle_market_list(request: web.Request):
    """获取插件市场列表"""
    search = request.query.get('search', '').lower()
    category = request.query.get('category', '')
    force = request.query.get('refresh', '') == '1'
    data = await _fetch_plugin_json(force=force)
    if data is None:
        return web.json_response({'success': False, 'message': '无法连接插件库, 请检查网络'})

    plugins = [dict(plugin) for plugin in _extract_plugins(data)]
    if category:
        plugins = [p for p in plugins if p.get('category', '') == category]
    if search:
        plugins = [
            p for p in plugins if search in p.get('name', '').lower() or search in p.get('description', '').lower() or search in p.get('author', '').lower()
        ]

    # 标记已安装状态 + 版本对比
    installed_plugins = _get_installed_names()
    installed_modules = _get_installed_module_names()
    for p in plugins:
        safe = _safe_name(p.get('name', ''))
        if _canonical_type(p.get('type', '')) == TYPE_MODULE:
            p['installed'] = safe in installed_modules
            if p['installed']:
                local_ver = _get_local_module_version(safe)
                p['local_version'] = local_ver
                p['has_update'] = _version_lt(local_ver, p.get('version', ''))
        else:
            p['installed'] = safe in installed_plugins
            if p['installed']:
                local_ver = _get_local_plugin_version(safe)
                p['local_version'] = local_ver
                p['has_update'] = _version_lt(local_ver, p.get('version', ''))

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
    return web.json_response({'success': True, 'data': match}) if match else web.json_response({'success': False, 'message': '插件不存在'})


async def handle_market_refresh(request: web.Request):
    """强制刷新插件库缓存"""
    _fetch_mod._plugin_cache, _fetch_mod._plugin_cache_ts = None, 0
    data = await _fetch_plugin_json(force=True)
    if data is None:
        return web.json_response({'success': False, 'message': '刷新失败, 无法连接插件库'})
    total = len(_extract_plugins(data))
    return web.json_response({'success': True, 'message': f'已刷新, 共 {total} 个插件'})


# ==================== 市场镜像 API ====================


async def handle_market_get_mirror(request: web.Request):
    """获取当前市场镜像偏好 + 可用镜像列表"""
    cached = _load_mirror_cache()
    return web.json_response(
        {
            'success': True,
            'mirror': _load_market_mirror(),
            'mirrors': list(GITHUB_FILE_MIRRORS),
            'fast_mirrors': cached,
        }
    )


async def handle_market_set_mirror(request: web.Request):
    """设置市场镜像偏好"""
    body = await request.json()
    mirror = body.get('mirror', '')
    _save_market_mirror(mirror)
    return web.json_response({'success': True, 'message': f'镜像已设为: {mirror or "(自动选择)"}'})


async def handle_market_test_mirror(request: web.Request):
    """测试单个镜像延迟"""
    body = await request.json()
    mirror = body.get('mirror', '')
    result = await _test_one_mirror(mirror, timeout=5)
    return web.json_response({'success': True, 'data': result})
