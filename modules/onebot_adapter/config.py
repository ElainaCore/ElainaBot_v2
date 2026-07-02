"""OneBot 适配器配置 — Value Object

连接以列表形式管理 (类似 NapCat / OneBot 实现端的网络配置):
  ws_server    — 正向 WS: 框架作为服务端, 外部框架连入 ws://host:port{path};
                 配置独立端口 (port) 时监听该端口且不校验路径, 可直接连接 ws://host:port
  ws_reverse   — 反向 WS: 框架作为客户端, 主动连接外部框架的 WS 地址
  http_server  — 正向 HTTP: 框架提供 OneBot HTTP API (POST {path}/{action})
  http_webhook — 反向 HTTP: 框架将事件 POST 上报到外部 URL (HTTP POST 上报)
"""

from __future__ import annotations

from dataclasses import dataclass, field

CONN_TYPES = ('ws_server', 'ws_reverse', 'http_server', 'http_webhook')


def normalize_connection(conn: dict) -> dict:
    """补全连接配置的缺省字段"""
    c = dict(conn or {})
    ctype = str(c.get('type', 'ws_server'))
    if ctype not in CONN_TYPES:
        ctype = 'ws_server'
    c['type'] = ctype
    c['name'] = str(c.get('name', '') or ctype)
    c['enable'] = bool(c.get('enable', True))
    c['access_token'] = str(c.get('access_token', '') or '')
    c['appid'] = str(c.get('appid', '') or '')
    if ctype in ('ws_server', 'http_server'):
        path = str(c.get('path', '') or ('/onebot' if ctype == 'ws_server' else '/onebot/http'))
        if not path.startswith('/'):
            path = '/' + path
        c['path'] = path
        c.pop('url', None)
        if ctype == 'ws_server':
            c['port'] = int(c.get('port', 0) or 0)
    else:
        c['url'] = str(c.get('url', '') or '')
        c.pop('path', None)
    if ctype == 'ws_reverse':
        c['reconnect_interval'] = int(c.get('reconnect_interval', 5) or 5)
    if ctype == 'http_webhook':
        c['secret'] = str(c.get('secret', '') or '')
        c['timeout'] = int(c.get('timeout', 10) or 10)
    return c


@dataclass
class OneBotConfig:
    """OneBot 适配器配置数据对象 (Value Object)"""

    connections: list[dict] = field(default_factory=list)
    heartbeat_interval: int = 30
    debug: bool = False

    @classmethod
    def defaults(cls) -> dict:
        """返回默认配置字典 (供 ModuleContext.ensure_config 使用)"""
        return {
            'connections': [
                {
                    'type': 'ws_server',
                    'name': '正向WS',
                    'enable': True,
                    'path': '/onebot',
                    'access_token': '',
                    'appid': '',
                }
            ],
            'heartbeat_interval': 30,
            'debug': False,
        }

    @classmethod
    def comments(cls) -> dict:
        """返回配置项注释字典"""
        return {
            'connections': '网络连接列表 (可在 Web 面板「OneBot 网络」页可视化管理), type: ws_server/ws_reverse/http_server/http_webhook; ws_server 可设 port 独立监听端口 (不校验路径)',
            'heartbeat_interval': '心跳间隔 (秒)',
            'debug': '调试模式, 输出完整收发载荷',
        }

    @classmethod
    def migrate_legacy(cls, d: dict) -> dict | None:
        """将旧版扁平配置 (ws_path/reverse_ws_urls/...) 迁移为 connections 列表

        返回迁移后的完整配置 dict; 无需迁移时返回 None。
        """
        if 'connections' in d or not any(k in d for k in ('ws_path', 'reverse_ws_urls', 'access_token')):
            return None
        token = str(d.get('access_token', '') or '')
        interval = int(d.get('reconnect_interval', 5) or 5)
        conns: list[dict] = [
            {
                'type': 'ws_server',
                'name': '正向WS',
                'enable': True,
                'path': str(d.get('ws_path', '/onebot') or '/onebot'),
                'access_token': token,
                'appid': '',
            }
        ]
        for e in d.get('reverse_ws_urls') or []:
            if isinstance(e, dict) and str(e.get('url', '')).strip():
                conns.append(
                    {
                        'type': 'ws_reverse',
                        'name': '反向WS',
                        'enable': True,
                        'url': str(e.get('url', '')).strip(),
                        'appid': str(e.get('appid', '') or ''),
                        'access_token': token,
                        'reconnect_interval': interval,
                    }
                )
        return {
            'connections': conns,
            'heartbeat_interval': int(d.get('heartbeat_interval', 30) or 30),
            'debug': bool(d.get('debug', False)),
        }

    @classmethod
    def from_dict(cls, d: dict) -> OneBotConfig:
        """从配置字典构造, 缺失字段使用默认值"""
        raw_conns = d.get('connections')
        if not isinstance(raw_conns, list):
            raw_conns = cls.defaults()['connections']
        return cls(
            connections=[normalize_connection(c) for c in raw_conns if isinstance(c, dict)],
            heartbeat_interval=int(d.get('heartbeat_interval', 30) or 30),
            debug=bool(d.get('debug', False)),
        )

    def by_type(self, ctype: str, *, enabled_only: bool = True) -> list[dict]:
        """按类型筛选连接"""
        return [c for c in self.connections if c['type'] == ctype and (c['enable'] or not enabled_only)]
