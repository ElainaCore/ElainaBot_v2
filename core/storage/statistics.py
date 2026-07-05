#!/usr/bin/env python
"""统计服务 — 每日定时扫描消息日志, 幂等累加用户/群统计到 statistics.db"""

import asyncio
import contextlib
import gc
import json
import os
import sqlite3
from datetime import datetime, timedelta

from core.base.config import cfg
from core.base.logger import FRAMEWORK, get_logger
from core.storage._daily_base import DailyScanService

log = get_logger(FRAMEWORK, '统计')

# 默认每日执行时间 (凌晨 4 点)
_SCHEDULE_HOUR = 4
_SCHEDULE_MINUTE = 0

# 私聊判定: group_id 为空或 'c2c'
_PRIVATE_GIDS = ('', 'c2c')

_USER_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS user_stats (
        userid TEXT PRIMARY KEY,
        total_messages INTEGER DEFAULT 0,
        private_messages INTEGER DEFAULT 0,
        group_messages TEXT DEFAULT '{}',
        command_stats TEXT DEFAULT '{}',
        daily_messages TEXT DEFAULT '{}',
        updated_at TEXT
    )
"""

_GROUP_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS group_stats (
        groupid TEXT PRIMARY KEY,
        total_messages INTEGER DEFAULT 0,
        user_messages TEXT DEFAULT '{}',
        command_stats TEXT DEFAULT '{}',
        daily_messages TEXT DEFAULT '{}',
        updated_at TEXT
    )
"""

_PROCESSED_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS processed (
        date TEXT PRIMARY KEY,
        processed_at TEXT
    )
