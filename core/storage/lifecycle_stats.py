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


def compute_lifecycle_counts(rows):
    """按实体去重统计生命周期事件.

    rows: 时间升序的 (type, user_id, group_id) 序列.
    同一群/好友只看首末事件: 首末同为加入计 1 次加入, 同为移除计 1 次移除,
    先加后删(或先删后加)互相抵消, 两边都不计数.
    """
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
