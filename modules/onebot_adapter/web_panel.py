"""OneBot 网络配置 Web 面板 — 注册面板页面与配置 API

复用框架的 Web 面板扩展机制 (core.plugin.web_pages), 模块与插件共用同一注册表:
  - register_page:  在面板侧边栏注册「OneBot 网络」页 (iframe 加载 panel.html)
  - register_route: 注册 /api/ext/onebot/* 配置接口 (复用后台登录 token 鉴权)
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from aiohttp import web

from core.plugin.web_pages import register_page, register_route, unregister_page, unregister_route

if TYPE_CHECKING:
    from modules.onebot_adapter.adapter import OneBotAdapter

PAGE_KEY = 'onebot-network'
API_PATH = '/api/ext/onebot/network'

_adapter: OneBotAdapter | None = None


def register(adapter: OneBotAdapter) -> None:
    """注册面板页面与 API 路由"""
    global _adapter
    _adapter = adapter

    register_page(
        key=PAGE_KEY,
        label='OneBot 网络',
        source='module',
        source_name='onebot_adapter',
        html_file=os.path.join(os.path.dirname(__file__), 'panel.html'),
        icon='wifi',
    )
    register_route('GET', API_PATH, _handle_get_network)
    register_route('POST', API_PATH, _handle_save_network)


def unregister() -> None:
    """注销面板页面与 API 路由 (模块禁用时调用)"""
    global _adapter
    _adapter = None
    unregister_page(PAGE_KEY)
    unregister_route('GET', API_PATH)
    unregister_route('POST', API_PATH)


async def _handle_get_network(request: web.Request) -> web.Response:
    """获取网络连接配置与运行状态"""
    if _adapter is None:
        return web.json_response({'success': False, 'error': 'OneBot 适配器未启用'}, status=503)
    return web.json_response(
        {
            'success': True,
            'connections': _adapter.cfg.connections,
            'heartbeat_interval': _adapter.cfg.heartbeat_interval,
            'debug': _adapter.cfg.debug,
            'status': _adapter.network_status(),
        }
    )


async def _handle_save_network(request: web.Request) -> web.Response:
    """保存网络连接配置并热重载网络层"""
    if _adapter is None:
        return web.json_response({'success': False, 'error': 'OneBot 适配器未启用'}, status=503)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({'success': False, 'error': '无效的 JSON 请求体'}, status=400)

    connections = body.get('connections')
    if not isinstance(connections, list):
        return web.json_response({'success': False, 'error': 'connections 必须是列表'}, status=400)

    raw_config = {
        'connections': connections,
        'heartbeat_interval': body.get('heartbeat_interval', _adapter.cfg.heartbeat_interval),
        'debug': body.get('debug', _adapter.cfg.debug),
    }
    try:
        await _adapter.apply_config(raw_config)
    except Exception as e:
        return web.json_response({'success': False, 'error': f'应用配置失败: {e}'}, status=500)
    return web.json_response({'success': True, 'status': _adapter.network_status()})