"""


def _extract_command(content):
    """从消息内容提取指令名: 首个空白分隔 token, 去掉前导 '/'"""
    if not content:
        return ''
    token = content.strip().split(None, 1)[0] if content.strip() else ''
    if token.startswith('/'):
        token = token[1:]
    return token


class StatisticsService(DailyScanService):
    """累加统计服务 — 异步调度 + SQLite 直读, 聚合到每个 appid 的 statistics.db"""

    __slots__ = ()

    _logger = log

    def __init__(self, log_base_dir, schedule_hour=_SCHEDULE_HOUR, schedule_minute=_SCHEDULE_MINUTE):
        super().__init__(log_base_dir, schedule_hour, schedule_minute)

    # ===== 开关 =====

    @staticmethod
    def _enabled():
        return bool(cfg.get('settings', 'statistics.enabled', True))

    # ===== 生命周期 =====

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._scheduler_loop())
        asyncio.create_task(self._startup_catchup())
        log.info(f'已启动 [每日 {self._schedule_hour:02d}:{self._schedule_minute:02d}]')

    async def _startup_catchup(self):
        """启动补算: 已过今日统计时间且尚无记录则立即补跑"""
        await asyncio.sleep(8)  # 等待其他服务就绪
        if not self._enabled():
            return
        now = datetime.now()
        today_run = now.replace(
            hour=self._schedule_hour,
            minute=self._schedule_minute,
            second=0,
            microsecond=0,
        )
        if now < today_run:
            return  # 未到今日统计时间, 交给调度器
        # 已过今日统计时间: 今日应执行的是聚合"昨日"
        yesterday = (now - timedelta(days=1)).strftime('%Y-%m-%d')
        pending = [
            appid
            for appid in self.list_appids()
            if os.path.isfile(self._message_db_path(appid, yesterday))
            and not self._is_processed(appid, yesterday)
        ]
        if not pending:
            return
        log.info(f'启动补算今日统计(聚合 {yesterday}): {len(pending)} 个机器人')
        for appid in pending:
            with contextlib.suppress(Exception):
                await self.aggregate(appid, yesterday)

    async def _run_scheduled(self):
        if self._enabled():
            await self.aggregate_yesterday()
        else:
            log.info('统计开关已关闭, 跳过本次聚合')

    # ===== 聚合 =====

    async def aggregate_yesterday(self):
        """聚合所有机器人昨日统计"""
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        return await self.aggregate_date(yesterday)

    async def aggregate_date(self, date_str):
        """聚合指定日期所有机器人统计, 返回 {appid: bool}"""
        results = {}
        appids = self.list_appids()
        log.info(f'开始统计 [{date_str}], {len(appids)} 个机器人')
        for appid in appids:
            try:
                results[appid] = await self.aggregate(appid, date_str)
            except Exception as e:
                log.warning(f'[{appid}] 统计失败: {e}')
                results[appid] = False
        log.info(f'统计完成 [{date_str}]: {sum(results.values())}/{len(results)} 成功')
        return results

    async def aggregate(self, appid, date_str):
        """聚合指定机器人 + 日期的统计 (累加到 statistics.db)"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._aggregate_sync, appid, date_str)

    # ===== 路径 / appid =====

    def _stats_db_path(self, appid):
        return os.path.join(self._log_dir, appid, 'statistics.db')

    # ===== 幂等 =====

    def _is_processed(self, appid, date_str):
        conn = self._open_ro(self._stats_db_path(appid))
        if not conn:
            return False
        try:
            row = conn.execute(
                "SELECT 1 FROM processed WHERE date=?", (date_str,)
            ).fetchone()
            return row is not None
        except sqlite3.Error:
            return False
        finally:
            conn.close()

    # ===== 核心聚合逻辑 =====

    def _aggregate_sync(self, appid, date_str):
        msg_db = self._message_db_path(appid, date_str)
        if not os.path.isfile(msg_db):
            log.debug(f'[{appid}] {date_str} 无消息日志')
            return False
        if self._is_processed(appid, date_str):
            log.debug(f'[{appid}] {date_str} 已统计, 跳过')
            return False

        day_user, day_group = self._scan_day(msg_db, date_str)
        if day_user is None:
            return False

        self._merge_into_db(appid, date_str, day_user, day_group)
        user_count, group_count = len(day_user), len(day_group)
        # 统计完成后主动释放大对象并触发 GC, 避免内存长期占用不回收
        del day_user, day_group
        gc.collect()
        log.info(f'[{appid}] {date_str} 已累加: 用户 {user_count}, 群 {group_count}')
        return True

    def _scan_day(self, db_path, date_str):
        """单遍扫描当日 receive 消息, 返回 (user_delta, group_delta) 增量"""
        conn = self._open_ro(db_path)
        if not conn:
            return None, None
        try:
            cur = conn.execute(
                "SELECT user_id, group_id, content FROM log WHERE direction='receive'"
            )
            users = {}
            groups = {}
            has_row = False
            for uid, gid, content in cur:
                has_row = True
                uid = uid or ''
                gid = gid or ''
                is_private = gid in _PRIVATE_GIDS
                cmd = _extract_command(content)

                if uid:
                    u = users.get(uid)
                    if u is None:
                        u = users[uid] = {
                            'total': 0, 'private': 0,
                            'groups': {}, 'cmds': {}, 'daily': {},
                        }
                    u['total'] += 1
                    u['daily'][date_str] = u['daily'].get(date_str, 0) + 1
                    if is_private:
                        u['private'] += 1
                    else:
                        u['groups'][gid] = u['groups'].get(gid, 0) + 1
                    if cmd:
                        u['cmds'][cmd] = u['cmds'].get(cmd, 0) + 1

                if not is_private:
                    g = groups.get(gid)
                    if g is None:
                        g = groups[gid] = {
                            'total': 0, 'users': {}, 'cmds': {}, 'daily': {},
                        }
                    g['total'] += 1
                    g['daily'][date_str] = g['daily'].get(date_str, 0) + 1
                    if uid:
                        g['users'][uid] = g['users'].get(uid, 0) + 1
                    if cmd:
                        g['cmds'][cmd] = g['cmds'].get(cmd, 0) + 1
            if not has_row:
                return {}, {}
            return users, groups
        except Exception as e:
            log.warning(f'扫描消息库失败 [{db_path}]: {e}')
            return None, None
        finally:
            conn.close()

    def _merge_into_db(self, appid, date_str, day_user, day_group):
        """将当日增量累加到 statistics.db, 并标记该日期已处理 (单事务)"""
        path = self._stats_db_path(appid)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        conn = sqlite3.connect(path, timeout=30)
        try:
            conn.execute(_USER_TABLE_SQL)
            conn.execute(_GROUP_TABLE_SQL)
            conn.execute(_PROCESSED_TABLE_SQL)
            # 再次确认幂等 (并发/重入保护)
            if conn.execute("SELECT 1 FROM processed WHERE date=?", (date_str,)).fetchone():
                return
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            self._merge_users(conn, day_user, now)
            self._merge_groups(conn, day_group, now)
            conn.execute(
                "INSERT OR REPLACE INTO processed (date, processed_at) VALUES (?,?)",
                (date_str, now),
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _add_maps(base_json, delta):
        """将 delta dict 累加进 JSON 字符串表示的 map, 返回新 JSON 字符串"""
        base = {}
        if base_json:
            with contextlib.suppress(Exception):
                base = json.loads(base_json) or {}
        for k, v in delta.items():
            base[k] = base.get(k, 0) + v
        return json.dumps(base, ensure_ascii=False)

    @staticmethod
    def _fetch_existing(conn, table, pk_col, cols, pks):
        """批量取已有行 (按主键分块 IN 查询), 返回 {pk: row_tuple}"""
        existing = {}
        col_sql = ', '.join((pk_col, *cols))
        pks = list(pks)
        for i in range(0, len(pks), 900):
            chunk = pks[i:i + 900]
            ph = ','.join('?' * len(chunk))
            for row in conn.execute(
                f"SELECT {col_sql} FROM {table} WHERE {pk_col} IN ({ph})", chunk
            ):
                existing[row[0]] = row[1:]
        return existing

    def _merge_users(self, conn, day_user, now):
        """批量累加用户统计: 一次性读已有行 + 内存合并 + executemany 写回"""
        if not day_user:
            return
        cols = ('total_messages', 'private_messages', 'group_messages', 'command_stats', 'daily_messages')
        existing = self._fetch_existing(conn, 'user_stats', 'userid', cols, day_user.keys())
        rows = []
        for uid, d in day_user.items():
            old = existing.get(uid)
            total, private, g_json, c_json, daily_json = old if old else (0, 0, '{}', '{}', '{}')
            rows.append((
                uid,
                total + d['total'],
                private + d['private'],
                self._add_maps(g_json, d['groups']),
                self._add_maps(c_json, d['cmds']),
                self._add_maps(daily_json, d['daily']),
                now,
            ))
        conn.executemany(
            """INSERT OR REPLACE INTO user_stats
               (userid, total_messages, private_messages,
                group_messages, command_stats, daily_messages, updated_at)
               VALUES (?,?,?,?,?,?,?)""",
            rows,
        )

    def _merge_groups(self, conn, day_group, now):
        """批量累加群统计: 一次性读已有行 + 内存合并 + executemany 写回"""
        if not day_group:
            return
        cols = ('total_messages', 'user_messages', 'command_stats', 'daily_messages')
        existing = self._fetch_existing(conn, 'group_stats', 'groupid', cols, day_group.keys())
        rows = []
        for gid, d in day_group.items():
            old = existing.get(gid)
            total, u_json, c_json, daily_json = old if old else (0, '{}', '{}', '{}')
            rows.append((
                gid,
                total + d['total'],
                self._add_maps(u_json, d['users']),
                self._add_maps(c_json, d['cmds']),
                self._add_maps(daily_json, d['daily']),
                now,
            ))
        conn.executemany(
            """INSERT OR REPLACE INTO group_stats
               (groupid, total_messages,
                user_messages, command_stats, daily_messages, updated_at)
               VALUES (?,?,?,?,?,?)""",
            rows,
        )

    # ===== 读取 (供 Web / 调试) =====

    @staticmethod
    def _open_ro(path):
        if not os.path.isfile(path):
            return None
        try:
            conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=10)
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.Error:
            return None

    _USER_JSON_KEYS = ('group_messages', 'command_stats', 'daily_messages')
    _GROUP_JSON_KEYS = ('user_messages', 'command_stats', 'daily_messages')

    async def get_user_stats(self, appid, userid):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._get_one_sync, appid, 'user_stats', 'userid', userid, self._USER_JSON_KEYS)

    async def get_group_stats(self, appid, groupid):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._get_one_sync, appid, 'group_stats', 'groupid', groupid, self._GROUP_JSON_KEYS)

    def _get_one_sync(self, appid, table, pk_col, pk_val, json_keys):
        conn = self._open_ro(self._stats_db_path(appid))
        if not conn:
            return None
        try:
            row = conn.execute(f"SELECT * FROM {table} WHERE {pk_col}=?", (pk_val,)).fetchone()
            if not row:
                return None
            d = dict(row)
            for k in json_keys:
                v = d.get(k)
                if isinstance(v, str) and v:
                    with contextlib.suppress(Exception):
                        d[k] = json.loads(v)
            return d
        except sqlite3.Error:
            return None
        finally:
            conn.close()
