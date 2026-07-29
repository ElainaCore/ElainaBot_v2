#!/usr/bin/env python
"""生命周期事件净变化统计 — 同一群/好友当日重复事件去重"""

_EVENT_KIND = {
    'group_add': ('group', 'add'),
    'group_del': ('group', 'del'),
    'friend_add': ('friend', 'add'),
    'friend_del': ('friend', 'del'),
}

_COUNT_KEY = {
    ('group', 'add'): 'group_join_count',
    ('group', 'del'): 'group_leave_count',
    ('friend', 'add'): 'friend_add_count',
    ('friend', 'del'): 'friend_remove_count',
}

# SQL 下推版: 单次扫描内完成按实体去重的净变化统计, 不把整表搬回 Python。
# tag = 零填充 id + 动作, MIN/MAX(tag) 即该实体的首/末动作 (语义同 compute_lifecycle_counts)
LIFECYCLE_COUNTS_SQL = """
    WITH e AS MATERIALIZED (
        SELECT CASE WHEN type IN ('group_add','group_del') THEN 'group' ELSE 'friend' END AS cat,
               CASE WHEN type IN ('group_add','group_del') THEN group_id ELSE user_id END AS ent,
               printf('%012d', id) || CASE WHEN type IN ('group_add','friend_add')
                                           THEN 'add' ELSE 'del' END AS tag
        FROM log
        WHERE type IN ('group_add','group_del','friend_add','friend_del')
    ),
    g AS (
        SELECT cat, SUBSTR(MIN(tag), 13) AS fa, SUBSTR(MAX(tag), 13) AS la
        FROM e GROUP BY cat, ent
    )
    SELECT cat, fa AS act, COUNT(*) AS c FROM g WHERE fa = la GROUP BY cat, fa
"""


def lifecycle_counts_from_rows(rows):
    """将 LIFECYCLE_COUNTS_SQL 的结果行 [{cat, act, c}] 转为计数 dict"""
    counts = {
        'group_join_count': 0,
        'group_leave_count': 0,
        'friend_add_count': 0,
        'friend_remove_count': 0,
    }
    for r in rows or []:
        key = _COUNT_KEY.get((r.get('cat', ''), r.get('act', '')))
        if key:
            counts[key] = r.get('c', 0) or 0
    return counts


def compute_lifecycle_counts(rows):
    """按实体去重统计生命周期事件: rows 为时间升序 (type,user_id,group_id), 同一群/好友只看首末事件, 首末同为加入/移除各计 1 次, 先加后删或先删后加互相抵消不计数"""
    first = {}
    last = {}
    for etype, uid, gid in rows:
        kind = _EVENT_KIND.get(etype)
        if not kind:
            continue
        cat, action = kind
        key = (cat, gid if cat == 'group' else uid)
        first.setdefault(key, action)
        last[key] = action
    counts = {
        'group_join_count': 0,
        'group_leave_count': 0,
        'friend_add_count': 0,
        'friend_remove_count': 0,
    }
    for key, first_action in first.items():
        if first_action == last[key]:
            counts[_COUNT_KEY[(key[0], first_action)]] += 1
    return counts
