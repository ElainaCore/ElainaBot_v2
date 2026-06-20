#!/usr/bin/env python
"""事件处理 Mixin — 事件分发 / 去重 / 生命周期 / 用户追踪 / 群组记录"""

import asyncio
import json
import time
from datetime import datetime, timedelta

from core.base.config import cfg
from core.base.logger import FRAMEWORK, get_logger, report_error
from core.message.event import (
    FRIEND_ADD,
    FRIEND_DEL,
    GROUP_ADD_ROBOT,
    GROUP_DEL_ROBOT,
    GROUP_MEMBER_ADD,
    GROUP_MEMBER_REMOVE,
    GROUP_MESSAGE_CREATE,
    GROUP_MSG_RECEIVE,
    GROUP_MSG_REJECT,
    INTERACTION_CREATE,
    MESSAGE_AUDIT_PASS,
    MESSAGE_AUDIT_REJECT,
    MESSAGE_TYPES,
    SILENT_TYPES,
)
from core.message.parsers import swap_ids

log = get_logger(FRAMEWORK, '事件处理')

_USER_CACHE_TTL = 3600
_DEDUP_TTL = 300
_GROUP_CACHE_MAX = 10000
_FULL_ACCESS_CACHE_TTL = 1800


def _new_user_entry(uid, today, member_role=''):
    entry = {'userid': uid, 'value': 1, 'last_active': today}
    if member_role:
        entry['member_role'] = member_role
    return entry


class _EventDedup:
    """轻量 TTL 去重"""

    __slots__ = ('_seen', '_next_purge')

    def __init__(self):
        self._seen = {}
        self._next_purge = 0

    def is_dup(self, *ids) -> bool:
        now = time.time()
        if now > self._next_purge or len(self._seen) > 5000:
            self._seen = {k: v for k, v in self._seen.items() if v > now}
            self._next_purge = now + 60
        for eid in ids:
            if not eid:
                continue
            if eid in self._seen:
                return True
            self._seen[eid] = now + _DEDUP_TTL
        return False


