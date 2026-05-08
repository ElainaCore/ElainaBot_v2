"""OneBot 11 适配器模块

将 Elaina_V2 的消息/事件以 OneBot 11 标准协议通过 WebSocket 推送到
外部机器人框架 (如 Yunzai-Bot), 并处理其回复动作 (send_msg 等)。

实现方式:
    1. setup 时通过运行时 patch 让 BotManager._on_event 在处理前
       触发 on_raw_event hook (该 hook 已在框架 hook.py 文档中声明但未实装)
    2. 模块监听 on_raw_event, 将事件转为 OneBot 格式后推给 WS 客户端
    3. 模块将 WS 路由挂载到框架已有的 aiohttp 服务器, 接收外部框架的 action 请求并调用 sender
"""

__module_meta__ = {
    'name': 'OneBot 适配器',
    'description': 'OneBot 11 协议适配器, 将消息/事件推送到外部机器人框架',
    'version': '1.0.0',
    'author': 'Elaina',
}

import json
import base64
import random
import asyncio
from modules.onebot_adapter.lib.id_mapper import IDMapper
from modules.onebot_adapter.lib.ws_server import OneBotWSServer
from modules.onebot_adapter.lib.event_converter import convert_message_event, convert_lifecycle_event
from core.message.media import upload_media_bytes

_adapter = None  # 全局引用, 用于 teardown

# ==================== 默认配置 ====================

_DEFAULT_CFG = {
    'ws_path': '/onebot',
    'reverse_ws_urls': [{'url': '', 'appid': ''}],
    'reconnect_interval': 5,
    'access_token': '',
    'heartbeat_interval': 30,
    'debug': False,
}

_CFG_COMMENTS = {
    'ws_path': '正向 WS 路由路径 (挂载到框架端口, 如 /onebot)',
    'reverse_ws_urls': '反向 WS 列表, 格式: [{url: ws://..., appid: 你的appid}]',
    'reconnect_interval': '反向 WS 断线重连间隔 (秒)',
    'access_token': '鉴权 Token, 为空则不鉴权',
    'heartbeat_interval': '心跳间隔 (秒)',
    'debug': '调试模式, 输出完整 WS 收发载荷',
}


