#!/usr/bin/env python
"""事件处理 Mixin — 事件分发 / 去重 / 生命周期 / 用户追踪 / 群组记录"""

import asyncio
import json
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from itertools import islice

from core.base.config import cfg
from core.base.logger import FRAMEWORK, get_logger, report_error
from core.base.tasks import spawn
from core.message.event import (
    FRIEND_ADD,
    FRIEND_DEL,
    GROUP_ADD_ROBOT,
    GROUP_DEL_ROBOT,
    GROUP_JOIN_REQUEST,
    GROUP_MEMBER_ADD,
    GROUP_MEMBER_REMOVE,
    GROUP_MESSAGE_CREATE,
    GROUP_MSG_RECEIVE,
    GROUP_MSG_REJECT,
    INTERACTION_CREATE,
    MESSAGE_TYPES,
    SILENT_TYPES,
    SUBSCRIBE_MESSAGE_STATUS,
)
from core.message.parsers import IdentityHelper
from core.message.parsers.base import MessageUtils

log = get_logger(FRAMEWORK, '事件处理')

_USER_CACHE_TTL = 3600
_DEDUP_TTL = 300
_FULL_ACCESS_CACHE_TTL = 1800
_TRACK_WORKERS = 8  # 用户追踪后台 worker 数
_TRACK_QUEUE_MAX = 5000  # 用户追踪队列上限, 满则转入合并缓冲 (不丢弃)
_TRACK_DEDUP_TTL = 60  # 同键群消息追踪去重窗口(秒): 追踪任务对同键幂等, 短时重复直接跳过
_CACHE_PRUNE_INTERVAL = 1.0
_CACHE_PRUNE_BATCH = 2048


_today_cache = ('', 0.0)  # (date_str, valid_until_epoch)


def _today_str():
    """缓存当天日期字符串 (秒级失效检查), 避免每条群消息都 datetime.now().strftime。"""
    global _today_cache
    now = time.time()
    if now >= _today_cache[1]:
        d = datetime.now()
        # 缓存到当天 23:59:59.x, 跨天自动失效
        nxt = d.replace(hour=23, minute=59, second=59, microsecond=0).timestamp()
        _today_cache = (d.strftime('%Y-%m-%d'), nxt)
    return _today_cache[0]


def _prune_expired_entries(cache, now, limit, expires_at):
    """轮转扫描有限条目，避免一次性重建整个缓存。"""
    keys = list(islice(cache, limit))
    for key in keys:
        value = cache.pop(key)
        if expires_at(value) > now:
            cache[key] = value


@dataclass(slots=True)
class _TrackItem:
    """用户追踪所需的最小状态；插件仍使用原始 Event。"""

    bot: object
    appid: str
    uid: str
    gid: str
    username: str
    member_role: str
    is_bot: bool
    is_direct: bool
    reply_event: object | None
    queued: bool = False
    version: int = 0

    def merge(self, newer):
        """合并积压更新，保留最新状态以及仍需执行的副作用。"""
        self.bot = newer.bot
        self.username = newer.username or self.username
        self.member_role = newer.member_role or self.member_role
        self.is_bot = self.is_bot or newer.is_bot
        self.is_direct = self.is_direct or newer.is_direct
        self.reply_event = newer.reply_event or self.reply_event
        self.version += 1

    @property
    def key(self):
        return self.appid, self.uid, self.gid


class _EventDedup:
    """轻量 TTL 去重"""

    __slots__ = ('_seen', '_next_purge', '_next_size_purge')

    def __init__(self):
        self._seen = {}
        self._next_purge = 0
        self._next_size_purge = 0

    def is_dup(self, *ids) -> bool:
        now = time.time()
        if now > self._next_purge:
            _prune_expired_entries(
                self._seen, now, _CACHE_PRUNE_BATCH, lambda value: value
            )
            self._next_purge = now + _CACHE_PRUNE_INTERVAL
        if len(self._seen) > 5000 and now >= self._next_size_purge:
            for eid, expire in list(self._seen.items()):
                if expire <= now:
                    self._seen.pop(eid, None)
            self._next_size_purge = now + 60
        unique = dict.fromkeys(eid for eid in ids if eid)
        for eid in unique:
            if eid in self._seen:
                return True
        for eid in unique:
            self._seen[eid] = now + _DEDUP_TTL
        return False


