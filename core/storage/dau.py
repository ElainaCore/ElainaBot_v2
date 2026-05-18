#!/usr/bin/env python
"""DAU 统计 — 每日凌晨自动扫描 SQLite 消息日志, 计算昨日统计并写入 dau.db"""

import asyncio
import contextlib
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta

from core.base.logger import FRAMEWORK, get_logger
from core.storage._schema import DAU_TABLE_SQL

log = get_logger(FRAMEWORK, 'DAU统计')

# 默认每日执行时间
_SCHEDULE_HOUR = 0
_SCHEDULE_MINUTE = 10


class DAUService:
    """DAU 统计服务 — 异步调度 + SQLite 直读"""

    __slots__ = ('_log_dir', '_running', '_task', '_schedule_hour', '_schedule_minute')

    def __init__(
        self,
        log_base_dir,
        schedule_hour=_SCHEDULE_HOUR,
        schedule_minute=_SCHEDULE_MINUTE,
    ):
        self._log_dir = os.path.abspath(log_base_dir)
        self._schedule_hour = schedule_hour
        self._schedule_minute = schedule_minute
        self._running = False
        self._task = None

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._scheduler_loop())
        log.info(f'已启动 [每日 {self._schedule_hour:02d}:{self._schedule_minute:02d}]')

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None

    async def _scheduler_loop(self):
        while self._running:
            try:
                next_run = self._next_run_time()
                wait = (next_run - datetime.now()).total_seconds()
                if wait > 0:
                    await asyncio.sleep(wait)
                if not self._running:
                    break
                await self.regenerate_yesterday()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning(f'调度异常: {e}')
                await asyncio.sleep(60)

    def _next_run_time(self):
        now = datetime.now()
        today_run = now.replace(
            hour=self._schedule_hour,
            minute=self._schedule_minute,
            second=0,
            microsecond=0,
        )
        return today_run if today_run > now else today_run + timedelta(days=1)

    async def regenerate_yesterday(self):
        """重算所有机器人昨日 DAU"""
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        return await self.regenerate_date(yesterday)

    async def regenerate_date(self, date_str):
        """重算指定日期所有机器人 DAU, 返回 {appid: bool}"""
        results = {}
        appids = self.list_appids()
        log.info(f'开始统计 [{date_str}], {len(appids)} 个机器人')
        for appid in appids:
            try:
                ok = await self.regenerate(appid, date_str)
                results[appid] = ok
            except Exception as e:
                log.warning(f'[{appid}] 统计失败: {e}')
                results[appid] = False
        log.info(f'统计完成 [{date_str}]: {sum(results.values())}/{len(results)} 成功')
        return results

    async def regenerate(self, appid, date_str):
        """重算指定机器人 + 日期的 DAU"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._regenerate_sync, appid, date_str)

    async def load(self, appid, date_str):
        """读取已存储的 DAU 数据"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._load_sync, appid, date_str)

    async def recent(self, appid, days=7):
        """读取最近 N 天 DAU"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._recent_sync, appid, days)

    # 日期目录格式 (SharedLogService 的 framework/error 日志), 不是 bot appid
    _DATE_DIR_PATTERN = re.compile(r'^\d{4}-\d{2}-\d{2}$')

    def list_appids(self):
        """列出所有有日志记录的 appid (排除 SharedLogService 的日期目录)"""
        if not os.path.isdir(self._log_dir):
            return []
        return [
            name
            for name in os.listdir(self._log_dir)
            if os.path.isdir(os.path.join(self._log_dir, name)) and not self._DATE_DIR_PATTERN.match(name)
        ]

    def _message_db_path(self, appid, date_str):
        return os.path.join(self._log_dir, appid, date_str, 'message.db')

    def _lifecycle_db_path(self, appid, date_str):
        return os.path.join(self._log_dir, appid, date_str, 'lifecycle.db')

    def _dau_db_path(self, appid):
        return os.path.join(self._log_dir, appid, 'dau.db')

    def _regenerate_sync(self, appid, date_str):
        msg_db = self._message_db_path(appid, date_str)
        if not os.path.isfile(msg_db):
            log.debug(f'[{appid}] {date_str} 无消息日志')
            return False

        msg_stats = self._compute_message_stats(msg_db)
        if not msg_stats:
            return False

        # 事件计数: 从 lifecycle.db 读取
        event_stats = self._compute_lifecycle_stats(appid, date_str)

        self._save_dau(appid, date_str, msg_stats, event_stats)
        log.info(f'[{appid}] {date_str} 已统计: 消息 {msg_stats["total_messages"]}, 用户 {msg_stats["active_users"]}, 群 {msg_stats["active_groups"]}')
        return True

    def _compute_message_stats(self, db_path):
        try:
            conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=10)
            conn.row_factory = sqlite3.Row
        except sqlite3.Error as e:
            log.warning(f'打开消息库失败 [{db_path}]: {e}')
            return None
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT COUNT(*) AS total,
                       COUNT(DISTINCT CASE WHEN user_id != '' THEN user_id END) AS users,
                       COUNT(DISTINCT CASE WHEN group_id != '' AND group_id != 'c2c'
                                           THEN group_id END) AS groups_,
                       COUNT(CASE WHEN group_id = 'c2c' OR group_id = '' THEN 1 END) AS private
                FROM log
            """)
            row = cur.fetchone()
            if not row or row['total'] == 0:
                return None

            stats = {
                'total_messages': row['total'],
                'active_users': row['users'],
                'active_groups': row['groups_'],
                'private_messages': row['private'],
            }

            cur.execute("""
                SELECT substr(timestamp, 12, 2) AS hr, COUNT(*) AS c
                FROM log GROUP BY hr ORDER BY c DESC LIMIT 1
            """)
            r = cur.fetchone()
            stats['peak_hour'] = int(r['hr']) if r and r['hr'] else 0
            stats['peak_hour_count'] = r['c'] if r else 0

            cur.execute("""
                SELECT group_id, COUNT(*) AS c FROM log
                WHERE group_id != '' AND group_id != 'c2c'
                GROUP BY group_id ORDER BY c DESC LIMIT 10
            """)
            stats['top_groups'] = [{'group_id': r['group_id'], 'message_count': r['c']} for r in cur.fetchall()]

            cur.execute("""
                SELECT user_id, COUNT(*) AS c FROM log
                WHERE user_id != ''
                GROUP BY user_id ORDER BY c DESC LIMIT 10
            """)
            stats['top_users'] = [{'user_id': r['user_id'], 'message_count': r['c']} for r in cur.fetchall()]

            cur.execute("""
                SELECT plugin_name, COUNT(*) AS c FROM log
                WHERE plugin_name != ''
                GROUP BY plugin_name ORDER BY c DESC LIMIT 10
            """)
            stats['top_commands'] = [{'command': r['plugin_name'], 'count': r['c']} for r in cur.fetchall()]
            return stats
        except Exception as e:
            log.warning(f'计算统计失败: {e}')
            return None
        finally:
            conn.close()

    _EMPTY_LIFECYCLE = {
        'group_join_count': 0,
        'group_leave_count': 0,
        'friend_add_count': 0,
        'friend_remove_count': 0,
    }

    def _compute_lifecycle_stats(self, appid, date_str):
        conn = self._open_ro(self._lifecycle_db_path(appid, date_str))
        if not conn:
            return dict(self._EMPTY_LIFECYCLE)
        try:
            cur = conn.execute('SELECT type, COUNT(*) AS c FROM log GROUP BY type')
            counts = {row['type']: row['c'] for row in cur.fetchall()}
            return {
                'group_join_count': counts.get('group_add', 0),
                'group_leave_count': counts.get('group_del', 0),
                'friend_add_count': counts.get('friend_add', 0),
                'friend_remove_count': counts.get('friend_del', 0),
            }
        except Exception as e:
            log.debug(f'[{appid}] 读取 lifecycle 统计失败: {e}')
            return dict(self._EMPTY_LIFECYCLE)
        finally:
            conn.close()

    def _save_dau(self, appid, date_str, msg_stats, event_stats):
        dau_path = self._dau_db_path(appid)
        os.makedirs(os.path.dirname(dau_path), exist_ok=True)
        conn = sqlite3.connect(dau_path, timeout=10)
        try:
            conn.execute(DAU_TABLE_SQL)
            conn.execute(
                """
                INSERT INTO log (date, active_users, active_groups, total_messages,
                                 private_messages, group_join_count, group_leave_count,
                                 friend_add_count, friend_remove_count,
                                 message_stats_detail, user_stats_detail, command_stats_detail)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(date) DO UPDATE SET
                    active_users=excluded.active_users,
                    active_groups=excluded.active_groups,
                    total_messages=excluded.total_messages,
                    private_messages=excluded.private_messages,
                    group_join_count=excluded.group_join_count,
                    group_leave_count=excluded.group_leave_count,
                    friend_add_count=excluded.friend_add_count,
                    friend_remove_count=excluded.friend_remove_count,
                    message_stats_detail=excluded.message_stats_detail,
                    user_stats_detail=excluded.user_stats_detail,
                    command_stats_detail=excluded.command_stats_detail
            """,
                (
                    date_str,
                    msg_stats['active_users'],
                    msg_stats['active_groups'],
                    msg_stats['total_messages'],
                    msg_stats['private_messages'],
                    event_stats['group_join_count'],
                    event_stats['group_leave_count'],
                    event_stats['friend_add_count'],
                    event_stats['friend_remove_count'],
                    json.dumps(
                        {k: v for k, v in msg_stats.items() if k not in ('top_users',)},
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {'top_users': msg_stats.get('top_users', [])},
                        ensure_ascii=False,
                    ),
                    json.dumps(msg_stats.get('top_commands', []), ensure_ascii=False),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _open_ro(path):
        """只读打开 SQLite, 返回 conn 或 None"""
        if not os.path.isfile(path):
            return None
        try:
            conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=10)
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.Error:
            return None

    def _load_sync(self, appid, date_str):
        conn = self._open_ro(self._dau_db_path(appid))
        if not conn:
            return None
        try:
            row = conn.execute('SELECT * FROM log WHERE date=?', (date_str,)).fetchone()
            return self._row_to_dict(row) if row else None
        finally:
            conn.close()

    def _recent_sync(self, appid, days):
        conn = self._open_ro(self._dau_db_path(appid))
        if not conn:
            return []
        try:
            rows = conn.execute('SELECT * FROM log ORDER BY date DESC LIMIT ?', (days,)).fetchall()
            return [self._row_to_dict(r) for r in rows]
        finally:
            conn.close()

    _JSON_KEYS = ('message_stats_detail', 'user_stats_detail', 'command_stats_detail')

    @staticmethod
    def _row_to_dict(row):
        d = dict(row)
        for k in DAUService._JSON_KEYS:
            v = d.get(k)
            if not isinstance(v, str) or not v:
                continue
            with contextlib.suppress(Exception):
                d[k] = json.loads(v)
        return d