class OneBotAdapter:
    """OneBot 适配器核心"""

    def __init__(self, ctx):
        self.ctx = ctx
        self.log = ctx.log
        self.cfg = {}
        self.id_mapper = None
        self.ws_server = None
        self._senders = {}           # {appid: sender}
        self._log_services = {}      # {appid: LogService}
        self._msg_id_cache = {}      # {(appid, chat_id): msg_id}  缓存最近 msg_id
        self._qq_map = {}            # {appid_str: robot_qq_int}
        self._default_qq = 0
        self._current_appid = ''     # action 处理时的上下文 appid
        self._original_on_event = None
        self._bm = None

    async def start(self):
        self.cfg = self.ctx.ensure_config(_DEFAULT_CFG, comments=_CFG_COMMENTS)
        ws_path = self.cfg.get('ws_path', '/onebot')
        self.log.info(f"配置: path={ws_path}, token={'***' if self.cfg['access_token'] else '(无)'}")

        # 1. 初始化 ID 映射器
        db_path = self.ctx.get_data_path('id_mapping.db')
        self.id_mapper = IDMapper(db_path)
        await self.id_mapper.open()
        self.log.info("ID 映射数据库已加载")

        # 2. 构建 appid → robot_qq 映射
        try:
            from core.base.config import cfg as _fw_cfg
            for bc in (_fw_cfg.get_bot_configs() or []):
                aid = str(bc.get('appid', ''))
                rq = bc.get('robot_qq', '')
                if aid and rq:
                    self._qq_map[aid] = int(rq)
        except Exception:
            pass
        for aid, qq in self._qq_map.items():
            self.log.info(f"QQ 映射: appid={aid} → robot_qq={qq}")
        self._default_qq = next(iter(self._qq_map.values()), 0)

        # 3. 注册 on_raw_event hook + 安装 hook 触发点
        self.ctx.register_hook('on_raw_event', self._on_raw_event, priority=10)
        self._install_event_hook()

        # 4. 注册 after_send hook (追踪机器人自身发送的消息)
        self.ctx.register_hook('after_send', self._on_after_send, priority=100)

        # 5. 解析 reverse_ws_urls [{url, appid}]
        reverse_entries = [
            {'url': e.get('url', ''), 'appid': str(e.get('appid', ''))}
            for e in (self.cfg.get('reverse_ws_urls') or [])
            if isinstance(e, dict) and e.get('url', '').strip()
        ]

        self.ws_server = OneBotWSServer(
            access_token=self.cfg.get('access_token', ''),
            heartbeat_interval=self.cfg.get('heartbeat_interval', 30),
            on_action=self._handle_action,
            default_qq=self._default_qq,
            qq_map=self._qq_map,
            log=self.log,
            ws_path=ws_path,
            reverse_entries=reverse_entries,
            reconnect_interval=self.cfg.get('reconnect_interval', 5),
            debug=self.cfg.get('debug', False),
        )

        # 5a. 正向 WS: 挂载到框架 aiohttp app
        app = self._get_framework_app()
        if app:
            self.ws_server.attach(app)
            port = self._get_framework_port()
            self.log.info(f"✅ 正向 WS 已挂载: ws://0.0.0.0:{port}{ws_path}")
        else:
            self.log.warning("⚠️ 无法获取框架 aiohttp app, 正向 WS 未挂载")

        # 5b. 反向 WS: 主动连接外部服务器
        await self.ws_server.start_reverse()
        if reverse_entries:
            self.log.info(f"✅ 反向 WS 已启动: {len(reverse_entries)} 个连接")

        # 5c. 启动心跳
        self.ws_server.start_heartbeat()

    async def stop(self):
        self._uninstall_event_hook()
        if self.ws_server:
            await self.ws_server.stop()
        if self.id_mapper:
            await self.id_mapper.close()
        self.log.info("OneBot 适配器已停止")

    # ==================== 框架资源获取 ====================

    @staticmethod
    def _get_framework_app():
        """获取框架的 aiohttp Application"""
        try:
            from core.bot.manager import _bot_manager_ref
            if _bot_manager_ref and _bot_manager_ref._app:
                return _bot_manager_ref._app
        except Exception:
            pass
        return None

    @staticmethod
    def _get_framework_port():
        """获取框架 HTTP 服务器端口"""
        try:
            from core.base.config import cfg
            return cfg.get('settings', 'server.port', 5001)
        except Exception:
            return 5001

    # ==================== 事件 Hook 安装 (运行时 patch) ====================

    def _install_event_hook(self):
        """通过运行时 patch 让 _on_event 触发 on_raw_event hook

        on_raw_event 已在框架 hook.py 文档中声明为内置 hook, 但未实装。
        此处由模块在 setup 阶段补全, teardown 时还原。
        """
        try:
            from core.bot.manager import _bot_manager_ref
            bm = _bot_manager_ref
            if bm is None:
                self.log.warning("BotManager 未初始化, 无法安装事件 hook")
                return
            self._bm = bm
            self._original_on_event = bm._on_event  # bound method

            from core.module.hook import get_hook_manager
            hooks = get_hook_manager()

            async def patched_on_event(event):
                # 补全 on_raw_event 触发
                bot = bm._bots.get(event.appid)
                if bot and hooks.has('on_raw_event'):
                    await hooks.emit('on_raw_event', event, bot)
                await self._original_on_event(event)

            bm._on_event = patched_on_event

            # 同步更新已有 WS 客户端的回调引用
            for bot_inst in bm._bots.values():
                if hasattr(bot_inst, 'ws_client') and bot_inst.ws_client:
                    bot_inst.ws_client._on_event = patched_on_event

            self.log.info("on_raw_event hook 已安装")
        except Exception as e:
            self.log.warning(f"安装事件 hook 失败: {e}")

    def _uninstall_event_hook(self):
        """还原 _on_event"""
        if self._original_on_event and self._bm:
            try:
                self._bm._on_event = self._original_on_event
                for bot_inst in self._bm._bots.values():
                    if hasattr(bot_inst, 'ws_client') and bot_inst.ws_client:
                        bot_inst.ws_client._on_event = self._original_on_event
                self.log.info("on_raw_event hook 已卸载")
            except Exception as e:
                self.log.warning(f"卸载事件 hook 失败: {e}")

    # ==================== 事件处理 ====================

    async def _on_raw_event(self, event, bot):
        """on_raw_event 回调 — 将事件转为 OneBot 格式推送"""
        if not self.ws_server or not self.ws_server.has_clients:
            return

        appid = event.appid
        aid = str(appid)
        if appid:
            if hasattr(bot, 'sender'):
                self._senders[appid] = bot.sender
            ls = getattr(bot, 'log_service', None)
            if ls:
                self._log_services[appid] = ls

        # 动态更新 qq_map
        if aid and aid not in self._qq_map:
            rq = getattr(bot, 'robot_qq', '') or ''
            if rq:
                self._qq_map[aid] = int(rq)
                self.ws_server.qq_map = self._qq_map
                if not self._default_qq:
                    self._default_qq = int(rq)
                    self.ws_server._default_qq = self._default_qq

        self_qq = self._qq_map.get(aid, self._default_qq) or self._default_qq

        # 缓存 msg_id (用于后续回复)
        if event.message_id:
            chat_id = event.group_id or event.user_id or ''
            if chat_id:
                self._msg_id_cache[(appid, chat_id)] = event.message_id

        # 转换事件
        ob_event = None
        if event.is_lifecycle:
            ob_event = await convert_lifecycle_event(event, self.id_mapper, self_qq)
        else:
            ob_event = await convert_message_event(event, self.id_mapper, self_qq)

        if ob_event:
            await self._log_recv(aid, event, ob_event)
            await self.ws_server.broadcast(ob_event, appid=aid)

    async def _on_after_send(self, data):
        """after_send hook — 可用于追踪机器人自身回复 (暂留扩展)"""
        pass

    # ==================== Action 处理 (OneBot → Elaina) ====================

    async def _handle_action(self, action: str, params: dict, echo=None, appid: str = '') -> dict:
        """处理来自外部框架的 OneBot action"""
        self._current_appid = appid
        handler = _ACTION_MAP.get(action)
        if handler:
            return await handler(self, params, echo)
        return _fail(f"不支持的 action: {action}", echo=echo)

    async def _action_send_msg(self, params, echo):
        msg_type = params.get('message_type', '')
        group_id = params.get('group_id')
        user_id = params.get('user_id')
        if not msg_type:
            msg_type = 'group' if group_id else 'private'

        text, image_bytes = _parse_segments(params.get('message', ''))
        if not text and not image_bytes:
            return _fail("消息内容为空", echo=echo)

        sender = self._get_sender()
        if not sender:
            return _fail("无可用的消息发送器", echo=echo)

        # 统一群/私聊发送路径
        is_group = msg_type == 'group' and group_id
        raw_id = group_id if is_group else user_id
        if not raw_id:
            return _fail("缺少 group_id 或 user_id", echo=echo)

        id_type = 'group' if is_group else 'user'
        real_id = await self.id_mapper.to_openid_by_type(int(raw_id), id_type)
        if not real_id:
            return _fail(f"未知{'群号' if is_group else '用户'}: {raw_id}", echo=echo)

        label = text[:200] if text else '[image]'
        self.log.info(f"{'群' if is_group else '私聊'} {raw_id}: {label}")

        gid = real_id if is_group else None
        uid = None if is_group else real_id
        ok, data = await self._do_send(sender, gid, uid, text, image_bytes, self._find_msg_id(real_id))
        await self._log_msg('group' if is_group else 'private', real_id, label, ok, data)
        if ok:
            return _ok({'message_id': hash(str(data)) & 0x7FFFFFFF}, echo=echo)
        self.log.warning(f"{'群' if is_group else '私聊'} {raw_id} 发送失败: {data}")
        return _fail(str(data), echo=echo)

    async def _do_send(self, sender, group_id, user_id, text, image_bytes, msg_id):
        """统一发送: 纯文本 / 图片 / 图文混合"""
        target = group_id or user_id
        prefix = 'groups' if group_id else 'users'
        if image_bytes:
            file_info = await upload_media_bytes(sender, image_bytes, 1, f"/v2/{prefix}/{target}/files")
            if not file_info:
                return False, '图片上传失败'
            payload = {'msg_type': 7, 'msg_seq': random.randint(10000, 999999),
                       'content': text or '', 'media': {'file_info': file_info}}
            if msg_id:
                payload['msg_id'] = msg_id
            return await sender.post_json(f"/v2/{prefix}/{target}/messages", payload)
        fn = sender.send_to_group if group_id else sender.send_to_user
        ok, data, _ = await fn(target, text, msg_id=msg_id)
        return ok, data

    async def _action_send_group_msg(self, params, echo):
        params['message_type'] = 'group'
        return await self._action_send_msg(params, echo)

    async def _action_send_private_msg(self, params, echo):
        params['message_type'] = 'private'
        return await self._action_send_msg(params, echo)

    async def _action_get_login_info(self, params, echo):
        qq = self._qq_map.get(self._current_appid, self._default_qq) or self._default_qq
        return _ok({'user_id': qq, 'nickname': 'ElainaBot'}, echo=echo)

    async def _action_get_group_list(self, params, echo):
        groups = []
        for (openid, id_type), qq_id in self.id_mapper._cache_fwd.items():
            if id_type == 'group':
                groups.append({
                    'group_id': qq_id,
                    'group_name': f'群{qq_id}',
                    'member_count': 0,
                    'max_member_count': 0,
                })
        return _ok(groups, echo=echo)

    async def _action_get_friend_list(self, params, echo):
        friends = []
        for (openid, id_type), qq_id in self.id_mapper._cache_fwd.items():
            if id_type == 'user':
                friends.append({
                    'user_id': qq_id,
                    'nickname': str(qq_id),
                    'remark': '',
                })
        return _ok(friends, echo=echo)

    async def _action_get_stranger_info(self, params, echo):
        uid = params.get('user_id', 0)
        return _ok({
            'user_id': uid,
            'nickname': str(uid),
            'sex': 'unknown',
            'age': 0,
        }, echo=echo)

    async def _action_get_group_member_info(self, params, echo):
        gid = params.get('group_id', 0)
        uid = params.get('user_id', 0)
        return _ok({
            'group_id': gid, 'user_id': uid,
            'nickname': str(uid), 'card': '',
            'sex': 'unknown', 'age': 0,
            'join_time': 0, 'last_sent_time': 0,
            'level': '0', 'role': 'member',
            'title': '',
        }, echo=echo)

    async def _action_get_group_member_list(self, params, echo):
        return _ok([], echo=echo)

    async def _action_get_status(self, params, echo):
        return _ok({
            'online': True,
            'good': True,
        }, echo=echo)

    async def _action_get_version_info(self, params, echo):
        return _ok({
            'app_name': 'Elaina-OneBot-Adapter',
            'app_version': '1.0.0',
            'protocol_version': 'v11',
        }, echo=echo)

    async def _action_can_send_image(self, params, echo):
        return _ok({'yes': True}, echo=echo)

    async def _action_can_send_record(self, params, echo):
        return _ok({'yes': False}, echo=echo)

    # ==================== 辅助 ====================

    def _get_ls(self, appid: str = ''):
        """获取 LogService"""
        aid = appid or self._current_appid
        return self._log_services.get(aid) or next(iter(self._log_services.values()), None)

    def _push_ws(self, *, user_id='', group_id='', content='', is_bot=False, appid='', direction=''):
        """实时推送到 web 面板日志流"""
        try:
            import web.ws as _ws
            aid = appid or self._current_appid
            bot = self._bm._bots.get(aid) if self._bm else None
            _ws.push_log('message', {
                'appid': aid,
                'bot_name': getattr(bot, 'name', '') if bot else '',
                'bot_qq': getattr(bot, 'robot_qq', '') if bot else '',
                'user_id': user_id, 'group_id': group_id,
                'content': content, 'is_bot': is_bot,
                'direction': direction,
                'source': 'onebot',
            })
        except Exception:
            pass

    async def _log_msg(self, msg_type, target_id, content, ok, resp_data):
        """保存发送记录到 SQLite (含发送结果) + 实时推送"""
        gid = str(target_id) if msg_type == 'group' else ''
        uid = str(target_id) if msg_type == 'private' else ''
        raw = json.dumps({'ok': ok, 'resp': str(resp_data)[:500]}, ensure_ascii=False)
        ls = self._get_ls()
        if ls:
            await ls.add('message', {
                'type': 'onebot_send',
                'user_id': uid, 'group_id': gid,
                'content': content, 'raw_message': raw,
                'plugin_name': 'onebot_adapter',
            })
        self._push_ws(user_id=uid, group_id=gid, content=content, is_bot=True, direction='send')

    async def _log_recv(self, appid, event, ob_event):
        """保存接收的 OneBot 事件 JSON 到 SQLite (用原始 OpenID, 不推实时日志)"""
        if ob_event.get('post_type') != 'message':
            return
        raw = json.dumps(ob_event, ensure_ascii=False)
        ls = self._get_ls(appid)
        if ls:
            await ls.add('message', {
                'type': 'onebot_recv',
                'user_id': event.user_id or '',
                'group_id': event.group_id or '',
                'content': ob_event.get('raw_message', ''),
                'raw_message': raw,
                'plugin_name': 'onebot_adapter',
            })

    def _get_sender(self, appid: str = ''):
        """获取 sender (优先匹配 appid, 否则任意)"""
        target = appid or self._current_appid
        return self._senders.get(target) or next(iter(self._senders.values()), None)

    def _find_msg_id(self, chat_id):
        """查找缓存中的 msg_id"""
        for appid in self._senders:
            mid = self._msg_id_cache.get((appid, chat_id))
            if mid:
                return mid
        return None


