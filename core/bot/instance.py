#!/usr/bin/env python
"""单机器人实例 — Token / Sender / WS / LogService 管理"""

import asyncio

from core.base.config import cfg
from core.base.logger import FRAMEWORK, get_logger
from core.message.sender import MessageSender
from core.network.access import TokenManager
from core.network.websocket import WSClient
from core.storage.log import LogService


class BotInstance:
    """单个机器人实例"""

    __slots__ = (
        'appid',
        'name',
        'secret',
        'bot_cfg',
        'token_manager',
        'sender',
        'ws_client',
        'log_service',
        'bot_id',
        'avatar_url',
        'robot_qq',
        'owner_ids',
    )

    def __init__(self, bot_cfg, base_log_dir):
        self.bot_cfg = bot_cfg
        self.appid = str(bot_cfg['appid'])
        self.name = self.appid
        self.secret = str(bot_cfg['secret'])

        custom_api_base = str(bot_cfg.get('api_base', '') or '')
        self.token_manager = TokenManager(self.appid, self.secret, api_base=custom_api_base)
        self.sender = MessageSender(self.token_manager, custom_api_base=custom_api_base)

        # 日志服务
        log_cfg = cfg.get('settings', 'logging') or {}
        self.log_service = LogService(
            base_dir=base_log_dir,
            appid=self.appid,
            wal_mode=log_cfg.get('wal_mode', True),
            insert_interval=log_cfg.get('insert_interval', 2),
            batch_size=log_cfg.get('batch_size', 0),
            retention_days=log_cfg.get('retention_days', 5),
        )

        self.robot_qq = str(bot_cfg.get('robot_qq', ''))
        self.owner_ids = bot_cfg.get('owner_ids', [])

        self.ws_client = None
        self.bot_id = ''
        self.avatar_url = ''

    async def start(self, on_event):
        """启动机器人: Token + 日志 + WS(可选)"""
        bot_log = get_logger(FRAMEWORK, self.name)
        bot_log.info(f'正在启动 (appid={self.appid})')

        await self.token_manager.ensure_token()
        await self.token_manager.start_auto_refresh()

        # 获取昵称 + 启动日志服务
        await asyncio.gather(self._fetch_bot_name(), self.log_service.start())
        self.sender.bind_instance(log_service=self.log_service, bot_name=self.name, bot_qq=self.robot_qq)

        ws_cfg = self.bot_cfg.get('websocket', {})
        if ws_cfg.get('enabled', False):
            identify_cfg = ws_cfg.get('identify', {}) or {}
            self.ws_client = WSClient(
                appid=self.appid,
                token_manager=self.token_manager,
                on_event=on_event,
                reconnect_interval=ws_cfg.get('reconnect_interval', 5),
                max_reconnects=ws_cfg.get('max_reconnects', -1),
                custom_url=ws_cfg.get('custom_url', ''),
                custom_api_base=str(self.bot_cfg.get('api_base', '') or ''),
                client_name=str(identify_cfg.get('name', '') or ''),
            )

        api_info = f', API={self.sender._base_url}' if self.sender._custom_api_base else ''
        bot_log.info(f'✅ 启动完成 (WS={"启用" if self.ws_client else "禁用"}{api_info})')

    async def _fetch_bot_name(self):
        """通过 GET /users/@me 获取机器人昵称"""
        try:
            token = await self.token_manager.get_token()
            client = await self.token_manager.get_client()
            resp = await client.get('/users/@me', headers={'Authorization': f'QQBot {token}'})
            if resp.status_code == 200:
                data = resp.json()
                name = data.get('username', '')
                self.bot_id = data.get('id', '')
                self.avatar_url = data.get('avatar', '')
                if name:
                    self.name = name
                    get_logger(FRAMEWORK, name).info(f'机器人昵称: {name}')
                    return
            get_logger(FRAMEWORK, self.appid).warning('获取机器人昵称失败, 使用 appid 代替')
        except Exception as e:
            get_logger(FRAMEWORK, self.appid).warning(f'获取机器人昵称异常: {e}, 使用 appid 代替')

    async def stop(self):
        """停止机器人"""
        tasks = []
        if self.ws_client:
            tasks.append(self.ws_client.close())
        tasks.extend(
            [
                self.log_service.shutdown(),
                self.sender.close(),
                self.token_manager.close(),
            ]
        )
        await asyncio.gather(*tasks, return_exceptions=True)
        get_logger(FRAMEWORK, self.name).info('已停止')
