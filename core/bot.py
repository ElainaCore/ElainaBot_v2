#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""多机器人管理器

BotInstance:  单个机器人实例 (Token + API + Sender + WS + Log)
BotManager:   管理所有机器人, HTTP 服务器, 插件系统, 扩展系统

Webhook: 通过 X-Bot-Appid 请求头路由到对应 BotInstance
WebSocket: 每个 BotInstance 独立 WS 连接, 由连接上下文绑定 appid
"""

import os
import sys
import json
import time
import asyncio
from datetime import datetime, timedelta
from aiohttp import web
from core.base.config import cfg
from core.base.logger import get_logger, setup as setup_logger, FRAMEWORK, SYSTEM, report_error
from core.base.sign import verify_and_respond
from core.message.event import (Event, extract_sign_headers,
                                 GROUP_AT_MESSAGE_CREATE, C2C_MESSAGE_CREATE,
                                 INTERACTION_CREATE, GROUP_ADD_ROBOT, GROUP_DEL_ROBOT,
                                 FRIEND_ADD, FRIEND_DEL, MESSAGE_TYPES)
from core.message.parsers import swap_ids
from core.message.sender import MessageSender
from core.plugin.manager import PluginManager
from core.message.template import tpl
from core.network.access import TokenManager
from core.storage.log import LogService, SharedLogService
from core.storage.dau import DAUService
from core.network.websocket import WSClient
from core.module.manager import ModuleManager

log = get_logger(SYSTEM, "启动器")

_bot_manager_ref = None  # 全局引用, 供 plugin.py 获取 log_service


class BotInstance:
    """单个机器人实例"""

    __slots__ = ('appid', 'name', 'secret', 'bot_cfg',
                 'token_manager', 'sender', 'ws_client', 'log_service',
                 'bot_id', 'avatar_url', 'robot_qq', 'owner_ids')

    def __init__(self, bot_cfg, base_log_dir):
        self.bot_cfg = bot_cfg
        self.appid = str(bot_cfg['appid'])
        self.name = self.appid
        self.secret = str(bot_cfg['secret'])

        self.token_manager = TokenManager(self.appid, self.secret)
        custom_api_base = str(bot_cfg.get('api_base', '') or '')
        self.sender = MessageSender(self.token_manager,
                                     custom_api_base=custom_api_base)

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
        bot_log.info(f"正在启动 (appid={self.appid})")

        await self.token_manager.ensure_token()
        await self.token_manager.start_auto_refresh()

        # 通过 /users/@me 获取机器人昵称
        await self._fetch_bot_name()

        await self.log_service.start()
        self.sender._log_service = self.log_service
        self.sender._bot_name = self.name
        self.sender._bot_qq = self.robot_qq

        ws_cfg = self.bot_cfg.get('websocket', {})
        if ws_cfg.get('enabled', False):
            self.ws_client = WSClient(
                appid=self.appid,
                token_manager=self.token_manager,
                on_event=on_event,
                reconnect_interval=ws_cfg.get('reconnect_interval', 5),
                max_reconnects=ws_cfg.get('max_reconnects', -1),
                custom_url=ws_cfg.get('custom_url', ''),
                custom_api_base=str(self.bot_cfg.get('api_base', '') or ''),
            )

        bot_log = get_logger(FRAMEWORK, self.name)
        api_info = f", API={self.sender._base_url}" if self.sender._custom_api_base else ''
        bot_log.info(f"✅ 启动完成 (WS={'启用' if self.ws_client else '禁用'}{api_info})")

    async def _fetch_bot_name(self):
        """通过 GET /users/@me 获取机器人昵称"""
        try:
            token = await self.token_manager.get_token()
            base = self.sender._base_url
            url = f"{base}/users/@me"
            client = await self.token_manager._ensure_client()
            async with client.get(url, headers={'Authorization': f'QQBot {token}'}) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    name = data.get('username', '')
                    self.bot_id = data.get('id', '')
                    self.avatar_url = data.get('avatar', '')
                    if name:
                        self.name = name
                        get_logger(FRAMEWORK, name).info(f"机器人昵称: {name}")
                        return
            get_logger(FRAMEWORK, self.appid).warning("获取机器人昵称失败, 使用 appid 代替")
        except Exception as e:
            get_logger(FRAMEWORK, self.appid).warning(f"获取机器人昵称异常: {e}, 使用 appid 代替")

    async def stop(self):
        """停止机器人"""
        bot_log = get_logger(FRAMEWORK, self.name)
        if self.ws_client:
            await self.ws_client.close()
        await self.log_service.shutdown()
        await self.sender.close()
        await self.token_manager.close()
        bot_log.info("已停止")


# 用户缓存 TTL (秒)
_USER_CACHE_TTL = 3600  # 1小时


class BotManager:
    """多机器人管理器 + HTTP 服务器"""

    def __init__(self):
        self._bots = {}             # {appid: BotInstance}
        self._plugin_manager = None
        self._module_manager = None
        self._dau_service = None
        self._app = None            # aiohttp app
        self._runner = None
        self._base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self._web_log_cb = None     # web.ws.push_log 回调 (由 web/setup.py 注入)
        self._known_users = {}      # {uid: expire_timestamp} 已知用户缓存
        self._cache_clean_ts = 0    # 上次清理缓存的时间
        self._group_users_cache = {}  # {group_id: (expire_ts, set(uid...))} 群用户缓存
        self._log_base = ''         # data/log 路径 (供热重载复用)
        self._media_dir = ''        # data/media 路径 (供热重载复用)

    @property
    def dau_service(self):
        """DAU 统计服务 (插件可访问)"""
        return self._dau_service

    @property
    def module_manager(self):
        return self._module_manager

    @property
    def plugin_manager(self):
        return self._plugin_manager

    # ==================== 启动 ====================

    async def start(self):
        """完整启动流程"""
        global _bot_manager_ref
        _bot_manager_ref = self

        # 1. 初始化配置
        config_dir = os.path.join(self._base_dir, 'config')
        cfg.init(config_dir)

        # 2. 初始化日志系统
        fw_name = cfg.get('settings', 'web.framework_name', 'ElainaBot')
        setup_logger(framework_name=fw_name)
        log.info(f"{'='*5} {fw_name} 启动中 {'='*5}")

        # 3. 校验机器人配置
        bot_configs = cfg.get_bot_configs()
        valid_bots = []
        if bot_configs:
            valid_bots = [b for b in bot_configs if b.get('appid') and b.get('secret')]
        if not bot_configs:
            log.warning("未配置任何机器人, 请通过 Web 面板填写配置")
        elif not valid_bots:
            log.warning("所有机器人配置均缺少 appid 或 secret, 请通过 Web 面板填写配置")

        # 4. 初始化模块管理器
        modules_dir = os.path.join(self._base_dir, 'modules')
        self._module_manager = ModuleManager(modules_dir)
        self._module_manager.discover()
        await self._module_manager.start_enabled()

        # 5. 初始化插件管理器
        plugins_dir = os.path.join(self._base_dir, 'plugins')
        self._plugin_manager = PluginManager(plugins_dir)
        await self._plugin_manager.load_all()

        # 6. 启动通用日志服务 (框架+错误, 不分机器人)
        log_base = os.path.join(self._base_dir, 'data', cfg.get('settings', 'logging.dir', 'log'))
        log_cfg = cfg.get('settings', 'logging') or {}
        self._shared_log = SharedLogService(
            base_dir=log_base,
            wal_mode=log_cfg.get('wal_mode', True),
            insert_interval=log_cfg.get('insert_interval', 2),
            retention_days=log_cfg.get('retention_days', 5),
        )
        await self._shared_log.start()

        # 7. 启动机器人实例
        self._log_base = log_base
        self._media_dir = os.path.join(self._base_dir, 'data', 'media')
        os.makedirs(self._media_dir, exist_ok=True)
        for bot_cfg in valid_bots:
            await self._start_bot(bot_cfg)

        # 注册 bot.yaml 热重载回调
        cfg.on_change('bot', self._on_bot_config_change)

        if not self._bots:
            log.warning("没有成功启动的机器人, 请通过 Web 面板填写机器人配置")

        # 8. 启动 DAU 统计服务
        self._dau_service = DAUService(log_base)
        await self._dau_service.start()

        # 9. 启动 HTTP 服务器
        await self._start_http_server()

        # 10. 定时配置检查 + 媒体清理
        asyncio.create_task(self._config_watch_loop())
        asyncio.create_task(self._media_cleanup_loop(self._media_dir))

        msg = (f"✅ 启动完成: {len(self._bots)} 个机器人, "
               f"{self._plugin_manager.handler_count} 个命令处理器")
        log.info(msg)
        self._push_web_log('framework', {'source': '启动器', 'content': msg})

        # 等待退出
        try:
            await self._wait_forever()
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await self.shutdown()

    # ==================== 机器人实例管理 ====================

    async def _start_bot(self, bot_cfg):
        """启动单个机器人实例"""
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
        """bot.yaml 变更回调 → 调度异步同步任务"""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._sync_bots())
        except RuntimeError:
            pass

    async def _sync_bots(self):
        """对比配置与运行中实例, 启动新增 / 停止移除 / 更新现有"""
        bot_configs = cfg.get_bot_configs()
        valid = {}
        for b in bot_configs:
            aid = str(b.get('appid', ''))
            if aid and b.get('secret'):
                valid[aid] = b

        current = set(self._bots.keys())
        target = set(valid.keys())

        # 移除不再配置的机器人
        for appid in current - target:
            bot = self._bots.pop(appid)
            log.info(f"移除机器人: {appid} ({bot.name})")
            await bot.stop()
            self._push_web_log('framework', {
                'content': f'热重载: 机器人 {bot.name} ({appid}) 已移除',
            })

        # 启动新增的机器人
        for appid in target - current:
            instance = await self._start_bot(valid[appid])
            if instance:
                log.info(f"热重载: 新机器人 {instance.name} ({appid}) 已启动")
                self._push_web_log('framework', {
                    'content': f'热重载: 新机器人 {instance.name} ({appid}) 已启动',
                })

        # 更新现有机器人的运行时配置
        for appid in current & target:
            bot = self._bots[appid]
            new_cfg = valid[appid]
            bot.bot_cfg = new_cfg
            bot.owner_ids = new_cfg.get('owner_ids', [])
            bot.robot_qq = str(new_cfg.get('robot_qq', ''))
            # WS 开关变更
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
                log.info(f"[{bot.name}] WS 已启用")
            elif not ws_cfg.get('enabled') and bot.ws_client:
                await bot.ws_client.close()
                bot.ws_client = None
                log.info(f"[{bot.name}] WS 已禁用")

    # ==================== 配置监听 ====================

    async def _config_watch_loop(self):
        """定时触发配置检测 (确保无消息时也能热重载)"""
        while True:
            await asyncio.sleep(5)
            try:
                cfg.get('bot', 'bots')
                cfg.get('settings')
                cfg.get('templates')
            except Exception:
                pass

    # ==================== 媒体清理 ====================

    async def _media_cleanup_loop(self, media_dir, max_days=3):
        """每小时清理 data/media/ 中超过 max_days 天的文件 (按 mtime)"""
        while True:
            await asyncio.sleep(3600)
            try:
                cutoff = time.time() - max_days * 86400
                for name in os.listdir(media_dir):
                    fpath = os.path.join(media_dir, name)
                    if os.path.isfile(fpath) and os.path.getmtime(fpath) < cutoff:
                        try:
                            os.remove(fpath)
                            log.debug(f"清理媒体: {name}")
                        except OSError:
                            pass
            except Exception:
                pass

    async def shutdown(self):
        """优雅关闭"""
        log.info("正在关闭...")
        if self._dau_service:
            await self._dau_service.stop()
        for bot in self._bots.values():
            await bot.stop()
        if self._module_manager:
            await self._module_manager.shutdown()
        if hasattr(self, '_shared_log') and self._shared_log:
            await self._shared_log.shutdown()
        if self._runner:
            await self._runner.cleanup()
        log.info("已关闭")

    # ==================== HTTP 服务器 ====================

    async def _start_http_server(self):
        """启动 aiohttp Webhook 接收服务器 + Web 管理面板"""
        self._app = web.Application(client_max_size=20 * 1024 * 1024)
        self._app.router.add_post('/', self._handle_webhook)
        self._app.router.add_get('/health', self._handle_health)

        # 挂载 Web 管理面板
        try:
            from web.setup import setup_web
            setup_web(self._app, self, self._base_dir)
        except Exception as e:
            log.warning(f"Web 面板加载失败: {e}")

        host = cfg.get('settings', 'server.host', '0.0.0.0')
        port = cfg.get('settings', 'server.port', 5001)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, host, port)
        await site.start()
        log.info(f"HTTP 服务器已启动: {host}:{port}")

    async def _handle_webhook(self, request):
        """处理 Webhook 请求

        通过 X-Bot-Appid 头识别机器人, 验签后分发事件。
        """
        raw_body = await request.read()
        headers = dict(request.headers)

        # 提取 appid (优先 header, 回退到 ?appid= 查询参数)
        appid = headers.get('X-Bot-Appid', headers.get('x-bot-appid', ''))
        if not appid:
            appid = request.query.get('appid', '')
        if not appid:
            return web.json_response({'error': 'missing X-Bot-Appid or ?appid='}, status=400)

        bot = self._bots.get(appid)
        if not bot:
            return web.json_response({'error': f'unknown bot: {appid}'}, status=404)

        # 解析 body
        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError:
            return web.json_response({'error': 'invalid JSON'}, status=400)

        # 签名验证 (op=13)
        if body.get('op') == 13:
            sig_resp = verify_and_respond(body, bot.secret)
            if sig_resp:
                return web.Response(text=sig_resp, content_type='application/json')
            return web.json_response({'error': 'invalid validation'}, status=400)

        # 构造事件并分发
        try:
            event = Event.from_webhook(headers, body)
            asyncio.create_task(self._on_event(event))
        except Exception as e:
            report_error(SYSTEM, "Webhook", e, context={'appid': appid})

        return web.json_response({})

    async def _handle_health(self, request):
        """健康检查"""
        return web.json_response({
            'status': 'ok',
            'bots': len(self._bots),
            'plugins': self._plugin_manager.handler_count if self._plugin_manager else 0,
        })

    # ==================== 事件处理 ====================

    async def _on_event(self, event):
        """统一事件处理入口 (Webhook + WS 共用)

        处理流程:
        1. union_id 交换
        2. 生命周期事件处理 (入群欢迎/好友添加/退群)
        3. 记录消息日志
        4. 记录用户/群组
        5. 分发到插件系统
        """
        appid = event.appid
        bot = self._bots.get(appid)
        if not bot:
            log.warning(f"收到未知机器人事件: appid={appid}")
            return

        et = event.event_type

        # 1. union_id 交换 (按机器人配置)
        if event.user_id and event.union_openid:
            should_swap_group = cfg.get_bot_setting(appid, 'identity.use_union_id_for_group', False)
            should_swap_channel = cfg.get_bot_setting(appid, 'identity.use_union_id_for_channel', True)
            should_swap = should_swap_group if event.is_group else (
                should_swap_channel if event.is_channel else should_swap_group)
            if should_swap:
                event.user_id, event.union_openid, _ = swap_ids(
                    event.raw_user_id, event.union_openid, True)

        # 2. 生命周期事件处理
        if et == GROUP_ADD_ROBOT:
            await self._handle_group_add(bot, event)
            return
        elif et == GROUP_DEL_ROBOT:
            await self._handle_group_del(bot, event)
            return
        elif et == FRIEND_ADD:
            await self._handle_friend_add(bot, event)
            return
        elif et == FRIEND_DEL:
            await self._handle_friend_del(bot, event)
            return

        # 3. 记录消息日志 + 推送到面板
        if et in MESSAGE_TYPES:
            log_entry = {
                'type': et,
                'message_id': event.message_id or '',
                'user_id': event.user_id or '',
                'group_id': event.group_id or '',
                'content': event.content or '',
                'raw_message': json.dumps(event.raw, ensure_ascii=False),
            }
            await bot.log_service.add('message', log_entry)
            self._push_web_log('message', {
                'appid': appid, 'bot_name': bot.name,
                'bot_qq': getattr(bot, 'robot_qq', '') or '',
                'event_type': et, 'user_id': event.user_id or '',
                'group_id': event.group_id or '',
                'content': event.content or '',
                'raw_message': json.dumps(event.raw, ensure_ascii=False),
                'direction': 'receive',
            })

        # 4. 用户/群组记录 + 插件分发 (并行执行)
        user_task = None
        if event.user_id and et in MESSAGE_TYPES:
            user_task = asyncio.create_task(
                self._track_user(bot, event, appid))

        # 5. 分发到插件系统 (与用户记录并行)
        if self._plugin_manager:
            try:
                await self._plugin_manager.dispatch(event, bot.sender)
            except Exception as e:
                report_error(FRAMEWORK, "事件分发", e,
                             context={'appid': appid, 'event_type': et,
                                      'user_id': event.user_id})
                self._push_web_log('error', {
                    'appid': appid, 'source': '事件分发',
                    'content': str(e), 'event_type': et,
                })

        # 等待用户记录完成 (不影响插件响应速度)
        if user_task:
            try:
                await user_task
            except Exception:
                pass

    # ==================== 生命周期事件处理 ====================

    async def _handle_group_add(self, bot, event):
        """机器人被邀请入群"""
        appid = event.appid
        await bot.log_service.add('lifecycle', {
            'type': 'group_add', 'group_id': event.group_id or '',
            'user_id': event.user_id or '',
        })
        self._push_web_log('lifecycle', {
            'appid': appid, 'bot_name': bot.name,
            'type': 'group_add', 'group_id': event.group_id or '',
            'user_id': event.user_id or '',
        })
        if cfg.get_bot_setting(appid, 'welcome.group_welcome', False):
            try:
                await bot.sender.reply(event, template_name='welcome',
                                       template_vars={'group_id': event.group_id or ''})
            except Exception as e:
                report_error(FRAMEWORK, "入群欢迎", e, context={'appid': appid})

    async def _handle_group_del(self, bot, event):
        """机器人被移出群"""
        await bot.log_service.add('lifecycle', {
            'type': 'group_del', 'group_id': event.group_id or '',
            'user_id': event.user_id or '',
        })
        self._push_web_log('lifecycle', {
            'appid': bot.appid, 'bot_name': bot.name,
            'type': 'group_del', 'group_id': event.group_id or '',
            'user_id': event.user_id or '',
        })

    async def _handle_friend_add(self, bot, event):
        """用户添加机器人好友 -> 写入 members 表 + 记录分享来源"""
        appid = event.appid
        uid = event.user_id or ''
        if uid:
            await bot.log_service.db_execute(
                "INSERT OR IGNORE INTO members (user_id) VALUES (?)", (uid,))
        # 记录分享来源
        sharer_id = event.sharer_id or ''
        scene = event.scene or 0
        if sharer_id and uid:
            try:
                await bot.log_service.share_record(sharer_id, uid, scene)
            except Exception:
                pass
        await bot.log_service.add('lifecycle', {
            'type': 'friend_add', 'user_id': uid,
            'sharer_id': sharer_id, 'scene': scene,
        })
        self._push_web_log('lifecycle', {
            'appid': appid, 'bot_name': bot.name,
            'type': 'friend_add', 'user_id': uid,
        })
        if cfg.get_bot_setting(appid, 'welcome.friend_add_message', False):
            try:
                await bot.sender.reply(event, template_name='friend_add',
                                       template_vars={'user_id': event.user_id or ''})
            except Exception as e:
                report_error(FRAMEWORK, "好友欢迎", e, context={'appid': appid})

    async def _handle_friend_del(self, bot, event):
        """用户删除机器人好友"""
        await bot.log_service.add('lifecycle', {
            'type': 'friend_del', 'user_id': event.user_id or '',
        })
        self._push_web_log('lifecycle', {
            'appid': bot.appid, 'bot_name': bot.name,
            'type': 'friend_del', 'user_id': event.user_id or '',
        })

    # ==================== 辅助 ====================

    async def _track_user(self, bot, event, appid):
        """记录用户/群组 (与插件分发并行执行)"""
        uid = event.user_id
        gid = event.group_id or ''
        username = getattr(event, 'username', '') or ''
        now = time.time()

        # 定期清理过期缓存 (每10分钟)
        if now - self._cache_clean_ts > 600:
            self._cache_clean_ts = now
            expired = [k for k, v in self._known_users.items() if v < now]
            for k in expired:
                del self._known_users[k]

        # 检查缓存判断是否新用户
        is_new_user = uid not in self._known_users

        if is_new_user:
            # 查库确认
            existing = await bot.log_service.db_fetch_one(
                "SELECT user_id FROM users WHERE user_id=?", (uid,))
            if existing:
                is_new_user = False
            else:
                await bot.log_service.db_execute(
                    "INSERT OR IGNORE INTO users (user_id) VALUES (?)", (uid,))

        # 写入缓存
        self._known_users[uid] = now + _USER_CACHE_TTL

        # 更新昵称
        if username:
            await bot.log_service.db_execute(
                "INSERT INTO users (user_id, name) VALUES (?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET name=?",
                (uid, username, username))

        # 私聊活跃记录 (唤醒系统)
        if event.is_direct:
            try:
                await bot.log_service.wakeup_update(uid)
            except Exception:
                pass

        # 群组记录
        if gid and gid != 'c2c':
            await self._add_user_to_group(bot, gid, uid)

        # 新用户首次私聊 -> 欢迎
        if is_new_user and event.is_direct:
            if cfg.get_bot_setting(appid, 'welcome.new_user_welcome', False):
                try:
                    total_users = await bot.log_service.db_fetch_value(
                        "SELECT COUNT(*) FROM users", default=1)
                    await bot.sender.reply(
                        event, template_name='user_welcome',
                        template_vars={
                            'user_id': uid,
                            'user_count': str(total_users),
                        })
                except Exception as e:
                    report_error(FRAMEWORK, "新用户欢迎", e, context={'appid': appid})

    @staticmethod
    def _tomorrow_ts():
        """返回明天 00:00:00 的时间戳 (缓存在当天结束时过期)"""
        d = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        return (d + timedelta(days=1)).timestamp()

    async def _add_user_to_group(self, bot, group_id, user_id):
        """记录群成员到 groups_users, 带内存缓存 (当天有效)

        数据格式: [{"userid": "xxx", "value": 1, "last_active": "2026-04-28"}, ...]
        缓存结构: {group_id: (expire_ts, {uid: entry_dict})}
        """
        uid = str(user_id)
        today = datetime.now().strftime('%Y-%m-%d')

        # 1. 检查内存缓存
        cached = self._group_users_cache.get(group_id)
        if cached:
            expire_ts, user_map = cached
            if time.time() < expire_ts:
                if uid in user_map:
                    # 已存在, 更新 last_active (如果不是今天)
                    if user_map[uid].get('last_active') == today:
                        return  # 完全跳过
                    user_map[uid]['last_active'] = today
                else:
                    # 新用户
                    user_map[uid] = {'userid': uid, 'value': 1, 'last_active': today}
                # 写入队列, 随 2 秒 flush 批量落盘
                bot.log_service.db_queue(
                    "UPDATE groups_users SET users=? WHERE group_id=?",
                    (json.dumps(list(user_map.values()), ensure_ascii=False), group_id))
                return
            else:
                del self._group_users_cache[group_id]

        # 2. 缓存未命中, 从数据库加载
        try:
            rows = bot.log_service.query_data(
                "SELECT users FROM groups_users WHERE group_id=?", (group_id,))
            if rows:
                raw = json.loads(rows[0].get('users', '[]'))
                # 兼容旧格式: ["uid1", "uid2"] → 转为新格式
                user_map = {}
                for item in raw:
                    if isinstance(item, str):
                        user_map[item] = {'userid': item, 'value': 1, 'last_active': ''}
                    elif isinstance(item, dict):
                        u = item.get('userid', '')
                        if u:
                            user_map[u] = item
                # 更新当前用户
                if uid in user_map:
                    user_map[uid]['last_active'] = today
                else:
                    user_map[uid] = {'userid': uid, 'value': 1, 'last_active': today}
                bot.log_service.db_queue(
                    "UPDATE groups_users SET users=? WHERE group_id=?",
                    (json.dumps(list(user_map.values()), ensure_ascii=False), group_id))
            else:
                user_map = {uid: {'userid': uid, 'value': 1, 'last_active': today}}
                bot.log_service.db_queue(
                    "INSERT INTO groups_users (group_id, users) VALUES (?, ?)",
                    (group_id, json.dumps(list(user_map.values()), ensure_ascii=False)))
            # 写入缓存, 过期时间为明天 00:00
            self._group_users_cache[group_id] = (self._tomorrow_ts(), user_map)
        except Exception as e:
            report_error(FRAMEWORK, "群用户列表更新", e,
                         context={'group_id': group_id, 'user_id': uid})

    def _push_web_log(self, log_type: str, entry: dict):
        """推送日志到 Web 面板 (安全调用, 面板未加载时静默忽略)"""
        if self._web_log_cb:
            try:
                self._web_log_cb(log_type, entry)
            except Exception:
                pass

    def get_bot(self, appid):
        """获取机器人实例"""
        return self._bots.get(str(appid))

    @staticmethod
    async def _wait_forever():
        """等待直到被中断"""
        stop = asyncio.Event()
        await stop.wait()
