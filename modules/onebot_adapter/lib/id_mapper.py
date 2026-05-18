"""openid ↔ 10 位 QQ 号映射器

使用 SQLite 持久化存储, 为每个 openid (用户/群) 生成唯一的 10 位数字 QQ 号。
已有映射直接复用, 保证幂等。
"""

from __future__ import annotations

import os
import random
import sqlite3

_QQ_MIN = 1_000_000_000
_QQ_MAX = 9_999_999_999

_DDL = """
CREATE TABLE IF NOT EXISTS id_map (
    openid  TEXT    NOT NULL,
    qq_id   INTEGER NOT NULL,
    id_type TEXT    NOT NULL DEFAULT 'user',
    PRIMARY KEY (openid, id_type)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_qq_id ON id_map (qq_id);
"""


class IDMapper:
    """异步 openid ↔ qq_id 双向映射"""

    __slots__ = ('_db_path', '_db', '_cache_fwd', '_cache_rev')

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._db: sqlite3.Connection | None = None
        self._cache_fwd: dict[tuple[str, str], int] = {}  # (openid, type) -> qq_id
        self._cache_rev: dict[int, tuple[str, str]] = {}  # qq_id -> (openid, type)

    async def open(self):
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._db = sqlite3.connect(self._db_path)
        self._db.executescript(_DDL)
        self._db.commit()
        # 预热缓存
        for openid, qq_id, id_type in self._db.execute('SELECT openid, qq_id, id_type FROM id_map').fetchall():
            self._cache_fwd[(openid, id_type)] = qq_id
            self._cache_rev[qq_id] = (openid, id_type)

    async def close(self):
        if self._db:
            self._db.close()
            self._db = None

    async def to_qq(self, openid: str, id_type: str = 'user') -> int:
        """openid → qq_id (不存在则自动生成)"""
        if not openid:
            return 0

        key = (openid, id_type)
        cached = self._cache_fwd.get(key)
        if cached is not None:
            return cached

        # 查库
        row = self._db.execute('SELECT qq_id FROM id_map WHERE openid=? AND id_type=?', (openid, id_type)).fetchone()
        if row:
            qq_id = row[0]
            self._cache_fwd[key] = qq_id
            self._cache_rev[qq_id] = key
            return qq_id

        # 生成新的不冲突的 10 位 QQ 号
        for _ in range(100):
            qq_id = random.randint(_QQ_MIN, _QQ_MAX)
            if qq_id not in self._cache_rev:
                try:
                    self._db.execute(
                        'INSERT INTO id_map (openid, qq_id, id_type) VALUES (?, ?, ?)',
                        (openid, qq_id, id_type),
                    )
                    self._db.commit()
                    self._cache_fwd[key] = qq_id
                    self._cache_rev[qq_id] = key
                    return qq_id
                except sqlite3.IntegrityError:
                    continue
        raise RuntimeError(f'无法为 {openid} ({id_type}) 分配 QQ 号')

    async def to_openid(self, qq_id: int) -> tuple[str, str] | None:
        """qq_id → (openid, id_type), 不存在返回 None"""
        cached = self._cache_rev.get(qq_id)
        if cached is not None:
            return cached

        row = self._db.execute('SELECT openid, id_type FROM id_map WHERE qq_id=?', (qq_id,)).fetchone()
        if row:
            openid, id_type = row
            key = (openid, id_type)
            self._cache_fwd[key] = qq_id
            self._cache_rev[qq_id] = key
            return (openid, id_type)
        return None

    async def to_openid_by_type(self, qq_id: int, id_type: str) -> str | None:
        """qq_id + type → openid"""
        result = await self.to_openid(qq_id)
        if result and result[1] == id_type:
            return result[0]
        # 类型不匹配时尝试精确查询
        row = self._db.execute('SELECT openid FROM id_map WHERE qq_id=? AND id_type=?', (qq_id, id_type)).fetchone()
        return row[0] if row else None