class EventHandlerMixin:
    """事件处理混入类 (由 BotManager 继承)"""

    def _init_event_state(self):
        self._dedup = {}
        self._known_users = {}
        self._cache_clean_ts = 0
        self._group_locks = {}
        self._full_access_cache = {}  # {(appid, group_id): expire_ts}
        # 用户追踪后台队列 (有界, 背压): 替代每条消息 create_task 无界堆积
        self._track_queue = None
        self._track_workers = []
        self._track_pending = deque()
        self._track_recent = {}  # {去重键: 过期时间} 同键短时跳过
        self._track_recent_purge = 0.0
        self._track_jobs = {}  # {(appid, uid, gid): 轻量合并任务}
        self._track_drainer = None
        self._track_overflow_count = 0

    # ==================== 用户追踪后台队列 ====================

    def _ensure_track_workers(self):
        """惰性创建用户追踪队列与 worker (需在事件循环内调用)"""
        if self._track_queue is not None:
            return
        self._track_queue = asyncio.Queue(maxsize=_TRACK_QUEUE_MAX)
        self._track_workers = [asyncio.create_task(self._track_worker()) for _ in range(_TRACK_WORKERS)]

    def _enqueue_track(self, bot, event, appid):
        """投递用户追踪任务: 群消息同键短时去重削峰; 队列满时转入合并缓冲, 不丢弃"""
        self._ensure_track_workers()
        uid = str(event.user_id or '')
        gid = event.group_id or ''
        if event.is_group and gid:
            # 追踪对同(用户/群/角色/当天)幂等, 同键短时重复无新信息, 跳过以削减洪峰任务量
            key = (appid, event.user_id, gid, event.member_role or '',
                   bool(getattr(event, 'username', '')), bool(getattr(event, 'is_bot', False)))
            now = time.time()
            if now > self._track_recent_purge:
                self._track_recent_purge = now + _CACHE_PRUNE_INTERVAL
                _prune_expired_entries(
                    self._track_recent, now, _CACHE_PRUNE_BATCH, lambda value: value
                )
            if self._track_recent.get(key, 0) > now:
                return
            self._track_recent[key] = now + _TRACK_DEDUP_TTL

        item = _TrackItem(
            bot=bot,
            appid=str(appid),
            uid=uid,
            gid=gid,
            username=getattr(event, 'username', '') or '',
            member_role=event.member_role or '',
            is_bot=bool(getattr(event, 'is_bot', False)),
            is_direct=bool(event.is_direct),
            # 全量群消息不会触发新用户欢迎，不必为回复保留完整事件树。
            reply_event=None if event.event_type == GROUP_MESSAGE_CREATE else event,
        )
        job = self._track_jobs.get(item.key)
        if job is not None:
            job.merge(item)
            return
        self._track_jobs[item.key] = item
        try:
            self._track_queue.put_nowait(item)
            item.queued = True
        except asyncio.QueueFull:
            self._track_overflow_count += 1
            if self._track_overflow_count % 1000 == 1:
                waiting = len(self._track_pending)
                log.warning(
                    f'[用户追踪] 队列已满({_TRACK_QUEUE_MAX}), 转入合并缓冲 '
                    f'(累计 {self._track_overflow_count} 键, 待回灌 {waiting} 键, 不丢弃)'
                )
            self._track_pending.append(item)
            self._ensure_track_drainer()

    def _ensure_track_drainer(self):
        if self._track_drainer is None or self._track_drainer.done():
            self._track_drainer = asyncio.create_task(self._drain_track_pending())

    async def _drain_track_pending(self):
        """队列有空位时把合并缓冲回灌 (阻塞式 put, 保证最终全部处理)"""
        while self._track_pending:
            job = self._track_pending[0]
            job.queued = True
            try:
                await self._track_queue.put(job)
            except BaseException:
                job.queued = False
                raise
            self._track_pending.popleft()

    async def _track_worker(self):
        q = self._track_queue
        while True:
            job = await q.get()
            try:
                while True:
                    version = job.version
                    try:
                        await self._track_user(job)
                    except Exception as e:
                        report_error(
                            FRAMEWORK, '用户追踪', e,
                            context={'appid': job.appid},
                        )
                    if version == job.version:
                        break
            finally:
                self._track_jobs.pop(job.key, None)
                q.task_done()

    def _message_content(self, event):
        content = MessageUtils.sanitize_content(event.raw_content, keep_at=True) or event.content or ''
        if event.image_url and f'<{event.image_url}>' not in content:
            return f'{content}<{event.image_url}>' if content else f'<{event.image_url}>'
        return content

    def _message_log_data(self, event, content, raw_json):
        # 仅全量群消息区分是否艾特机器人; 非全量消息收不到未艾特消息, 一律算艾特
        at_bot = event.is_at_self if event.event_type == GROUP_MESSAGE_CREATE else True
        return {
            'message_id': event.message_id or '',
            'user_id': event.user_id or '',
            'reference_id': getattr(event, 'message_reference_id', '') or '',
            'group_id': event.group_id or '',
            'content': content,
            'raw_message': raw_json,
            'direction': 'receive',
            'at_bot': at_bot,
        }

    def _record_message_event(self, bot, event, appid):
        content = self._message_content(event)
        raw_json = json.dumps(event.raw, ensure_ascii=False)
        log_data = self._message_log_data(event, content, raw_json)
        bot.log_service.add_sync('message', log_data)
        self._push_web_log(
            'message',
            {
                **log_data,
                'appid': appid,
                'bot_name': bot.name,
                'bot_qq': getattr(bot, 'robot_qq', '') or '',
                'event_type': event.event_type,
            },
        )

    # ==================== 事件入口 ====================

    async def _on_event(self, event):
        appid = event.appid
        bot = self._bots.get(appid)
        if not bot:
            return

        et = event.event_type

        # 去重
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
                event.user_id, event.union_openid, _ = IdentityHelper.swap_ids(event.raw_user_id, event.union_openid, True)

        # 生命周期事件
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


        # 静默事件
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

        # 未预设事件
        if et not in MESSAGE_TYPES and et != INTERACTION_CREATE:
            raw_json = json.dumps(event.raw, ensure_ascii=False)
            report_error(
                FRAMEWORK,
                '未知事件',
                f'收到未预设事件类型: {et}',
                context={'appid': appid, 'event_type': et, 'raw': raw_json},
            )

        # 消息日志 + 用户追踪
        if et in MESSAGE_TYPES or et == INTERACTION_CREATE:
            self._record_message_event(bot, event, appid)
            if event.user_id:
                self._enqueue_track(bot, event, appid)


        if et == GROUP_MESSAGE_CREATE and event.group_id:
            self._record_full_access_group(bot, event.group_id)
            if event.is_at_self and event.bot_member_role in ('admin', 'owner'):
                self._record_bot_admin(bot, event.group_id)

        # 全量群 @全体成员 跳过
        if et == GROUP_MESSAGE_CREATE and event.is_at_all:
            return

        # 屏蔽其他机器人发送的消息 (author.bot=true)
        if getattr(event, 'is_bot', False) and cfg.get_bot_setting(appid, 'non_at_message.ignore_bot_sender', False):
            return

        # 插件分发
        if not self._plugin_manager:
            return
        try:
            await self._plugin_manager.dispatch(event, bot.sender)
        except Exception as e:
            ctx = {'appid': appid, 'event_type': et, 'user_id': event.user_id}
            report_error(FRAMEWORK, '事件分发', e, context=ctx)
            web_log_err_item = {'appid': appid, 'source': '事件分发', 'content': str(e), 'event_type': et}
            self._push_web_log('error', web_log_err_item)

    # ==================== 全量群记录 ====================

    def _record_full_access_group(self, bot, group_id):
        """记录实际收到全量消息的群，不触发受限查询接口。"""
        now = time.time()
        cache_key = (bot.appid, group_id)
        expire = self._full_access_cache.get(cache_key)
        if expire and now < expire:
            return
        self._full_access_cache[cache_key] = now + _FULL_ACCESS_CACHE_TTL
        bot.log_service.db_queue(
            'INSERT INTO groups_users (group_id, is_full_access, in_group) VALUES (?, 1, 1) '
            'ON CONFLICT(group_id) DO UPDATE SET is_full_access=1, in_group=1',
            (group_id,),
        )

    def _record_bot_admin(self, bot, group_id):
        """记录机器人在该群为管理员"""
        bot.log_service.db_queue(
            'INSERT INTO groups_users (group_id, is_admin, in_group) VALUES (?, 1, 1) '
            'ON CONFLICT(group_id) DO UPDATE SET is_admin=1, in_group=1',
            (group_id,),
        )

    def get_full_access_groups(self):
        """从所有 bot 的 data.db 拉取全量群记录 (含所属 appid)"""
        rows = []
        for appid, bot in self._bots.items():
            try:
                bot_rows = bot.log_service.query_data(
                    'SELECT group_id, group_name, group_member_num, in_group, allow_proactive_msg '
                    'FROM groups_users WHERE is_full_access=1 AND in_group=1'
                )
            except Exception as e:
                log.debug(f'读取全量群记录失败 {appid}: {e}')
                continue
            rows.extend(
                {
                    'group_id': r['group_id'],
                    'group_name': str(r.get('group_name') or ''),
                    'group_member_num': int(r.get('group_member_num') or 0),
                    'in_group': bool(r.get('in_group', 1)),
                    'allow_proactive_msg': bool(r.get('allow_proactive_msg')),
                    'appid': appid,
                }
                for r in bot_rows
                if r.get('group_id')
            )
        return rows

    # ==================== 生命周期 ====================

    def _log_lifecycle(self, bot, log_type, extra=None, raw_event=None):
        entry = {'type': log_type, 'user_id': '', 'group_id': ''}
        if extra:
            entry.update(extra)
        if raw_event:
            raw_json = json.dumps(raw_event, ensure_ascii=False)
            entry['extra'] = raw_json
        spawn(bot.log_service.add('lifecycle', entry))
        web_entry = {'appid': bot.appid, 'bot_name': bot.name, **entry}
        if raw_event:
            web_entry['raw_message'] = entry['extra']
        self._push_web_log('lifecycle', web_entry)

    async def _handle_group_add(self, bot, event):
        gid = event.group_id or ''
        if gid:
            should_refresh = False
            async with self._group_lock(gid):
                existing = await bot.log_service.db_fetch_one(
                    'SELECT 1 FROM groups_users WHERE group_id=?', (gid,))
                if existing:
                    bot.log_service.db_queue(
                        'UPDATE groups_users SET in_group=1 WHERE group_id=?',
                        (gid,),
                    )
                else:
                    # 先占位，避免同一新群的重复事件并发触发受限接口。
                    await bot.log_service.db_execute(
                        'INSERT OR IGNORE INTO groups_users (group_id, in_group) VALUES (?, 1)',
                        (gid,),
                    )
                    should_refresh = True
            if should_refresh:
                spawn(bot.sender.refresh_group_info(gid))
        self._log_lifecycle(
            bot,
            'group_add',
            {'group_id': gid, 'user_id': event.user_id or ''},
            raw_event=event.raw,
        )
        await self._lifecycle_reply(
            bot,
            event,
            'welcome.group_welcome',
            'welcome',
            {'group_id': gid},
        )

    async def _handle_group_del(self, bot, event):
        if event.group_id:
            bot.log_service.db_queue(
                'UPDATE groups_users SET in_group=0 WHERE group_id=?', (event.group_id,))
        self._log_lifecycle(
            bot,
            'group_del',
            {'group_id': event.group_id or '', 'user_id': event.user_id or ''},
            raw_event=event.raw,
        )

    async def _handle_group_member_add(self, bot, event):
        gid, uid = event.group_id or '', event.user_id or ''
        if gid and uid:
            spawn(self._persist_group_member_change(bot, gid, uid, adding=True))
        self._log_lifecycle(bot, 'group_member_add', {'group_id': gid, 'user_id': uid}, raw_event=event.raw)

    async def _handle_group_member_remove(self, bot, event):
        gid, uid = event.group_id or '', event.user_id or ''
        if gid and uid:
            spawn(self._persist_group_member_change(bot, gid, uid, adding=False))
        self._log_lifecycle(bot, 'group_member_del', {'group_id': gid, 'user_id': uid}, raw_event=event.raw)

    async def _persist_group_member_change(self, bot, group_id, user_id, *, adding):
        changed = (
            await self._add_user_to_group(bot, group_id, user_id)
            if adding
            else await self._remove_user_from_group(bot, group_id, user_id)
        )
        if changed:
            member_num = 'group_member_num+1' if adding else 'MAX(group_member_num-1, 0)'
            bot.log_service.db_queue(
                f'UPDATE groups_users SET group_member_num={member_num} WHERE group_id=?',
                (group_id,),
            )

    async def _handle_group_join_request(self, bot, event):
        self._log_lifecycle(bot, 'group_join_request', {
            'group_id': event.group_id or '',
            'user_id': event.user_id or '',
            'join_request_id': event.join_request_id or '',
            'username': event.username or '',
            'apply_at': event.apply_at or '',
            'apply_source': event.apply_source or '',
            'verify_method': event.verify_method or '',
            'review_qa_list': event.review_qa_list or [],
            'auto_approved': event.auto_approved or {},
        }, raw_event=event.raw)

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
        gid = event.group_id or ''
        uid = event.user_id or ''
        self._log_lifecycle(
            bot, 'group_msg_reject',
            {'group_id': gid, 'user_id': uid},
            raw_event=event.raw,
        )

    async def _handle_subscribe_status(self, bot, event):
        try:
            await bot.log_service.subscribe_record(
                event.subscribe_results, event.group_id or '', event.user_id or '')
        except Exception as e:
            report_error(FRAMEWORK, '订阅记录', e, context={'appid': event.appid})
        self._log_lifecycle(
            bot, 'subscribe_status',
            {'group_id': event.group_id or '', 'user_id': event.user_id or ''},
            raw_event=event.raw,
        )

    async def _handle_group_msg_receive(self, bot, event):
        gid = event.group_id or ''
        uid = event.user_id or ''
        self._log_lifecycle(
            bot, 'group_msg_receive',
            {'group_id': gid, 'user_id': uid},
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
        GROUP_JOIN_REQUEST: _handle_group_join_request,
        FRIEND_ADD: _handle_friend_add,
        FRIEND_DEL: _handle_friend_del,
        GROUP_MSG_REJECT: _handle_group_msg_reject,
        GROUP_MSG_RECEIVE: _handle_group_msg_receive,
        SUBSCRIBE_MESSAGE_STATUS: _handle_subscribe_status,
    }

    # ==================== 用户/群组追踪 ====================

    async def _run_side_tasks(self, item):
        """wakeup + 群组记录 (复用)"""
        tasks = []
        if item.is_direct:
            tasks.append(item.bot.log_service.wakeup_update(item.uid))
        if item.gid and item.gid != 'c2c':
            tasks.append(
                self._add_user_to_group(
                    item.bot, item.gid, item.uid, item.member_role, item.is_bot
                )
            )
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _prune_event_caches(self, now):
        if now < self._cache_clean_ts:
            return
        self._cache_clean_ts = now + _CACHE_PRUNE_INTERVAL
        _prune_expired_entries(
            self._known_users, now, _CACHE_PRUNE_BATCH, lambda value: value
        )
        _prune_expired_entries(
            self._full_access_cache, now, _CACHE_PRUNE_BATCH, lambda value: value
        )
        self._prune_group_locks()

    def _prune_group_locks(self):
        # 群锁只在占位/建群竞态时需要, 空闲后分批回收。
        for group_id in list(islice(self._group_locks, _CACHE_PRUNE_BATCH)):
            lock = self._group_locks.get(group_id)
            if (
                lock is not None
                and not lock.locked()
                and not getattr(lock, '_waiters', None)
            ):
                self._group_locks.pop(group_id, None)

    async def _track_user(self, item):
        uid = item.uid
        bot = item.bot
        now = time.time()

        self._prune_event_caches(now)

        if item.username:
            bot.log_service.db_queue(
                'INSERT INTO users (user_id, name) VALUES (?, ?) '
                'ON CONFLICT(user_id) DO UPDATE SET name=excluded.name '
                "WHERE users.name = '' OR users.name IS NULL",
                (uid, item.username),
            )

        # 已知用户: 跳过 DB 查询
        if uid in self._known_users:
            await self._run_side_tasks(item)
            return

        # 只有开启欢迎且保留了原始事件时才需要查询 existing。
        welcome_enabled = bool(
            item.reply_event is not None
            and cfg.get_bot_setting(item.appid, 'welcome.new_user_welcome', False)
        )
        existing = True
        if welcome_enabled:
            existing = await bot.log_service.db_fetch_one(
                'SELECT user_id FROM users WHERE user_id=?',
                (uid,),
            )

        # 没有昵称时直接幂等建档，避免额外 SELECT。
        if not item.username:
            bot.log_service.db_queue(
                'INSERT OR IGNORE INTO users (user_id) VALUES (?)',
                (uid,),
            )

        self._known_users[uid] = now + _USER_CACHE_TTL

        if not welcome_enabled:
            await self._run_side_tasks(item)
            return

        # 群成员写回在后台并行进行，不阻塞新用户欢迎。
        side_task = asyncio.create_task(self._run_side_tasks(item))
        try:
            if not existing:
                total = await bot.log_service.db_fetch_value('SELECT COUNT(*) FROM users', default=1)
                await bot.sender.reply(
                    item.reply_event,
                    template_name='user_welcome',
                    template_vars={'user_id': uid, 'user_count': str(total)},
                )
        except Exception as e:
            report_error(FRAMEWORK, '新用户欢迎', e, context={'appid': item.appid})
        finally:
            await side_task

    # ==================== 群组成员记录 ====================
    #
    # 成员自 2.1.0 起存放在 group_members (一用户一行): 消息路径只写一行,
    # 不再有进程内成员缓存、脏集合、批量刷库与跨天过期。

    async def _add_user_to_group(self, bot, group_id, user_id, member_role='', is_bot=False):
        """记录群成员活跃; 返回该成员是否为本次新增"""
        try:
            return await bot.log_service.group_member_touch(
                group_id, user_id, _today_str(), member_role, is_bot=is_bot,
            )
        except Exception as e:
            report_error(
                FRAMEWORK, '群成员记录', e,
                context={'group_id': group_id, 'user_id': str(user_id)},
            )
            return False

    async def _remove_user_from_group(self, bot, group_id, user_id):
        """移除群成员; 返回该成员此前是否存在"""
        try:
            return await bot.log_service.group_member_remove(group_id, user_id)
        except Exception as e:
            report_error(
                FRAMEWORK, '群成员移除', e,
                context={'group_id': group_id, 'user_id': str(user_id)},
            )
            return False

    def _group_lock(self, group_id):
        """取或建群级写锁 (建群占位等竞态场景使用)"""
        lock = self._group_locks.get(group_id)
        if lock is None:
            lock = self._group_locks[group_id] = asyncio.Lock()
        return lock
