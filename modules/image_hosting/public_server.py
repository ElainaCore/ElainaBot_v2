"""自身图床公开读取路由，由图床模块通过框架扩展路由接口注册。"""

from aiohttp import web

from core.base.logger import EXTENSION, get_logger
from core.plugin.web_pages import match_route, register_route, unregister_route

log = get_logger(EXTENSION, '图床服务')

PUBLIC_ROUTE = '/api/ext/image-hosting'
_hosting = None


def attach(hosting) -> None:
    """注册免鉴权公开路由；重复调用会热更新当前图床服务实例。"""
    global _hosting
    _hosting = hosting
    register_route('GET', PUBLIC_ROUTE, _handle_image, auth=False)
    log.info(f'自身图床公开路由已注册: {PUBLIC_ROUTE}?filename=<文件名>')


def detach(hosting) -> None:
    """注销当前实例的公开路由。"""
    global _hosting
    if _hosting is hosting:
        _hosting = None
        entry = match_route('GET', PUBLIC_ROUTE)
        if entry and entry.get('handler') is _handle_image:
            unregister_route('GET', PUBLIC_ROUTE)


async def _handle_image(request: web.Request):
    """无鉴权读取自身图床文件，模块停用或文件不存在时返回 404。"""
    bed = _hosting.get_bed('self_hosted') if _hosting else None
    path = bed.resolve_file(request.query.get('filename', '')) if bed else None
    if path is None:
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers=bed.response_headers())
