"""多机器人注册表 — 实例管理 / 启动 / 同步 / 热重载"""

import asyncio
import logging

from core.base.config import cfg
from core.base.logger import SYSTEM, report_error
from core.base.tasks import spawn
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
        self._loop = None
        self._sender_log_cb = None

    @property
    def bots(self):
        return self._bots.copy()

    def get(self, appid):
        return self._bots.get(str(appid))

    def __iter__(self):
        return iter(self._bots.values())

    def __len__(self):
        return len(self._bots)

    # ---------- 启动 ----------

    async def start_all(self):
        """启动所有有效且启用的机器人"""
        self._loop = asyncio.get_running_loop()
        bot_configs = cfg.get_bot_configs()
        valid = [b for b in bot_configs if b.get('appid') and b.get('secret')]
        if not valid:
            log.warning('未配置有效的机器人')
            return
        enabled = [b for b in valid if b.get('enabled', True)]
        disabled = [b for b in valid if not b.get('enabled', True)]
        for b in disabled:
            log.info(f'机器人 {b["appid"]} 已关闭, 跳过启动')
        if not enabled:
            log.warning('所有机器人均已关闭')
            return
        results = await asyncio.gather(*(self._start_one(bc) for bc in enabled), return_exceptions=True)
        count = sum(1 for r in results if r is not None and not isinstance(r, Exception))
        log.info(f'已启动 {count} 个机器人')

    async def _start_one(self, bot_cfg):
        appid = str(bot_cfg['appid'])
        try:
            if self._loop is None:
                self._loop = asyncio.get_running_loop()
            instance = BotInstance(bot_cfg, self._log_base)
            await instance.start(self._on_event)
            if self._media_dir:
                instance.sender.bind_instance(media_dir=self._media_dir)
            if self._sender_log_cb:
                instance.sender._web_log_cb = self._sender_log_cb
            self._bots[appid] = instance
            if instance.ws_client:
                spawn(instance.ws_client.connect())
            return instance
        except Exception as e:
            report_error(SYSTEM, '启动器', e, context={'appid': appid})
            return None

    # ---------- 热重载 ----------

    def on_config_change(self, data):
        """bot.yaml 变更回调"""
        try:
            asyncio.get_running_loop()
            spawn(self._sync())
        except RuntimeError:
            loop = self._loop
            if loop is not None and loop.is_running():
                loop.call_soon_threadsafe(lambda: spawn(self._sync()))

    async def _sync(self):
        """同步 bot 配置与实际运行状态"""
        valid = {str(b['appid']): b for b in cfg.get_bot_configs() if b.get('appid') and b.get('secret')}
        current = set(self._bots)
        target = {aid for aid, b in valid.items() if b.get('enabled', True)}

        log.debug(f'_sync: to_add={target - current}, to_remove={current - target}')

        # 移除
        for appid in current - target:
            try:
                bot = self._bots.pop(appid)
                await bot.stop()
                bc = valid.get(appid)
                if bc and not bc.get('enabled', True):
                    self._push_web_log('framework', {'content': f'热重载: {bot.name} ({appid}) 已关闭'})
                else:
                    self._push_web_log('framework', {'content': f'热重载: {bot.name} ({appid}) 已移除'})
            except Exception as e:
                log.error(f'停止机器人 {appid} 失败: {e}')

        # 新增
        for appid in target - current:
            try:
                inst = await self._start_one(valid[appid])
                if inst:
                    self._push_web_log('framework', {'content': f'热重载: {inst.name} ({appid}) 已启动'})
                else:
                    log.warning(f'启动机器人 {appid} 返回 None')
                    self._push_web_log('framework', {'content': f'热重载: {appid} 启动失败'})
            except Exception as e:
                log.error(f'启动机器人 {appid} 异常: {e}')
                self._push_web_log('framework', {'content': f'热重载: {appid} 启动异常: {e}'})

        # 更新
        for appid in current & target:
            bot = self._bots[appid]
            new_cfg = valid[appid]
            new_secret = str(new_cfg.get('secret', ''))
            new_api_base = str(new_cfg.get('api_base', '') or '')

            if new_secret != bot.secret or new_api_base != (bot.token_manager.api_base if bot.token_manager else ''):
                await bot.stop()
                self._bots.pop(appid, None)
                inst = await self._start_one(new_cfg)
                if inst:
                    self._push_web_log('framework', {'content': f'热重载: {inst.name} ({appid}) 密钥已更新, 重新连接'})
                continue

            bot.bot_cfg = new_cfg
            bot.owner_ids = new_cfg.get('owner_ids', [])
            bot.robot_qq = str(new_cfg.get('robot_qq', ''))

            ws_cfg = new_cfg.get('websocket', {})
            if ws_cfg.get('enabled') and not bot.ws_client:
                identify_cfg = ws_cfg.get('identify', {}) or {}
                bot.ws_client = WSClient(
                    appid=appid,
                    token_manager=bot.token_manager,
                    on_event=self._on_event,
                    reconnect_interval=ws_cfg.get('reconnect_interval', 5),
                    max_reconnects=ws_cfg.get('max_reconnects', -1),
                    custom_url=ws_cfg.get('custom_url', ''),
                    custom_api_base=new_api_base,
                    client_name=str(identify_cfg.get('name', '') or ''),
                )
                spawn(bot.ws_client.connect())
            elif not ws_cfg.get('enabled') and bot.ws_client:
                await bot.ws_client.close()
                bot.ws_client = None

    # ---------- 关闭 ----------

    async def shutdown(self):
        if self._bots:
            await asyncio.gather(*(b.stop() for b in self._bots.values()), return_exceptions=True)
        self._bots.clear()
