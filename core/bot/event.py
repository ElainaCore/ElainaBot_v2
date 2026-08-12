#!/usr/bin/env python
"""事件处理 Mixin — 事件分发 / 去重 / 生命周期 / 用户追踪 / 群组记录"""

import asyncio
import json
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
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
_GROUP_CACHE_MAX = 10000
_FULL_ACCESS_CACHE_TTL = 1800
_DIRTY_FLUSH_THRESHOLD = 500  # 脏群数超过此阈值提前刷写
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


def _new_user_entry(uid, today, member_role='', is_bot=False):
    entry = {'userid': uid, 'value': 1, 'last_active': today}
    if member_role:
        entry['member_role'] = member_role
    if is_bot:
        entry['is_bot'] = True
    return entry


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
        self._group_users_cache = {}
        self._group_locks = {}
        self._full_access_cache = {}  # {(appid, group_id): expire_ts}
        self._dirty_groups = {}  # {group_id: bot} — 待写入的群缓存
        self._flush_task = None
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
        if gid and uid and await self._add_user_to_group(bot, gid, uid):
            bot.log_service.db_queue(
                'UPDATE groups_users SET group_member_num=group_member_num+1 WHERE group_id=?',
                (gid,),
            )
        self._log_lifecycle(bot, 'group_member_add', {'group_id': gid, 'user_id': uid}, raw_event=event.raw)

    async def _handle_group_member_remove(self, bot, event):
        gid, uid = event.group_id or '', event.user_id or ''
        if gid and uid and await self._remove_user_from_group(bot, gid, uid):
            bot.log_service.db_queue(
                'UPDATE groups_users SET group_member_num=MAX(group_member_num-1, 0) WHERE group_id=?',
                (gid,),
            )
        self._log_lifecycle(bot, 'group_member_del', {'group_id': gid, 'user_id': uid}, raw_event=event.raw)

    async def _handle_group_join_request(self, bot, event):
        self._log_lifecycle(bot, 'group_join_request', {
            'group_id': event.group_id or '', 'user_id': event.user_id or ''}, raw_event=event.raw)

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
        self._prune_group_caches(now)

    def _prune_group_caches(self, now):
        # LRU 的头部是最冷的缓存，每次只检查有限数量，避免整表重建。
        for group_id in list(islice(self._group_users_cache, _CACHE_PRUNE_BATCH)):
            cached = self._group_users_cache.get(group_id)
            if cached and cached[0] <= now and group_id not in self._dirty_groups:
                self._group_users_cache.pop(group_id, None)
                lock = self._group_locks.get(group_id)
                if (
                    lock is not None
                    and not lock.locked()
                    and not getattr(lock, '_waiters', None)
                ):
                    self._group_locks.pop(group_id, None)

        # 没有对应缓存的失败/空查询也可能留下群锁，分批清除。
        for group_id in list(islice(self._group_locks, _CACHE_PRUNE_BATCH)):
            lock = self._group_locks.get(group_id)
            if (
                group_id not in self._group_users_cache
                and lock is not None
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

    def _upsert_group_user(self, user_map, uid, today, member_role='', is_bot=False):
        """更新或新增群成员条目, 返回是否有变更"""
        entry = user_map.get(uid)
        if entry is None:
            user_map[uid] = _new_user_entry(uid, today, member_role, is_bot)
            return True
        changed = False
        if entry.get('last_active') != today:
            entry['last_active'] = today
            changed = True
        if member_role and entry.get('member_role') != member_role:
            entry['member_role'] = member_role
            changed = True
        if is_bot and not entry.get('is_bot'):
            entry['is_bot'] = True
            changed = True
        return changed

    def _ensure_flush_task(self):
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.create_task(self._flush_dirty_groups())

    def _mark_group_dirty(self, group_id, bot):
        """标记群缓存待落库, 由 _flush_dirty_groups 批量写回"""
        self._dirty_groups[group_id] = bot
        self._ensure_flush_task()
        if len(self._dirty_groups) >= _DIRTY_FLUSH_THRESHOLD:
            self._force_flush_dirty()

    def _force_flush_dirty(self):
        """脏群数超过阈值时立即刷写, 降低内存峰值"""
        if not self._dirty_groups:
            return
        batch, self._dirty_groups = self._dirty_groups, {}
        for gid, bot in batch.items():
            cached = self._group_users_cache.get(gid)
            if cached:
                bot.log_service.db_queue(
                    'UPDATE groups_users SET users=? WHERE group_id=?',
                    (self._users_json(cached[1]), gid),
                )

    async def _flush_dirty_groups(self):
        while True:
            await asyncio.sleep(30)
            if not self._dirty_groups:
                continue
            self._force_flush_dirty()

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
        """群成员表变更统一入口"""
        async with self._group_lock(group_id):
            # 1. 内存缓存命中: 仅改内存 + 标脏, 由 _flush_dirty_groups 批量落库
            cached = self._group_users_cache.get(group_id)
            if cached and time.time() < cached[0]:
                # LRU: 命中后移到末尾, 保证热点群在大规模(群数>>缓存上限)下不被冷群挤出
                self._group_users_cache.pop(group_id, None)
                self._group_users_cache[group_id] = cached
                changed = mutate(cached[1])
                if changed:
                    self._mark_group_dirty(group_id, bot)
                return changed
            self._group_users_cache.pop(group_id, None)

            # 2. DB 加载
            try:
                user_map, existed = await self._load_group_user_map(bot, group_id)
                if not existed and not create_if_missing:
                    return False
                changed = mutate(user_map)
                if changed:
                    bot.log_service.db_queue(
                        'INSERT INTO groups_users (group_id, users) VALUES (?, ?) '
                        'ON CONFLICT(group_id) DO UPDATE SET users=excluded.users',
                        (group_id, self._users_json(user_map)),
                    )
                self._set_group_cache(group_id, user_map)
                return changed
            except Exception as e:
                report_error(
                    FRAMEWORK,
                    '群用户列表更新',
                    e,
                    context={'group_id': group_id},
                )
                return False

    async def _add_user_to_group(self, bot, group_id, user_id, member_role='', is_bot=False):
        uid = str(user_id)
        today = _today_str()
        added = False

        def upsert(user_map):
            nonlocal added
            added = uid not in user_map
            return self._upsert_group_user(user_map, uid, today, member_role, is_bot)

        changed = await self._mutate_group_user(
            bot,
            group_id,
            upsert,
            create_if_missing=True,
        )
        return changed and added

    async def _remove_user_from_group(self, bot, group_id, user_id):
        uid = str(user_id)
        return await self._mutate_group_user(
            bot,
            group_id,
            lambda user_map: user_map.pop(uid, None) is not None,
            create_if_missing=False,
        )

    def _set_group_cache(self, group_id, user_map):
        if len(self._group_users_cache) >= _GROUP_CACHE_MAX and group_id not in self._group_users_cache:
            # 脏群必须等批量写回后再淘汰；优先移除最老的干净缓存。
            oldest = next(
                (gid for gid in self._group_users_cache if gid not in self._dirty_groups),
                None,
            )
            if oldest is not None:
                del self._group_users_cache[oldest]
        expire = self._tomorrow_ts()
        self._group_users_cache[group_id] = (expire, user_map)
