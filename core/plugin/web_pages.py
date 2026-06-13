"""Web 面板自定义页面注册表"""

from __future__ import annotations

_registry: dict = {}  # {key: page_info}


def register_page(
    key: str,
    label: str,
    *,
    source: str = 'plugin',
    source_name: str = '',
    html: str = '',
    html_file: str = '',
    icon: str = '',
):
    """注册自定义 Web 面板页面"""
    _registry[key] = {
        'key': key,
        'label': label,
        'source': source,
        'source_name': source_name,
        'html': html,
        'html_file': html_file,
        'icon': icon,
    }


def unregister_page(key: str):
    """注销页面"""
    _registry.pop(key, None)


def get_pages() -> list:
    """获取所有已注册页面 (不含 html 内容)"""
    return [{k: v for k, v in p.items() if k not in ('html', 'html_file')} for p in _registry.values()]


def get_page_html(key: str) -> str | None:
    """获取指定页面的 HTML 内容"""
    info = _registry.get(key)
    if not info:
        return None
    if info.get('html'):
        return str(info['html'])
    path = info.get('html_file')
    if not path:
        return '<p>空页面</p>'
    try:
        with open(path, encoding='utf-8') as f:
            return f.read()
    except Exception:
        return '<p style="color:red">页面文件加载失败</p>'


# ==================== 自定义 HTTP 路由 ====================
# 插件可注册自己的 Web 路由 (统一挂在 /api/ext/ 前缀下), 由 web 层的动态分发器
# 在请求时查表执行, 因此插件热重载/卸载即时生效。默认需要框架登录 token, 可显式
# auth=False 开放免验证路由 (如对外回调、健康检查)。

_routes: dict = {}  # {(METHOD, path): route_info}
_ROUTE_PREFIX = '/api/ext/'


def register_route(method: str, path: str, handler=None, *, auth: bool = True):
    """注册插件 HTTP 路由 (路径需以 /api/ext/ 开头)。

    用法 (装饰器)::

        @register_route('GET', '/api/ext/myplugin/ping', auth=False)
        async def ping(request):
            return web.json_response({'ok': True})

    或直接调用: ``register_route('POST', '/api/ext/myplugin/do', do_handler)``。

    auth=True (默认) 时复用框架登录 token 鉴权; auth=False 时开放免验证。
    """
    from core.plugin import context as _ctx

    owner = getattr(getattr(_ctx, 'ctx', None), 'name', '') or ''
    method = str(method).upper()
    if not path.startswith(_ROUTE_PREFIX):
        raise ValueError(f'插件路由路径必须以 {_ROUTE_PREFIX} 开头: {path}')

    def _add(fn):
        _routes[(method, path)] = {
            'method': method,
            'path': path,
            'handler': fn,
            'auth': bool(auth),
            'owner': owner,
        }
        return fn

    return _add(handler) if handler is not None else _add


def unregister_route(method: str, path: str):
    """注销路由"""
    _routes.pop((str(method).upper(), path), None)


def match_route(method: str, path: str):
    """精确匹配已注册路由; HEAD 回退到 GET。未命中返回 None。"""
    m = str(method).upper()
    entry = _routes.get((m, path))
    if entry is None and m == 'HEAD':
        entry = _routes.get(('GET', path))
    return entry


def get_routes() -> list:
    """获取所有已注册路由 (不含 handler)"""
    return [{k: v for k, v in r.items() if k != 'handler'} for r in _routes.values()]


def clear_routes_by_owner(owner: str) -> int:
    """注销某插件注册的全部路由 (插件卸载时由框架自动调用); 返回清理数量。"""
    keys = [k for k, v in _routes.items() if v.get('owner') == owner]
    for k in keys:
        _routes.pop(k, None)
    return len(keys)
