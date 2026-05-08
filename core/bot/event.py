#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""事件处理 Mixin — 事件分发 / 去重 / 生命周期 / 用户追踪 / 群组记录"""

import json
import time
import asyncio
from datetime import datetime, timedelta

from core.base.config import cfg
from core.base.logger import get_logger, FRAMEWORK, report_error
from core.message.event import (
    GROUP_ADD_ROBOT, GROUP_DEL_ROBOT, FRIEND_ADD, FRIEND_DEL, MESSAGE_TYPES,
)
from core.message.parsers import swap_ids

log = get_logger(FRAMEWORK, "事件处理")

_USER_CACHE_TTL = 3600
_DEDUP_TTL = 300
_NEW_USER_ENTRY = lambda uid, today: {'userid': uid, 'value': 1, 'last_active': today}


class _EventDedup:
    """轻量 TTL 去重"""
    __slots__ = ('_seen', '_next_purge')

    def __init__(self):
        self._seen = {}
        self._next_purge = 0

    def is_dup(self, *ids) -> bool:
        now = time.time()
        if now > self._next_purge:
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

    # ==================== 事件入口 ====================

    async def _on_event(self, event):
        appid = event.appid
        bot = self._bots.get(appid)
        if not bot:
            return

        et = event.event_type

        # 去重 (setdefault 避免二次查找)
        if cfg.get_bot_setting(appid, 'dedup.enabled', False):
            dedup = self._dedup.setdefault(appid, _EventDedup())
            if dedup.is_dup(event.message_id, event.event_id):
                return

        # union_id 交换
        if event.user_id and event.union_openid:
            need_swap = (cfg.get_bot_setting(appid, 'identity.use_union_id_for_group', False)
                         if event.is_group else
                         cfg.get_bot_setting(appid, 'identity.use_union_id_for_channel', True)
                         if event.is_channel else
                         cfg.get_bot_setting(appid, 'identity.use_union_id_for_group', False))
            if need_swap:
                event.user_id, event.union_openid, _ = swap_ids(
                    event.raw_user_id, event.union_openid, True)

        # 生命周期事件 → 提前返回
        lc = self._LIFECYCLE_HANDLERS.get(et)
        if lc:
            await lc(self, bot, event)
            return

        _t0 = time.time()

        # 消息日志 + 用户追踪
        if et in MESSAGE_TYPES:
            log_entry = {
                'type': et, 'message_id': event.message_id or '',
                'user_id': event.user_id or '', 'group_id': event.group_id or '',
                'content': event.content or '',
                'raw_message': json.dumps(event.raw, ensure_ascii=False),
            }
            bot.log_service.add_sync('message', log_entry)
            self._push_web_log('message', {
                **log_entry, 'appid': appid, 'bot_name': bot.name,
                'bot_qq': getattr(bot, 'robot_qq', '') or '',
                'event_type': et, 'direction': 'receive',
            })
            if event.user_id:
                asyncio.create_task(self._track_user(bot, event, appid))

        # 插件分发
        if not self._plugin_manager:
            return
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
        _dt = time.time() - _t0
        if _dt > 1:
            msg = (f"[性能] 事件处理耗时 {_dt*1000:.0f}ms "
                   f"(分发={_dt*1000:.0f}ms) "
                   f"content={event.content[:50] if event.content else ''}")
            log.warning(msg)
            self._push_web_log('framework', {
                'appid': appid, 'source': '性能', 'content': msg,
            })

    # ==================== 生命周期 ====================

    def _log_lifecycle(self, bot, log_type, extra=None):
        entry = {'type': log_type, 'user_id': '', 'group_id': ''}
        if extra:
            entry.update(extra)
        asyncio.create_task(bot.log_service.add('lifecycle', entry))
        self._push_web_log('lifecycle', {'appid': bot.appid, 'bot_name': bot.name, **entry})

    async def _handle_group_add(self, bot, event):
        self._log_lifecycle(bot, 'group_add', {
            'group_id': event.group_id or '', 'user_id': event.user_id or ''})
        await self._lifecycle_reply(bot, event, 'welcome.group_welcome', 'welcome',
                                    {'group_id': event.group_id or ''})

    async def _handle_group_del(self, bot, event):
        self._log_lifecycle(bot, 'group_del', {
            'group_id': event.group_id or '', 'user_id': event.user_id or ''})

    async def _handle_friend_add(self, bot, event):
        uid = event.user_id or ''
        sharer_id = event.sharer_id or ''
        scene = event.scene or 0
        if uid:
            tasks = [bot.log_service.db_execute(
                "INSERT OR IGNORE INTO members (user_id) VALUES (?)", (uid,))]
            if sharer_id:
                tasks.append(bot.log_service.share_record(sharer_id, uid, scene))
            await asyncio.gather(*tasks, return_exceptions=True)
        self._log_lifecycle(bot, 'friend_add', {
            'user_id': uid, 'sharer_id': sharer_id, 'scene': scene})
        await self._lifecycle_reply(bot, event, 'welcome.friend_add_message', 'friend_add',
                                    {'user_id': uid})

    async def _handle_friend_del(self, bot, event):
        self._log_lifecycle(bot, 'friend_del', {'user_id': event.user_id or ''})

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
        FRIEND_ADD: _handle_friend_add,
        FRIEND_DEL: _handle_friend_del,
    }

    # ==================== 用户/群组追踪 ====================

    async def _run_side_tasks(self, bot, event, gid):
        """wakeup + 群组记录 (复用)"""
        tasks = []
        if event.is_direct:
            tasks.append(bot.log_service.wakeup_update(event.user_id))
        if gid and gid != 'c2c':
            tasks.append(self._add_user_to_group(bot, gid, event.user_id))
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

        if username:
            bot.log_service.db_queue(
                "INSERT INTO users (user_id, name) VALUES (?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET name=?",
                (uid, username, username))

        # 已知用户: 跳过 DB 查询
        if uid in self._known_users:
            await self._run_side_tasks(bot, event, gid)
            return

        # 新用户判定
        existing = await bot.log_service.db_fetch_one(
            "SELECT user_id FROM users WHERE user_id=?", (uid,))
        if not existing:
            bot.log_service.db_queue(
                "INSERT OR IGNORE INTO users (user_id) VALUES (?)", (uid,))

        self._known_users[uid] = now + _USER_CACHE_TTL
        await self._run_side_tasks(bot, event, gid)

        # 新用户首次私聊 → 欢迎 (合并条件)
        if not existing and event.is_direct \
                and cfg.get_bot_setting(appid, 'welcome.new_user_welcome', False):
            try:
                total = await bot.log_service.db_fetch_value(
                    "SELECT COUNT(*) FROM users", default=1)
                await bot.sender.reply(event, template_name='user_welcome',
                                       template_vars={'user_id': uid, 'user_count': str(total)})
            except Exception as e:
                report_error(FRAMEWORK, "新用户欢迎", e, context={'appid': appid})

    # ==================== 群组成员记录 ====================

    @staticmethod
    def _tomorrow_ts():
        d = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        return (d + timedelta(days=1)).timestamp()

    @staticmethod
    def _users_json(user_map):
        return json.dumps(list(user_map.values()), ensure_ascii=False)

    def _upsert_group_user(self, user_map, uid, today):
        """更新或新增群成员条目, 返回是否有变更"""
        entry = user_map.get(uid)
        if entry and entry.get('last_active') == today:
            return False
        user_map[uid] = entry or _NEW_USER_ENTRY(uid, today)
        if entry:
            entry['last_active'] = today
        return True

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
                result[item] = _NEW_USER_ENTRY(item, '')
        return result

    async def _add_user_to_group(self, bot, group_id, user_id):
        uid = str(user_id)
        today = datetime.now().strftime('%Y-%m-%d')

        # 1. 内存缓存命中
        cached = self._group_users_cache.get(group_id)
        if cached:
            expire_ts, user_map = cached
            if time.time() < expire_ts:
                if self._upsert_group_user(user_map, uid, today):
                    bot.log_service.db_queue(
                        "UPDATE groups_users SET users=? WHERE group_id=?",
                        (self._users_json(user_map), group_id))
                return
            del self._group_users_cache[group_id]

        # 2. DB 加载
        try:
            rows = await asyncio.get_running_loop().run_in_executor(
                None, bot.log_service.query_data,
                "SELECT users FROM groups_users WHERE group_id=?", (group_id,))
            if rows:
                user_map = self._parse_user_map(json.loads(rows[0].get('users', '[]')))
                self._upsert_group_user(user_map, uid, today)
                bot.log_service.db_queue(
                    "UPDATE groups_users SET users=? WHERE group_id=?",
                    (self._users_json(user_map), group_id))
            else:
                user_map = {uid: _NEW_USER_ENTRY(uid, today)}
                bot.log_service.db_queue(
                    "INSERT INTO groups_users (group_id, users) VALUES (?, ?)",
                    (group_id, self._users_json(user_map)))
            self._group_users_cache[group_id] = (self._tomorrow_ts(), user_map)
        except Exception as e:
            report_error(FRAMEWORK, "群用户列表更新", e,
                         context={'group_id': group_id, 'user_id': uid})