# ==================== Action 路由表 ====================

_ACTION_MAP = {
    'send_msg':                 OneBotAdapter._action_send_msg,
    'send_group_msg':           OneBotAdapter._action_send_group_msg,
    'send_private_msg':         OneBotAdapter._action_send_private_msg,
    'get_login_info':           OneBotAdapter._action_get_login_info,
    'get_group_list':           OneBotAdapter._action_get_group_list,
    'get_friend_list':          OneBotAdapter._action_get_friend_list,
    'get_stranger_info':        OneBotAdapter._action_get_stranger_info,
    'get_group_member_info':    OneBotAdapter._action_get_group_member_info,
    'get_group_member_list':    OneBotAdapter._action_get_group_member_list,
    'get_status':               OneBotAdapter._action_get_status,
    'get_version_info':         OneBotAdapter._action_get_version_info,
    'can_send_image':           OneBotAdapter._action_can_send_image,
    'can_send_record':          OneBotAdapter._action_can_send_record,
}


# ==================== 响应构建 ====================

def _ok(data=None, echo=None):
    r = {'status': 'ok', 'retcode': 0, 'data': data or {}}
    if echo is not None:
        r['echo'] = echo
    return r


def _fail(msg='', echo=None, retcode=1):
    r = {'status': 'failed', 'retcode': retcode, 'data': None, 'msg': msg, 'wording': msg}
    if echo is not None:
        r['echo'] = echo
    return r


