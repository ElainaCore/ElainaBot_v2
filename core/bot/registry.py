"""多机器人注册表 — 实例管理 / 启动 / 同步 / 热重载"""

import asyncio
import contextlib
import logging

from core.base.config import cfg
from core.base.logger import SYSTEM, report_error
from core.bot.instance import BotInstance
from core.network.websocket import WSClient

log = logging.getLogger('ElainaBot.registry')


class BotRegistry:
    """管理多个 BotInstance 的生命周期和配置热重载"""

    def __init__(self, log_base: str, on_event=None, push_web_log=None, media_dir=''):
        self._bots: dict[str, object] = {}  # {appid: BotInstance}
        self._log_base = log_base
        self._on_event = on_event
        self._push_web_log = push_web_log or (lambda t, e: None)
        self._media_dir = media_dir

    @property
    def bots(self):
        return self._bots

    def get(self, appid):
        return self._bots.get(str(appid))

    def __iter__(self):
        return iter(self._bots.values())

    def __len__(self):
        return len(self._bots)

    # ---------- 启动 ----------

    async def start_all(self):
        """启动所有有效机器人"""
        bot_configs = cfg.get_bot_configs()
        valid = [b for b in bot_configs if b.get('appid') and b.get('secret')]
        if not valid:
            log.warning('未配置有效的机器人')
            return
        results = await asyncio.gather(*(self._start_one(bc) for bc in valid), return_exceptions=True)
        count = sum(1 for r in results if r is not None and not isinstance(r, Exception))
        log.info(f'已启动 {count} 个机器人')

    async def _start_one(self, bot_cfg):
        appid = str(bot_cfg['appid'])
        try:
            instance = BotInstance(bot_cfg, self._log_base)
            await instance.start(self._on_event)
            if self._media_dir:
                instance.sender.bind_instance(media_dir=self._media_dir)
            self._bots[appid] = instance
            if instance.ws_client:
                asyncio.create_task(instance.ws_client.connect())
            return instance
        except Exception as e:
            report_error(SYSTEM, '启动器', e, context={'appid': appid})
            return None

    # ---------- 热重载 ----------

    def on_config_change(self, data):
        """bot.yaml 变更回调 (在后台线程中触发)"""
        with contextlib.suppress(RuntimeError):
            asyncio.get_running_loop().create_task(self._sync())

    async def _sync(self):
        """同步 bot 配置: 新增/移除/更新 WebSocket"""
        valid = {str(b['appid']): b for b in cfg.get_bot_configs() if b.get('appid') and b.get('secret')}
        current = set(self._bots)
        target = set(valid)

        # 移除
        for appid in current - target:
            bot = self._bots.pop(appid)
            await bot.stop()
            self._push_web_log('framework', {'content': f'热重载: {bot.name} ({appid}) 已移除'})

        # 新增
        for appid in target - current:
            inst = await self._start_one(valid[appid])
            if inst:
                self._push_web_log('framework', {'content': f'热重载: {inst.name} ({appid}) 已启动'})

        # 更新 WebSocket 配置
        for appid in current & target:
            bot = self._bots[appid]
            new_cfg = valid[appid]
            bot.bot_cfg = new_cfg
            bot.owner_ids = new_cfg.get('owner_ids', [])
            bot.robot_qq = str(new_cfg.get('robot_qq', ''))

            ws_cfg = new_cfg.get('websocket', {})
            if ws_cfg.get('enabled') and not bot.ws_client:
                bot.ws_client = WSClient(
                    appid=appid,
                    token_manager=bot.token_manager,
                    on_event=self._on_event,
                    reconnect_interval=ws_cfg.get('reconnect_interval', 5),
                    max_reconnects=ws_cfg.get('max_reconnects', -1),
                    custom_url=ws_cfg.get('custom_url', ''),
                    custom_api_base=str(new_cfg.get('api_base', '') or ''),
                )
                asyncio.create_task(bot.ws_client.connect())
            elif not ws_cfg.get('enabled') and bot.ws_client:
                await bot.ws_client.close()
                bot.ws_client = None

    # ---------- 关闭 ----------

    async def shutdown(self):
        if self._bots:
            await asyncio.gather(*(b.stop() for b in self._bots.values()), return_exceptions=True)
        self._bots.clear()
