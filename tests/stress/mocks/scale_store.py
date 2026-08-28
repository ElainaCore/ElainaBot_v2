"""ScaleLogService — 大规模在内存中模拟真实存储层的 LogService。

与 MockLogService 不同, 本实现真正保留群成员 JSON blob 与已知用户集合,
从而让 core/bot/event.py 的真实热路径 (群成员表读改写 + 用户去重) 在压测中
体现出与生产一致的 O(群规模) 序列化/反序列化开销与缓存命中行为。

仅实现 EventHandlerMixin._on_event / _track_user / _mutate_group_user 路径
所需的最小接口, 不写真实 SQLite, 因此可在单进程中承载 20w 群 / 150w 用户。
"""

from __future__ import annotations

import time


class ScaleLogService:
    """在内存中模拟群成员/用户存储的 LogService 替身。"""

    def __init__(self, appid: str = '0000') -> None:
        self._appid = str(appid)
        # 群成员表: {group_id: users_json_str} — 真实 groups_users.users 列
        self._groups_users: dict[str, str] = {}
        # 已知用户集合: 真实 users 表主键
        self._users: set[str] = set()

        # 计数器 (用于报告)
        self.add_count = 0
        self.db_queue_count = 0
        self.group_select_count = 0
        self.group_write_count = 0
        self.user_fetch_count = 0
        self.user_insert_count = 0

    # ---- 日志写入 (异步队列, 这里仅计数) ----

    def add_sync(self, log_type, data):
        self.add_count += 1
        return True

    async def add(self, log_type, data):
        self.add_count += 1
        return True

    # ---- DB 写队列: 解析最小语义并落到内存 ----

    def db_queue(self, sql, params=()):
        self.db_queue_count += 1
        s = sql.lstrip()[:6].upper()
        if 'groups_users' in sql:
            # INSERT / UPDATE groups_users SET users=? WHERE group_id=?
            if s == 'UPDATE':
                users_json, gid = params[0], params[1]
                self._groups_users[gid] = users_json
            elif s == 'INSERT':
                gid, users_json = params[0], params[1]
                self._groups_users[gid] = users_json
            self.group_write_count += 1
        elif ' users ' in f' {sql} ' or 'INTO users' in sql:
            uid = params[0] if params else None
            if uid is not None:
                self._users.add(str(uid))
            self.user_insert_count += 1

    async def db_execute(self, sql, params=()):
        if 'INTO users' in sql and params:
            self._users.add(str(params[0]))
        return None

    async def db_fetch_one(self, sql, params=()):
        self.user_fetch_count += 1
        if 'FROM users' in sql and params:
            uid = str(params[0])
            return {'user_id': uid} if uid in self._users else None
        return None

    async def db_fetch_value(self, sql, default=0, params=()):
        if 'COUNT(*) FROM users' in sql:
            return len(self._users)
        return default

    def query_data(self, sql, params=()):
        if 'FROM groups_users' in sql and params:
            self.group_select_count += 1
            gid = params[0]
            stored = self._groups_users.get(gid)
            return [{'users': stored}] if stored is not None else []
        return []

    def query(self, log_type, sql, params=(), date=None):
        return []

    # ---- 分享 / 唤醒 桩 ----

    async def share_record(self, sharer_id, user_id, scene):
        pass

    async def wakeup_update(self, user_id):
        pass

    async def wakeup_can_send(self, user_id):
        return (False, 0, -1)

    async def wakeup_mark_sent(self, user_id, stage):
        pass

    # ---- 预热: 直接灌入稳态人口, 避免逐条事件 warm-up 过慢 ----

    def prewarm_users(self, user_ids) -> None:
        self._users.update(str(u) for u in user_ids)

    def prewarm_group(self, group_id: str, users_json: str) -> None:
        self._groups_users[group_id] = users_json

    def stats(self):
        return {
            'appid': self._appid,
            'known_users': len(self._users),
            'known_groups': len(self._groups_users),
            'add_count': self.add_count,
            'db_queue_count': self.db_queue_count,
            'group_select_count': self.group_select_count,
            'group_write_count': self.group_write_count,
            'user_fetch_count': self.user_fetch_count,
            'user_insert_count': self.user_insert_count,
        }


class _StubApp:
    """供 _DispatchMixin._get_log_service 使用的极简 app, 返回带 log_service 的 bot。"""

    def __init__(self, bots: dict) -> None:
        self._bots = bots

    def get_bot(self, appid):
        return self._bots.get(str(appid))


def estimate_group_members_json(member_count: int, prefix: str = 'u') -> str:
    """构造一个含 member_count 个成员的 groups_users.users JSON (供预热使用)。"""
    import json

    today = time.strftime('%Y-%m-%d')
    entries = [{'userid': f'{prefix}{i}', 'value': 1, 'last_active': today} for i in range(member_count)]
    return json.dumps(entries, ensure_ascii=False)
