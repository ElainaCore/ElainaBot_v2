#!/usr/bin/env python
"""SQLite 日志 + 数据服务 — LogService / SharedLogService"""

import asyncio
import contextlib
import os
from datetime import datetime, timedelta

from core.base.logger import SERVICE, get_logger, now_str, on_error
from core.base.metrics import counter
from core.base.tasks import spawn
from core.storage._base import _BaseLogService
from core.storage._schema import (
    _QUEUE_MAXSIZE,
    ALL_TYPES,
    _json_field,
)
from core.storage.members import build_member_entry
from core.storage.share import ShareMixin
from core.storage.subscribe import SubscribeMixin
from core.storage.wakeup import WakeupMixin

log = get_logger(SERVICE, '日志')


class LogService(_BaseLogService, ShareMixin, WakeupMixin, SubscribeMixin):
    """SQLite 日志服务 (每个 bot 一个实例, 异步)"""

    _global_callbacks_registered = False
    _all_instances: list['LogService'] = []  # 所有活跃实例 (全局回调分发到每个实例)

    def __init__(
        self,
        base_dir,
        appid,
        wal_mode=True,
        insert_interval=2,
        batch_size=0,
        retention_days=5,
    ):
        super().__init__(
            os.path.join(base_dir, str(appid)),
            wal_mode,
            insert_interval,
            batch_size,
            retention_days,
            ALL_TYPES,
        )
        self._appid = str(appid)
        self._log_tag = self._appid
        self._data_write_queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)

    async def start(self):
        """启动日志服务"""
        LogService._all_instances.append(self)
        if not LogService._global_callbacks_registered:
            LogService._global_callbacks_registered = True
            on_error(LogService._global_error_dispatch)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._ensure_today_message_schema)
        # 昨日库结构/索引后台预热, 避免首次统计查询时现场建索引
        loop.run_in_executor(None, self._warm_yesterday_message_schema)
        await self._start_tasks()

    def _ensure_today_message_schema(self):
        """启动时主动校验当天 message.db 结构。"""
        self._get_conn(self._resolve_db_path('message'), 'message')

    def _warm_yesterday_message_schema(self):
        """预热昨日 message.db (补建缺失索引供同期对比查询)"""
        try:
            yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
            db_path = self._resolve_db_path('message', yesterday)
            if os.path.isfile(db_path):
                self._get_conn(db_path, 'message')
        except Exception as e:
            log.warning(f'[{self._log_tag}] 昨日消息库预热失败: {e}')

    async def shutdown(self):
        """关闭日志服务, 刷写缓冲"""
        with contextlib.suppress(ValueError):
            LogService._all_instances.remove(self)
        await self._shutdown_base()
        log.info(f'[{self._appid}] 日志服务已关闭')

    async def add(self, log_type, data):
        """添加日志条目到队列 (队列满时丢弃, 不阻塞)"""
        if log_type not in ALL_TYPES:
            return False
        try:
            self._queues[log_type].put_nowait(data)
        except asyncio.QueueFull:
            return False
        # DAU 立即刷写 (异步后台, 不阻塞调用方)
        if log_type == 'dau':
            spawn(self._flush_type('dau'))
        return True

    def query_data(self, sql, params=()):
        """同步查询 data.db (users/groups/members 表)"""
        return self.query('data', sql, params)

    def _extract_row(self, log_type, data):
        """dict → INSERT 参数元组"""
        ts = data.get('timestamp', now_str())
        if log_type == 'message':
            def _s(v): return str(v) if not isinstance(v, str) else v
            return (
                ts,
                _s(data.get('message_id', '')),
                _s(data.get('reference_id', '')),
                _s(data.get('user_id', '')),
                _s(data.get('group_id', '')),
                _s(data.get('content', '')),
                _s(data.get('raw_message', '')),
                _s(data.get('plugin_name', '')),
                _s(data.get('direction', '')),
                1 if data.get('at_bot', True) else 0,
                _json_field(data, 'context', ''),
            )
        common = self._extract_common_row(log_type, data, ts)
        if common:
            return common
        if log_type == 'dau':
            return (
                data.get('date', datetime.now().strftime('%Y-%m-%d')),
                data.get('active_users', 0),
                data.get('active_groups', 0),
                data.get('total_messages', 0),
                data.get('private_messages', 0),
                data.get('received_messages', 0),
                data.get('sent_messages', 0),
                data.get('group_join_count', 0),
                data.get('group_leave_count', 0),
                data.get('friend_add_count', 0),
                data.get('friend_remove_count', 0),
                _json_field(data, 'message_stats_detail'),
                _json_field(data, 'user_stats_detail'),
                _json_field(data, 'command_stats_detail'),
            )
        if log_type == 'lifecycle':
            return (
                ts,
                data.get('type', ''),
                data.get('user_id', ''),
                data.get('group_id', ''),
                data.get('extra', ''),
            )
        return None

    async def _flush_all(self):
        await super()._flush_all()
        await self._flush_data_queue()

    def db_queue(self, sql, params=()):
        """写操作放入队列, 随下次 flush 批量执行"""
        with contextlib.suppress(asyncio.QueueFull):
            self._data_write_queue.put_nowait((sql, params))

    async def _flush_data_queue(self):
        q = self._data_write_queue
        if q.empty():
            return
        ops = []
        while not q.empty():
            try:
                ops.append(q.get_nowait())
            except asyncio.QueueEmpty:
                break
        if ops:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._flush_data_queue_sync, ops)

    def _flush_data_queue_sync(self, ops):
        conn = self._data_conn
        lock = self._data_lock
        try:
            with lock:
                # 按 SQL 分组, 相同语句用 executemany 批量执行
                grouped = {}
                for sql, params in ops:
                    grouped.setdefault(sql, []).append(params)
                for sql, params_list in grouped.items():
                    if len(params_list) == 1:
                        conn.execute(sql, params_list[0])
                    else:
                        conn.executemany(sql, params_list)
                conn.commit()
        except Exception as e:
            log.error(f'[{self._appid}] data.db 批量写入失败: {e}')
            with contextlib.suppress(Exception):
                conn.rollback()

    @property
    def _data_conn(self):
        return self._get_conn(self._resolve_db_path('data'), 'data')

    @property
    def _data_lock(self):
        # 首次成员热路径可能直接写 data.db，先确保连接和 schema 已完成初始化。
        db_path = self._resolve_db_path('data')
        self._get_conn(db_path, 'data')
        return self._conn_locks[db_path]

    async def db_execute(self, sql, params=()):
        """执行写操作, 返回 lastrowid"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._db_execute_sync, sql, params)

    def _db_execute_sync(self, sql, params):
        with self._data_lock:
            cursor = self._data_conn.execute(sql, params)
            self._data_conn.commit()
            return cursor.lastrowid

    async def db_execute_many(self, sql, params_list):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._db_execute_many_sync, sql, params_list)

    def _db_execute_many_sync(self, sql, params_list):
        with self._data_lock:
            self._data_conn.executemany(sql, params_list)
            self._data_conn.commit()

    async def db_execute_ops(self, ops):
        """在单个事务中执行多条语句, 返回首条语句的 rowcount。

        用于"插入判定 + 条件更新"这类需要原子完成、又要拿到影响行数的写入。
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._db_execute_ops_sync, ops)

    def _db_execute_ops_sync(self, ops):
        conn = self._data_conn
        with self._data_lock:
            try:
                first = 0
                for index, (sql, params) in enumerate(ops):
                    cursor = conn.execute(sql, params)
                    if index == 0:
                        first = cursor.rowcount
                conn.commit()
                return first
            except Exception:
                with contextlib.suppress(Exception):
                    conn.rollback()
                raise

    async def db_execute_script(self, script):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._db_execute_script_sync, script)

    def _db_execute_script_sync(self, script):
        with self._data_lock:
            self._data_conn.executescript(script)

    async def db_fetch_one(self, sql, params=()):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._db_fetch_one_sync, sql, params)

    def _db_fetch_one_sync(self, sql, params):
        rows = self.query('data', sql, params)
        return rows[0] if rows else None

    async def db_fetch_all(self, sql, params=()):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._db_fetch_all_sync, sql, params)

    def _db_fetch_all_sync(self, sql, params):
        return self.query('data', sql, params)

    async def db_fetch_value(self, sql, params=(), default=None):
        """查询单个值"""
        row = await self.db_fetch_one(sql, params)
        return list(row.values())[0] if row else default

    async def db_upsert(self, table, data, conflict_columns):
        """INSERT OR UPDATE"""
        columns = list(data.keys())
        values = list(data.values())
        placeholders = ','.join(['?'] * len(columns))
        col_str = ','.join(columns)
        update_cols = [c for c in columns if c not in conflict_columns]
        conflict_str = ','.join(conflict_columns)
        sql = f'INSERT INTO {table} ({col_str}) VALUES ({placeholders})'
        if update_cols:
            update_str = ','.join(f'{c}=excluded.{c}' for c in update_cols)
            sql += f' ON CONFLICT({conflict_str}) DO UPDATE SET {update_str}'
        else:
            sql += f' ON CONFLICT({conflict_str}) DO NOTHING'
        return await self.db_execute(sql, values)

    async def db_table_exists(self, table_name):
        row = await self.db_fetch_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        )
        return row is not None

    # ==================== 群成员 (group_members / bots) ====================
    #
    # 成员数据自 2.1.0 起一用户一行, 消息路径只写一行, 不再整群读写。
    # 所有写入都走 db_execute* (提交确认), 不使用可能静默丢弃的 db_queue。

    _MEMBER_INSERT_SQL = (
        'INSERT OR IGNORE INTO group_members '
        '(group_id, user_id, last_active, member_role, extra) VALUES (?,?,?,?,?)'
    )
    # 活跃时间或角色没有变化时不动库, 与迁移前的"当天重复消息跳过写入"一致
    _MEMBER_TOUCH_SQL = (
        'UPDATE group_members SET last_active=?, '
        "member_role=CASE WHEN ?<>'' THEN ? ELSE member_role END "
        'WHERE group_id=? AND user_id=? '
        "AND (last_active<>? OR (?<>'' AND member_role<>?))"
    )
    # 成员同步路径: 整行覆盖 (含 extra), 角色为空时保留原值
    _MEMBER_UPDATE_SQL = (
        'UPDATE group_members SET last_active=?, '
        "member_role=CASE WHEN ?<>'' THEN ? ELSE member_role END, extra=? "
        'WHERE group_id=? AND user_id=?'
    )
    _MEMBER_SET_SQL = (
        'INSERT INTO group_members '
        '(group_id, user_id, last_active, member_role, extra) VALUES (?,?,?,?,?) '
        'ON CONFLICT(group_id, user_id) DO UPDATE SET '
        'last_active=excluded.last_active, member_role=excluded.member_role, '
        'extra=excluded.extra'
    )
    _BOT_MARK_SQL = (
        'INSERT OR IGNORE INTO bots (user_id, first_seen) VALUES (?, ?)'
    )
    _GROUP_ENSURE_SQL = (
        'INSERT OR IGNORE INTO groups_users (group_id, in_group) VALUES (?, 1)'
    )

    @staticmethod
    def _member_ops(group_id, user_id, last_active, member_role, extra, is_bot, keep_extra):
        """组装"插入判定 + 更新"语句序列; keep_extra=True 时不动已有 extra。"""
        ops = [
            (LogService._MEMBER_INSERT_SQL,
             (group_id, user_id, last_active, member_role, extra)),
            # 与迁移前一致: 成员事件会为未知群补一条群记录
            (LogService._GROUP_ENSURE_SQL, (group_id,)),
        ]
        if keep_extra:
            ops.append((
                LogService._MEMBER_TOUCH_SQL,
                (last_active, member_role, member_role, group_id, user_id,
                 last_active, member_role, member_role),
            ))
        else:
            ops.append((
                LogService._MEMBER_UPDATE_SQL,
                (last_active, member_role, member_role, extra, group_id, user_id),
            ))
        if is_bot:
            ops.append((LogService._BOT_MARK_SQL, (user_id, now_str())))
        return tuple(ops)

    async def group_member_touch(self, group_id, user_id, last_active,
                                 member_role='', is_bot=False):
        """记录成员活跃 (消息热路径)。返回是否为新成员。

        只更新活跃时间与角色, 保留成员同步写入的其他字段 (extra)。
        """
        group_id, user_id = str(group_id or ''), str(user_id or '')
        if not group_id or not user_id:
            return False
        ops = self._member_ops(
            group_id, user_id, str(last_active or ''),
            str(member_role or ''), '', is_bot, keep_extra=True,
        )
        added = await self.db_execute_ops(ops) == 1
        counter('group_member_writes')
        if added:
            counter('group_member_new')
        return added

    async def group_member_set(self, group_id, user_id, last_active,
                               member_role='', extra='', is_bot=False):
        """整行写入一个成员 (成员同步路径)。返回是否为新成员。"""
        group_id, user_id = str(group_id or ''), str(user_id or '')
        if not group_id or not user_id:
            return False
        ops = self._member_ops(
            group_id, user_id, str(last_active or ''),
            str(member_role or ''), str(extra or ''), is_bot, keep_extra=False,
        )
        added = await self.db_execute_ops(ops) == 1
        counter('group_member_writes')
        return added

    async def group_member_remove(self, group_id, user_id):
        """移除一个群成员, 返回该成员此前是否存在。"""
        removed = await self.db_execute_ops((
            ('DELETE FROM group_members WHERE group_id=? AND user_id=?',
             (str(group_id or ''), str(user_id or ''))),
        )) == 1
        if removed:
            counter('group_member_removes')
        return removed

    async def group_member_rows(self, group_id):
        """读取成员原始行 (供成员同步合并), 返回 [dict]。"""
        return await self.db_fetch_all(
            'SELECT user_id, last_active, member_role, extra '
            'FROM group_members WHERE group_id=?',
            (str(group_id or ''),),
        )

    async def group_members_write(self, rows):
        """批量整行写入成员 (user_id, last_active, member_role, extra), 单事务。"""
        if not rows:
            return 0
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._group_members_write_sync, rows)
        counter('group_member_writes', len(rows))
        return len(rows)

    def _group_members_write_sync(self, rows):
        """批量写成员并确保群元数据行存在。"""
        with self._data_lock:
            conn = self._data_conn
            try:
                for group_id, *_ in rows:
                    conn.execute(self._GROUP_ENSURE_SQL, (str(group_id),))
                conn.executemany(self._MEMBER_SET_SQL, rows)
                conn.commit()
            except Exception:
                with contextlib.suppress(Exception):
                    conn.rollback()
                raise

    async def group_bot_mark(self, user_id):
        """登记机器人账号 (全局去重)。"""
        user_id = str(user_id or '')
        if not user_id:
            return
        await self.db_execute(self._BOT_MARK_SQL, (user_id, now_str()))

    # ---- 读取接口 ----
    # 每个接口都提供同步实现 (插件在工作线程中直接调用) 与异步包装 (框架内部用),
    # 让调用方不必知道成员存在哪张表、有哪些列。

    def group_member_entries_sync(self, group_id, limit=None):
        """群成员完整条目 (公开 API 形状, 含 is_bot 标记), 同步版。"""
        sql = (
            'SELECT m.user_id, m.last_active, m.member_role, m.extra, '
            'b.user_id IS NOT NULL AS is_bot '
            'FROM group_members m LEFT JOIN bots b ON b.user_id = m.user_id '
            'WHERE m.group_id=?'
        )
        params = [str(group_id or '')]
        if limit is not None:
            sql += ' LIMIT ?'
            params.append(int(limit))
        rows = self.query_data(sql, tuple(params)) or []
        return [
            build_member_entry(
                row['user_id'], row['last_active'], row['member_role'],
                row['extra'], bool(row['is_bot']),
            )
            for row in rows
        ]

    async def group_member_entries(self, group_id, limit=None):
        """群成员完整条目 (异步包装)。"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self.group_member_entries_sync, group_id, limit)

    def group_member_ids_sync(self, group_id, *, exclude_bots=True,
                              roles=None, limit=None):
        """群成员 ID 列表 (同步版)。

        exclude_bots: 排除机器人账号; roles: 只取这些角色, 如 ('admin','owner');
        limit: 最多返回多少个 (抽人场景)。
        """
        sql = 'SELECT m.user_id FROM group_members m WHERE m.group_id=?'
        params = [str(group_id or '')]
        if exclude_bots:
            sql += ' AND m.user_id NOT IN (SELECT user_id FROM bots)'
        if roles:
            placeholders = ','.join('?' for _ in roles)
            sql += f' AND m.member_role IN ({placeholders})'
            params.extend(str(role) for role in roles)
        if limit is not None:
            sql += ' LIMIT ?'
            params.append(int(limit))
        rows = self.query_data(sql, tuple(params)) or []
        return [str(row['user_id']) for row in rows if row.get('user_id')]

    async def group_member_ids(self, group_id, exclude_bots=True, *,
                               roles=None, limit=None):
        """群成员 ID 列表 (异步包装)。"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.group_member_ids_sync(
                group_id, exclude_bots=exclude_bots,
                roles=roles, limit=limit),
        )

    def group_admin_ids_sync(self, group_id):
        """群主与管理员 ID (同步版)。"""
        return self.group_member_ids_sync(group_id, roles=('admin', 'owner'))

    def group_member_roles_sync(self, group_id):
        """成员角色映射 {user_id: {role?, is_bot?}} (同步版, 面板/群管用)。"""
        rows = self.query_data(
            'SELECT m.user_id, m.member_role, b.user_id IS NOT NULL AS is_bot '
            'FROM group_members m LEFT JOIN bots b ON b.user_id = m.user_id '
            'WHERE m.group_id=?',
            (str(group_id or ''),),
        ) or []
        result = {}
        for row in rows:
            info = {}
            if row.get('member_role'):
                info['role'] = str(row['member_role'])
            if row.get('is_bot'):
                info['is_bot'] = True
            if info:
                result[str(row['user_id'])] = info
        return result

    async def group_member_roles(self, group_id):
        """成员角色映射 (异步包装)。"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self.group_member_roles_sync, group_id)

    def group_last_active_map_sync(self, limit, offset=0):
        """跨群扫描: [{'group_id', 'last_active'}] (同步版)。

        供"找出长期不活跃的群"这类需要遍历所有群的场景; last_active 取该群
        成员的最大值, 群内没有任何成员行时为 None。
        """
        return self.query_data(
            'SELECT g.group_id AS group_id, '
            '(SELECT MAX(m.last_active) FROM group_members m '
            ' WHERE m.group_id = g.group_id) AS last_active '
            'FROM groups_users g ORDER BY g.group_id LIMIT ? OFFSET ?',
            (int(limit), int(offset)),
        ) or []

    def group_member_count_sync(self, group_id):
        """群成员数 (同步版)。"""
        rows = self.query_data(
            'SELECT COUNT(*) AS total FROM group_members WHERE group_id=?',
            (str(group_id or ''),),
        ) or []
        return int(rows[0]['total']) if rows else 0

    async def group_member_count(self, group_id):
        """群成员数 (异步包装)。"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self.group_member_count_sync, group_id)

    async def group_row_delete(self, group_id):
        """机器人退群: 单事务清掉群记录与其成员行。"""
        group_id = str(group_id or '')
        await self.db_execute_ops((
            ('DELETE FROM group_members WHERE group_id=?', (group_id,)),
            ('DELETE FROM groups_users WHERE group_id=?', (group_id,)),
        ))

    @staticmethod
    def _global_error_dispatch(error_data):
        if SharedLogService._instance:
            SharedLogService._instance.add_sync('error', error_data)

    @staticmethod
    def _global_framework_dispatch(log_data):
        if SharedLogService._instance:
            SharedLogService._instance.add_sync('framework', log_data)


# ==================== 通用日志服务 ====================


class SharedLogService(_BaseLogService):
    """通用日志服务 — framework.db / error.db, 不分机器人"""

    _instance = None  # 单例, 供 LogService 回调桥接使用

    def __init__(self, base_dir, wal_mode=True, insert_interval=2, retention_days=5):
        super().__init__(
            base_dir,
            wal_mode,
            insert_interval,
            0,
            retention_days,
            ('framework', 'error'),
        )
        self._log_tag = '通用日志'

    async def start(self):
        SharedLogService._instance = self
        await self._start_tasks()

    async def shutdown(self):
        SharedLogService._instance = None
        await self._shutdown_base()
        log.info('[通用日志] 已关闭')

    def _extract_row(self, log_type, data):
        ts = data.get('timestamp', now_str())
        return self._extract_common_row(log_type, data, ts)
