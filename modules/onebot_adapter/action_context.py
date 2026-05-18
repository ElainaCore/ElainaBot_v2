"""Action 执行上下文 — Dependency Injection 容器

封装所有 Action 可能需要的服务依赖, 通过 dataclass 实现,
避免 God Object 传递, 同时保持简洁。
"""

from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from core.message.sender import MessageSender
from modules.onebot_adapter.lib.id_mapper import IDMapper

if TYPE_CHECKING:
    from core.bot.manager import BotManager
    from core.message.event import Event
    from core.storage.log import LogService


@dataclass
class ActionContext:
    """Action 执行上下文 — 封装所有 Action 可能需要的服务依赖"""

    log: Any  # 日志器 (来自框架, 接口不透明)
    senders: dict[int, MessageSender] = field(default_factory=dict)  # {appid: sender}
    log_services: dict[int, LogService] = field(default_factory=dict)  # {appid: LogService}
    id_mapper: IDMapper | None = None
    msg_id_cache: OrderedDict[tuple[int, int | str], int | str] = field(default_factory=OrderedDict)  # {(appid, chat_id): msg_id}
    qq_map: dict[str, int] = field(default_factory=dict)  # {appid_str: robot_qq_int}
    default_qq: int = 0
    current_appid: str = ''  # 当前 action 上下文的 appid
    bm: BotManager | None = None  # BotManager 引用 (用于 push_ws 获取 bot 信息)

    # ==================== 服务访问 ====================

    def get_sender(self, appid: str = '') -> MessageSender | None:
        """获取消息发送器 (优先匹配 appid, 否则返回任意)"""
        target = appid or self.current_appid
        return self.senders.get(target) or next(iter(self.senders.values()), None)

    def get_log_service(self, appid: str = '') -> LogService | None:
        """获取 LogService"""
        aid = appid or self.current_appid
        return self.log_services.get(aid) or next(iter(self.log_services.values()), None)

    def find_msg_id(self, chat_id: int | str) -> int | str | None:
        """从缓存查找 msg_id"""
        for appid in self.senders:
            mid = self.msg_id_cache.get((appid, chat_id))
            if mid:
                return mid
        return None

    # ==================== 日志推送 ====================

    def push_ws(
        self,
        *,
        user_id: str = '',
        group_id: str = '',
        content: str = '',
        is_bot: bool = False,
        appid: str = '',
        direction: str = '',
    ) -> None:
        """实时推送到 web 面板日志流"""
        try:
            import web.ws as _ws

            aid = appid or self.current_appid
            bot = self.bm._bots.get(aid) if self.bm else None
            _ws.push_log(
                'message',
                {
                    'appid': aid,
                    'bot_name': getattr(bot, 'name', '') if bot else '',
                    'bot_qq': getattr(bot, 'robot_qq', '') if bot else '',
                    'user_id': user_id,
                    'group_id': group_id,
                    'content': content,
                    'is_bot': is_bot,
                    'direction': direction,
                    'source': 'onebot',
                },
            )
        except Exception:
            pass

    async def log_send(
        self,
        msg_type: str,
        target_id: int | str,
        content: str,
        ok: bool,
        resp_data: Any,
    ) -> None:
        """记录发送消息到 SQLite + 实时推送"""
        gid = str(target_id) if msg_type == 'group' else ''
        uid = str(target_id) if msg_type == 'private' else ''
        raw = json.dumps({'ok': ok, 'resp': str(resp_data)[:500]}, ensure_ascii=False)
        ls = self.get_log_service()
        if ls:
            await ls.add(
                'message',
                {
                    'type': 'onebot_send',
                    'user_id': uid,
                    'group_id': gid,
                    'content': content,
                    'raw_message': raw,
                    'plugin_name': 'onebot_adapter',
                    'direction': 'send',
                },
            )
        self.push_ws(
            user_id=uid,
            group_id=gid,
            content=content,
            is_bot=True,
            direction='send',
        )

    async def log_recv(self, appid: str, event: Event, ob_event: dict[str, Any]) -> None:
        """记录接收的 OneBot 事件 JSON 到 SQLite (不推实时日志)"""
        if ob_event.get('post_type') != 'message':
            return
        raw = json.dumps(ob_event, ensure_ascii=False)
        ls = self.get_log_service(appid)
        if ls:
            await ls.add(
                'message',
                {
                    'type': 'onebot_recv',
                    'user_id': event.user_id or '',
                    'group_id': event.group_id or '',
                    'content': ob_event.get('raw_message', ''),
                    'raw_message': raw,
                    'plugin_name': 'onebot_adapter',
                    'direction': 'receive',
                },
            )