class EventHandlerMixin:
    """事件处理混入类 (由 BotManager 继承)"""

    def _init_event_state(self):
        self._dedup = {}
        self._known_users = {}
        self._cache_clean_ts = 0
        self._group_users_cache = {}
        self._group_locks = {}
        self._full_access_cache = {}  # {group_id: expire_ts}
        self._dirty_groups = {}  # {group_id: bot} — 待写入的群缓存
        self._flush_task = None

    # ==================== 事件入口 ====================

    async def _on_event(self, event):
        appid = event.appid
        bot = self._bots.get(appid)
        if not bot:
            return

        et = event.event_type

        # 交互事件 (INTERACTION_CREATE) 的 op12 ACK 不再由框架在分发前自动发送,
        # 改由传输层 (webhook / websocket) 在插件分发后, 用插件经
        # event.set_callback_code() 设置的状态码发送; 插件未设置则用默认 code。
        # 参见 core/server/webhook.py 与 core/network/websocket.py。

        # 去重 (setdefault 避免二次查找)
        if cfg.get_bot_setting(appid, 'dedup.enabled', False):
            dedup = self._dedup.setdefault(appid, _EventDedup())
            if dedup.is_dup(event.message_id, event.event_id):
                return

        # union_id 交换
        if event.user_id and event.union_openid:
            need_swap = (
                cfg.get_bot_setting(appid, 'identity.use_union_id_for_group', False)
                if event.is_group
                else cfg.get_bot_setting(appid, 'identity.use_union_id_for_channel', True)
                if event.is_channel
                else cfg.get_bot_setting(appid, 'identity.use_union_id_for_group', False)
            )
            if need_swap:
                event.user_id, event.union_openid, _ = swap_ids(event.raw_user_id, event.union_openid, True)

        # 生命周期事件 → 记录事件日志后, 分发给插件 (允许插件回复入群/退群等事件)
        lc = self._LIFECYCLE_HANDLERS.get(et)
        if lc:
            await lc(self, bot, event)
            if self._plugin_manager:
                try:
                    await self._plugin_manager.dispatch(event, bot.sender)
                except Exception as e:
                    report_error(
                        FRAMEWORK,
                        '事件分发',
                        e,
                        context={'appid': appid, 'event_type': et, 'user_id': event.user_id},
                    )
            return

        # 消息审核事件
        if et in (MESSAGE_AUDIT_PASS, MESSAGE_AUDIT_REJECT):
            await self._handle_audit(bot, event, et)
            return

        # 静默事件（表态/频道更新）→ 记录日志 + 推送web面板事件日志，不分发插件
        if et in SILENT_TYPES:
            raw_json = json.dumps(event.raw, ensure_ascii=False)
            bot.log_service.add_sync(
                'lifecycle',
                {
                    'type': et,
                    'user_id': event.user_id or '',
                    'group_id': event.group_id or '',
                    'extra': raw_json,
                },
            )
            self._push_web_log(
                'event',
                {
                    'appid': appid,
                    'event_type': et,
                    'content': raw_json,
                    'raw_message': raw_json,
                    'bot_name': bot.name,
                },
            )
            return

        # 未预设事件 → 记录到错误日志 (LIFECYCLE/SILENT 已在上方 return)
        if et not in MESSAGE_TYPES and et != INTERACTION_CREATE:
            raw_json = json.dumps(event.raw, ensure_ascii=False)
            report_error(
                FRAMEWORK,
                '未知事件',
                f'收到未预设事件类型: {et}',
                context={'appid': appid, 'event_type': et, 'raw': raw_json},
            )

        # 消息日志 + 用户追踪 (消息事件和回调事件都记录)
        if et in MESSAGE_TYPES or et == INTERACTION_CREATE:
            # json.dumps 移至轻量 dict 构造后, 仅序列化一次
            msg_id = event.message_id or ''
            uid = event.user_id or ''
            gid = event.group_id or ''
            content = event.content or ''
            raw_json = json.dumps(event.raw, ensure_ascii=False)
            bot.log_service.add_sync('message', {
                'message_id': msg_id, 'user_id': uid,
                'reference_id': getattr(event, 'message_reference_id', '') or '',
                'group_id': gid, 'content': content,
                'raw_message': raw_json, 'direction': 'receive',
            })
            self._push_web_log('message', {
                'message_id': msg_id, 'user_id': uid,
                'group_id': gid, 'content': content, 'direction': 'receive',
                'appid': appid, 'bot_name': bot.name,
                'bot_qq': getattr(bot, 'robot_qq', '') or '', 'event_type': et,
                'reference_id': getattr(event, 'message_reference_id', '') or '',
                'raw_message': raw_json,
            })
            if uid:
                asyncio.create_task(self._track_user(bot, event, appid))

        # 全量群记录
        if et == GROUP_MESSAGE_CREATE and event.group_id:
            self._record_full_access_group(bot, event.group_id)

        # 全量群 @全体成员 → 跳过插件处理 (含同时 @机器人, 防止双机器人轮回)
        if et == GROUP_MESSAGE_CREATE and event.is_at_all:
            return

        # 屏蔽其他机器人发送的消息 (author.bot=true)
        if getattr(event, 'is_bot', False) \
                and cfg.get_bot_setting(appid, 'non_at_message.ignore_bot_sender', False):
            return

        # 插件分发
        if not self._plugin_manager:
            return
        try:
            await self._plugin_manager.dispatch(event, bot.sender)
        except Exception as e:
            report_error(
                FRAMEWORK,
                '事件分发',
                e,
                context={'appid': appid, 'event_type': et, 'user_id': event.user_id},
            )
            self._push_web_log(
                'error',
                {
                    'appid': appid,
                    'source': '事件分发',
                    'content': str(e),
                    'event_type': et,
                },
            )

    # ==================== 消息审核 ====================

    async def _handle_audit(self, bot, event, et):
        """MESSAGE_AUDIT_PASS / MESSAGE_AUDIT_REJECT: 仅记录, 不再替换消息 id"""
        if et == MESSAGE_AUDIT_REJECT:
            d = event.raw.get('d', {}) if isinstance(event.raw, dict) else {}
            log.warning(f'[{event.appid}] 消息审核未通过: {d.get("audit_id", "")}')

    # ==================== 全量群记录 ====================

    def _record_full_access_group(self, bot, group_id):
        """记录全量群到 data.db, 内存缓存 30 分钟"""
        now = time.time()
        expire = self._full_access_cache.get(group_id)
        if expire and now < expire:
            return
        self._full_access_cache[group_id] = now + _FULL_ACCESS_CACHE_TTL
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        bot.log_service.db_queue(
            'INSERT OR IGNORE INTO full_access_groups (group_id, first_seen) VALUES (?, ?)',
            (group_id, ts),
        )

    def get_full_access_groups(self):
        """从 data.db 拉取所有全量群记录"""
        bot = next(iter(self._bots.values()), None)
        if not bot:
            return []
        rows = bot.log_service.query_data('SELECT group_id FROM full_access_groups ORDER BY first_seen DESC')
        return rows

    # ==================== 生命周期 ====================

    def _log_lifecycle(self, bot, log_type, extra=None, raw_event=None):
        entry = {'type': log_type, 'user_id': '', 'group_id': ''}
        if extra:
            entry.update(extra)
        if raw_event:
            raw_json = json.dumps(raw_event, ensure_ascii=False)
            entry['extra'] = raw_json
        asyncio.create_task(bot.log_service.add('lifecycle', entry))
        web_entry = {'appid': bot.appid, 'bot_name': bot.name, **entry}
        if raw_event:
            web_entry['raw_message'] = entry['extra']
        self._push_web_log('lifecycle', web_entry)

    async def _handle_group_add(self, bot, event):
        self._log_lifecycle(
            bot,
            'group_add',
            {'group_id': event.group_id or '', 'user_id': event.user_id or ''},
            raw_event=event.raw,
        )
        await self._lifecycle_reply(
            bot,
            event,
            'welcome.group_welcome',
            'welcome',
            {'group_id': event.group_id or ''},
        )

    async def _handle_group_del(self, bot, event):
        self._log_lifecycle(
            bot,
            'group_del',
            {'group_id': event.group_id or '', 'user_id': event.user_id or ''},
            raw_event=event.raw,
        )

    async def _handle_group_member_add(self, bot, event):
        gid, uid = event.group_id or '', event.user_id or ''
        if gid and uid:
            await self._add_user_to_group(bot, gid, uid)
        self._log_lifecycle(bot, 'group_member_add', {'group_id': gid, 'user_id': uid}, raw_event=event.raw)

    async def _handle_group_member_remove(self, bot, event):
        gid, uid = event.group_id or '', event.user_id or ''
        if gid and uid:
            await self._remove_user_from_group(bot, gid, uid)
        self._log_lifecycle(bot, 'group_member_del', {'group_id': gid, 'user_id': uid}, raw_event=event.raw)

    async def _handle_friend_add(self, bot, event):
        uid = event.user_id or ''
        sharer_id = event.sharer_id or ''
        scene = event.scene or 0
        if uid:
            tasks = [bot.log_service.db_execute('INSERT OR IGNORE INTO members (user_id) VALUES (?)', (uid,))]
            if sharer_id:
                tasks.append(bot.log_service.share_record(sharer_id, uid, scene))
            await asyncio.gather(*tasks, return_exceptions=True)
        self._log_lifecycle(bot, 'friend_add', {'user_id': uid}, raw_event=event.raw)
        await self._lifecycle_reply(bot, event, 'welcome.friend_add_message', 'friend_add', {'user_id': uid})

    async def _handle_friend_del(self, bot, event):
        self._log_lifecycle(bot, 'friend_del', {'user_id': event.user_id or ''}, raw_event=event.raw)

    async def _handle_group_msg_reject(self, bot, event):
        self._log_lifecycle(
            bot, 'group_msg_reject',
            {'group_id': event.group_id or '', 'user_id': event.user_id or ''},
            raw_event=event.raw,
        )

    async def _handle_group_msg_receive(self, bot, event):
        self._log_lifecycle(
            bot, 'group_msg_receive',
            {'group_id': event.group_id or '', 'user_id': event.user_id or ''},
            raw_event=event.raw,
        )

    async def _lifecycle_reply(self, bot, event, cfg_key, template, tvars):
        """生命周期欢迎消息 (复用)"""
        if cfg.get_bot_setting(event.appid, cfg_key, False):
            try:
                await bot.sender.reply(event, template_name=template, template_vars=tvars)
            except Exception as e:
                report_error(FRAMEWORK, cfg_key, e, context={'appid': event.appid})

    _LIFECYCLE_HANDLERS = {
        GROUP_ADD_ROBOT: _handle_group_add,
        GROUP_DEL_ROBOT: _handle_group_del,
        GROUP_MEMBER_ADD: _handle_group_member_add,
        GROUP_MEMBER_REMOVE: _handle_group_member_remove,
        FRIEND_ADD: _handle_friend_add,
        FRIEND_DEL: _handle_friend_del,
        GROUP_MSG_REJECT: _handle_group_msg_reject,
        GROUP_MSG_RECEIVE: _handle_group_msg_receive,
    }

    # ==================== 用户/群组追踪 ====================

    async def _run_side_tasks(self, bot, event, gid):
        """wakeup + 群组记录 (复用)"""
        tasks = []
        if event.is_direct:
            tasks.append(bot.log_service.wakeup_update(event.user_id))
        if gid and gid != 'c2c':
            tasks.append(self._add_user_to_group(bot, gid, event.user_id, event.member_role or ''))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _track_user(self, bot, event, appid):
        uid = event.user_id
        gid = event.group_id or ''
        username = getattr(event, 'username', '') or ''
        now = time.time()

        # 定期清理过期缓存
        if now - self._cache_clean_ts > 600:
            self._cache_clean_ts = now
            self._known_users = {k: v for k, v in self._known_users.items() if v > now}
            # 清理过期群缓存 (expire_ts < now), 避免不活跃群的 user_map 一直占用内存
            active = {k: v for k, v in self._group_users_cache.items() if v[0] > now}
            self._group_users_cache = active
            self._group_locks = {k: v for k, v in self._group_locks.items() if k in active}

        if username:
            bot.log_service.db_queue(
                'INSERT INTO users (user_id, name) VALUES (?, ?) '
                'ON CONFLICT(user_id) DO UPDATE SET name=excluded.name '
                "WHERE users.name = '' OR users.name IS NULL",
                (uid, username),
            )

        # 已知用户: 跳过 DB 查询
        if uid in self._known_users:
            await self._run_side_tasks(bot, event, gid)
            return

        # 新用户判定
        existing = await bot.log_service.db_fetch_one('SELECT user_id FROM users WHERE user_id=?', (uid,))
        if not existing:
            bot.log_service.db_queue('INSERT OR IGNORE INTO users (user_id) VALUES (?)', (uid,))

        self._known_users[uid] = now + _USER_CACHE_TTL
        await self._run_side_tasks(bot, event, gid)

        # 新用户首次私聊 → 欢迎 (合并条件)
        if not existing and event.is_direct and cfg.get_bot_setting(appid, 'welcome.new_user_welcome', False):
            try:
                total = await bot.log_service.db_fetch_value('SELECT COUNT(*) FROM users', default=1)
                await bot.sender.reply(
                    event,
                    template_name='user_welcome',
                    template_vars={'user_id': uid, 'user_count': str(total)},
                )
            except Exception as e:
                report_error(FRAMEWORK, '新用户欢迎', e, context={'appid': appid})

    # ==================== 群组成员记录 ====================

    @staticmethod
    def _tomorrow_ts():
        d = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        return (d + timedelta(days=1)).timestamp()

    @staticmethod
    def _users_json(user_map):
        try:
            return json.dumps(list(user_map.values()), ensure_ascii=False)
        except RuntimeError:
            return json.dumps(list(dict(user_map).values()), ensure_ascii=False)

    def _upsert_group_user(self, user_map, uid, today, member_role=''):
        """更新或新增群成员条目, 返回是否有变更"""
        entry = user_map.get(uid)
        if entry is None:
            user_map[uid] = _new_user_entry(uid, today, member_role)
            return True
        changed = False
        if entry.get('last_active') != today:
            entry['last_active'] = today
            changed = True
        if member_role and entry.get('member_role') != member_role:
            entry['member_role'] = member_role
            changed = True
        return changed

    def _ensure_flush_task(self):
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.create_task(self._flush_dirty_groups())

    def _mark_group_dirty(self, group_id, bot):
        """标记群缓存待落库, 由 _flush_dirty_groups 批量写回"""
        self._dirty_groups[group_id] = bot
        self._ensure_flush_task()

    async def _flush_dirty_groups(self):
        while True:
            await asyncio.sleep(30)
            if not self._dirty_groups:
                continue
            batch, self._dirty_groups = self._dirty_groups, {}
            for gid, bot in batch.items():
                cached = self._group_users_cache.get(gid)
                if cached:
                    bot.log_service.db_queue(
                        'UPDATE groups_users SET users=? WHERE group_id=?',
                        (self._users_json(cached[1]), gid),
                    )

    @staticmethod
    def _parse_user_map(raw_list):
        """将 DB 中的 users JSON 列表解析为 {uid: entry} dict"""
        result = {}
        for item in raw_list:
            if isinstance(item, dict):
                uid = item.get('userid', '')
                if uid:
                    result[uid] = item
            elif item:
                result[item] = _new_user_entry(item, '')
        return result

    def _group_lock(self, group_id):
        """取或建群级写锁 (保证同群成员增删串行)"""
        lock = self._group_locks.get(group_id)
        if lock is None:
            lock = self._group_locks[group_id] = asyncio.Lock()
        return lock

    async def _load_group_user_map(self, bot, group_id):
        """从 DB 读取群成员 {uid: entry}; 返回 (user_map, existed)"""
        rows = await asyncio.get_running_loop().run_in_executor(
            None,
            bot.log_service.query_data,
            'SELECT users FROM groups_users WHERE group_id=?',
            (group_id,),
        )
        if not rows:
            return {}, False
        raw_str = rows[0].get('users', '[]')
        try:
            raw = json.loads(raw_str)
        except (json.JSONDecodeError, TypeError) as e:
            p = getattr(e, 'pos', 0) or 0
            log.warning(f'[群用户列表] group={group_id} JSON损坏: {e}, 上下文: ...{raw_str[max(0,p-50):p+50]}...')
            raw = []
        return self._parse_user_map(raw), True

    async def _mutate_group_user(self, bot, group_id, mutate, create_if_missing):
        """群成员表单次变更的统一入口: 串行锁 + 缓存命中/落库逻辑只此一份。
        mutate(user_map) 就地改动并返回是否有变更; create_if_missing 控制群不存在时是否新建行。"""
        async with self._group_lock(group_id):
            # 1. 内存缓存命中: 仅改内存 + 标脏, 由 _flush_dirty_groups 批量落库
            cached = self._group_users_cache.get(group_id)
            if cached and time.time() < cached[0]:
                if mutate(cached[1]):
                    self._mark_group_dirty(group_id, bot)
                return
            self._group_users_cache.pop(group_id, None)

            # 2. DB 加载
            try:
                user_map, existed = await self._load_group_user_map(bot, group_id)
                if not existed and not create_if_missing:
                    return
                if mutate(user_map):
                    if existed:
                        bot.log_service.db_queue(
                            'UPDATE groups_users SET users=? WHERE group_id=?',
                            (self._users_json(user_map), group_id),
                        )
                    else:
                        bot.log_service.db_queue(
                            'INSERT INTO groups_users (group_id, users) VALUES (?, ?)',
                            (group_id, self._users_json(user_map)),
                        )
                self._set_group_cache(group_id, user_map)
            except Exception as e:
                report_error(
                    FRAMEWORK,
                    '群用户列表更新',
                    e,
                    context={'group_id': group_id},
                )

    async def _add_user_to_group(self, bot, group_id, user_id, member_role=''):
        uid = str(user_id)
        today = datetime.now().strftime('%Y-%m-%d')
        await self._mutate_group_user(
            bot,
            group_id,
            lambda user_map: self._upsert_group_user(user_map, uid, today, member_role),
            create_if_missing=True,
        )

    async def _remove_user_from_group(self, bot, group_id, user_id):
        uid = str(user_id)
        await self._mutate_group_user(
            bot,
            group_id,
            lambda user_map: user_map.pop(uid, None) is not None,
            create_if_missing=False,
        )

    def _set_group_cache(self, group_id, user_map):
        """写入群缓存, 超过上限时淘汰最早条目"""
        if len(self._group_users_cache) >= _GROUP_CACHE_MAX and group_id not in self._group_users_cache:
            oldest = next(iter(self._group_users_cache))
            del self._group_users_cache[oldest]
        self._group_users_cache[group_id] = (self._tomorrow_ts(), user_map)
