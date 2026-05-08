#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""多机器人管理器 — 启动 / 关闭 / HTTP / 配置同步 / 定时任务"""

import os
import json
import time
import shutil
import logging
import asyncio
from datetime import datetime, timedelta
from aiohttp import web
from core.base.config import cfg
from core.base.logger import get_logger, setup as setup_logger, SYSTEM, report_error
from core.base.sign import verify_and_respond
from core.message.event import Event
from core.plugin.manager import PluginManager
from core.storage.log import SharedLogService
from core.storage.dau import DAUService
from core.module.manager import ModuleManager
from core.network.websocket import WSClient
from core.bot.instance import BotInstance
from core.bot.event import EventHandlerMixin

log = get_logger(SYSTEM, "启动器")

_bot_manager_ref = None


class BotManager(EventHandlerMixin):

    def __init__(self):
        self._bots = {}
        self._plugin_manager = None
        self._module_manager = None
        self._dau_service = None
        self._shared_log = None
        self._app = None
        self._runner = None
        self._base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self._web_log_cb = None
        self._log_base = ''
        self._media_dir = ''
        self._stop_event = None
        self._restart_requested = False
        self._init_event_state()

    @property
    def dau_service(self):
        return self._dau_service

    @property
    def module_manager(self):
        return self._module_manager

    @property
    def plugin_manager(self):
        return self._plugin_manager

    def _path(self, *parts):
        return os.path.join(self._base_dir, *parts)

    def _cleanup_deprecated(self):
        """启动时删除 web/deprecated_files.json 中列出的废弃文件"""
        dep_file = self._path('web', 'deprecated_files.json')
        if not os.path.isfile(dep_file):
            return
        try:
            with open(dep_file, 'r', encoding='utf-8') as f:
                entries = json.load(f)
        except Exception:
            return
        if not isinstance(entries, list):
            return
        cache_dirs = set()
        for rel in entries:
            target = self._path(rel.replace('\\', '/'))
            if os.path.isfile(target):
                try:
                    os.remove(target)
                    log.info(f"已删除废弃文件: {rel}")
                except Exception as e:
                    log.warning(f"删除废弃文件失败 {rel}: {e}")
            cache_dirs.add(os.path.join(os.path.dirname(target), '__pycache__'))
        for cd in cache_dirs:
            if os.path.isdir(cd):
                shutil.rmtree(cd, ignore_errors=True)

    # ==================== 启动 ==

    async def start(self):
        global _bot_manager_ref
        _bot_manager_ref = self

        cfg.init(self._path('config'))

        fw_name = cfg.get('settings', 'web.framework_name', 'ElainaBot')
        setup_logger(framework_name=fw_name)
        log.info(f"{'='*5} {fw_name} 启动中 {'='*5}")

        self._cleanup_deprecated()

        bot_configs = cfg.get_bot_configs()
        valid_bots = [b for b in bot_configs if b.get('appid') and b.get('secret')]
        if not valid_bots:
            log.warning("未配置有效的机器人, 仅启动 Web 面板")

        self._init_http_app()

        self._module_manager = ModuleManager(self._path('modules'))
        self._module_manager.discover()
        await self._module_manager.start_enabled()

        self._plugin_manager = PluginManager(self._path('plugins'))
        await self._plugin_manager.load_all()
        self._plugin_manager.start_watcher()

        log_base = self._path('data', cfg.get('settings', 'logging.dir', 'log'))
        log_cfg = cfg.get('settings', 'logging') or {}
        self._shared_log = SharedLogService(
            base_dir=log_base,
            wal_mode=log_cfg.get('wal_mode', True),
            insert_interval=log_cfg.get('insert_interval', 2),
            retention_days=log_cfg.get('retention_days', 5),
        )
        await self._shared_log.start()

        self._log_base = log_base
        self._media_dir = self._path('data', 'media')
        os.makedirs(self._media_dir, exist_ok=True)
        if valid_bots:
            await asyncio.gather(*(self._start_bot(bc) for bc in valid_bots))

        cfg.on_change('bot', self._on_bot_config_change)

        self._dau_service = DAUService(log_base)
        await self._dau_service.start()
        await self._start_http_server()

        for coro in (self._config_watch_loop, self._media_cleanup_loop, self._restart_scheduler):
            asyncio.create_task(coro())

        msg = (f"✅ 启动完成: {len(self._bots)} 个机器人, "
               f"{self._plugin_manager.handler_count} 个命令处理器")
        log.info(msg)
        self._push_web_log('framework', {'source': '启动器', 'content': msg})

        self._stop_event = asyncio.Event()
        try:
            await self._stop_event.wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await self.shutdown()
        return self._restart_requested

    # ==================== 机器人实例管理 ====================

    async def _start_bot(self, bot_cfg):
        appid = str(bot_cfg['appid'])
        instance = BotInstance(bot_cfg, self._log_base)
        try:
            await instance.start(self._on_event)
            instance.sender._media_dir = self._media_dir
            self._bots[appid] = instance
            if instance.ws_client:
                asyncio.create_task(instance.ws_client.connect())
            return instance
        except Exception as e:
            report_error(SYSTEM, "启动器", e, context={'appid': appid})
            return None

    def _on_bot_config_change(self, data):
        try:
            asyncio.get_running_loop().create_task(self._sync_bots())
        except RuntimeError:
            pass

    async def _sync_bots(self):
        valid = {str(b['appid']): b for b in cfg.get_bot_configs()
                 if b.get('appid') and b.get('secret')}
        current, target = set(self._bots), set(valid)

        for appid in current - target:
            bot = self._bots.pop(appid)
            await bot.stop()
            self._push_web_log('framework', {'content': f'热重载: {bot.name} ({appid}) 已移除'})

        for appid in target - current:
            inst = await self._start_bot(valid[appid])
            if inst:
                self._push_web_log('framework', {'content': f'热重载: {inst.name} ({appid}) 已启动'})

        for appid in current & target:
            bot = self._bots[appid]
            new_cfg = valid[appid]
            bot.bot_cfg = new_cfg
            bot.owner_ids = new_cfg.get('owner_ids', [])
            bot.robot_qq = str(new_cfg.get('robot_qq', ''))
            ws_cfg = new_cfg.get('websocket', {})
            if ws_cfg.get('enabled') and not bot.ws_client:
                bot.ws_client = WSClient(
                    appid=appid, token_manager=bot.token_manager,
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

    # ==================== 后台任务 ====================

    async def _config_watch_loop(self):
        while True:
            await asyncio.sleep(5)
            try:
                cfg.get('bot', 'bots')
                cfg.get('settings')
                cfg.get('templates')
            except Exception:
                pass

    async def _media_cleanup_loop(self, max_days=3):
        while True:
            await asyncio.sleep(3600)
            try:
                cutoff = time.time() - max_days * 86400
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self._do_media_cleanup, self._media_dir, cutoff)
            except Exception:
                pass

    @staticmethod
    def _do_media_cleanup(media_dir, cutoff):
        for name in os.listdir(media_dir):
            fpath = os.path.join(media_dir, name)
            try:
                if os.path.isfile(fpath) and os.path.getmtime(fpath) < cutoff:
                    os.remove(fpath)
            except OSError:
                pass

    async def shutdown(self):
        log.info("正在关闭...")
        # 按依赖顺序关闭: 插件监听 → DAU → 机器人 → 模块 → 日志 → HTTP
        if self._plugin_manager:
            self._plugin_manager.stop_watcher()
        cleanup = [
            self._dau_service and self._dau_service.stop(),
            self._bots and asyncio.gather(*(b.stop() for b in self._bots.values()),
                                           return_exceptions=True),
            self._module_manager and self._module_manager.shutdown(),
            self._shared_log and self._shared_log.shutdown(),
        ]
        for coro in cleanup:
            if coro:
                await coro
        if self._runner:
            try:
                await asyncio.wait_for(self._runner.cleanup(), timeout=5)
            except asyncio.TimeoutError:
                pass
        log.info("已关闭")

    # ==================== HTTP ====================

    def _init_http_app(self):
        self._app = web.Application(client_max_size=20 * 1024 * 1024)
        self._app.router.add_post('/', self._handle_webhook)
        self._app.router.add_get('/health', self._handle_health)

    async def _start_http_server(self):
        try:
            from web.setup import setup_web
            setup_web(self._app, self, self._base_dir)
        except Exception as e:
            log.warning(f"Web 面板加载失败: {e}")

        class _F(logging.Filter):
            def filter(self, record):
                return 'BadStatusLine' not in record.getMessage()
        logging.getLogger('aiohttp.server').addFilter(_F())

        host = cfg.get('settings', 'server.host', '0.0.0.0')
        port = cfg.get('settings', 'server.port', 5200)
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        await web.TCPSite(self._runner, host, port).start()
        log.info(f"HTTP 服务器已启动: {host}:{port}")

    async def _handle_webhook(self, request):
        raw_body = await request.read()
        headers = dict(request.headers)

        appid = headers.get('X-Bot-Appid', headers.get('x-bot-appid', '')) \
                or request.query.get('appid', '')
        if not appid:
            return web.json_response({'error': 'missing appid'}, status=400)

        bot = self._bots.get(appid)
        if not bot:
            return web.json_response({'error': f'unknown bot: {appid}'}, status=404)

        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError:
            return web.json_response({'error': 'invalid JSON'}, status=400)

        if body.get('op') == 13:
            sig_resp = verify_and_respond(body, bot.secret)
            success = bool(sig_resp)
            log.info(f"[Webhook] 签名校验{'成功' if success else '失败'} appid={appid}")
            return (web.Response(text=sig_resp, content_type='application/json')
                    if success else web.json_response({'error': 'invalid validation'}, status=400))

        try:
            asyncio.create_task(self._on_event(Event.from_webhook(headers, body)))
        except Exception as e:
            report_error(SYSTEM, "Webhook", e, context={'appid': appid})

        return web.json_response({})

    async def _handle_health(self, request):
        return web.json_response({
            'status': 'ok',
            'bots': len(self._bots),
            'plugins': self._plugin_manager.handler_count if self._plugin_manager else 0,
        })

    # ==================== 辅助 ====================

    def _push_web_log(self, log_type: str, entry: dict):
        if self._web_log_cb:
            try:
                self._web_log_cb(log_type, entry)
            except Exception:
                pass

    def get_bot(self, appid):
        return self._bots.get(str(appid))

    async def _restart_scheduler(self):
        restart_cfg = cfg.get('settings', 'restart') or {}
        if not restart_cfg.get('enabled', False):
            return

        mode = restart_cfg.get('mode', 'daily')
        now = datetime.now()

        if mode == 'daily':
            time_str = str(restart_cfg.get('daily_time', '04:00'))
            parts = time_str.split(':')
            h, m = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
            target = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if now >= target:
                target += timedelta(days=1)
            wait_secs = (target - now).total_seconds()
        elif mode == 'interval':
            hours = float(restart_cfg.get('interval_hours', 24))
            wait_secs = hours * 3600
            target = now + timedelta(seconds=wait_secs)
        else:
            return

        log.info(f"定时重启: {target.strftime('%m-%d %H:%M')}")

        try:
            await asyncio.sleep(wait_secs)
        except asyncio.CancelledError:
            return

        self._push_web_log('framework', {'content': '⏰ 定时重启触发'})
        self._restart_requested = True
        if self._stop_event:
            self._stop_event.set()
