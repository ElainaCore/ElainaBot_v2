"""OneBot 适配器配置 — Value Object"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class OneBotConfig:
    """OneBot 适配器配置数据对象 (Value Object)

    封装默认值、注释和反序列化, 确保配置在创建后不可变 (逻辑上)。
    """

    ws_path: str = '/onebot'
    reverse_ws_urls: list[dict] = field(default_factory=lambda: [{'url': '', 'appid': ''}])
    reconnect_interval: int = 5
    access_token: str = ''
    heartbeat_interval: int = 30
    debug: bool = False

    @classmethod
    def defaults(cls) -> dict:
        """返回默认配置字典 (供 ModuleContext.ensure_config 使用)"""
        return {
            'ws_path': '/onebot',
            'reverse_ws_urls': [{'url': '', 'appid': ''}],
            'reconnect_interval': 5,
            'access_token': '',
            'heartbeat_interval': 30,
            'debug': False,
        }

    @classmethod
    def comments(cls) -> dict:
        """返回配置项注释字典"""
        return {
            'ws_path': '正向 WS 路由路径 (挂载到框架端口, 如 /onebot)',
            'reverse_ws_urls': '反向 WS 列表, 格式: [{url: ws://..., appid: 你的appid}]',
            'reconnect_interval': '反向 WS 断线重连间隔 (秒)',
            'access_token': '鉴权 Token, 为空则不鉴权',
            'heartbeat_interval': '心跳间隔 (秒)',
            'debug': '调试模式, 输出完整 WS 收发载荷',
        }

    @classmethod
    def from_dict(cls, d: dict) -> OneBotConfig:
        """从配置字典构造, 缺失字段使用默认值"""
        return cls(
            ws_path=d.get('ws_path', '/onebot'),
            reverse_ws_urls=d.get('reverse_ws_urls', [{'url': '', 'appid': ''}]),
            reconnect_interval=d.get('reconnect_interval', 5),
            access_token=d.get('access_token', ''),
            heartbeat_interval=d.get('heartbeat_interval', 30),
            debug=d.get('debug', False),
        )

    @property
    def has_token(self) -> bool:
        return bool(self.access_token)