def _parse_segments(message):
    """从 OneBot message 字段提取 (text, image_bytes)"""
    if isinstance(message, str):
        return message, None
    if not isinstance(message, list):
        return str(message), None
    texts = []
    image_bytes = None
    for seg in message:
        if not isinstance(seg, dict):
            continue
        t = seg.get('type', '')
        d = seg.get('data', {})
        if t == 'text':
            texts.append(d.get('text', ''))
        elif t == 'at':
            texts.append(f"@{d.get('qq', '')}")
        elif t == 'image' and not image_bytes:
            image_bytes = _decode_image(d.get('file', ''))
    return ''.join(texts), image_bytes


def _decode_image(file_str):
    """解码 base64:// 或 data:image 格式的图片"""
    if not file_str:
        return None
    if file_str.startswith('base64://'):
        try:
            return base64.b64decode(file_str[9:])
        except Exception:
            return None
    if file_str.startswith('data:image'):
        idx = file_str.find(',') + 1
        if idx:
            try:
                return base64.b64decode(file_str[idx:])
            except Exception:
                return None
    return None


# ==================== 模块入口 ====================

async def setup(ctx):
    global _adapter
    _adapter = OneBotAdapter(ctx)
    await _adapter.start()


async def teardown():
    global _adapter
    if _adapter:
        await _adapter.stop()
        _adapter = None
