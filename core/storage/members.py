#!/usr/bin/env python
"""群成员条目的解析与重建 — 旧 users 列 JSON 与 group_members 行表之间的桥。

旧格式 (groups_users.users):
    [{"userid": "xxx", "last_active": "2026-04-28", "member_role": "admin"}, "uid", ...]
    - 字符串元素是更早的格式, 视为"只知道 uid, 从未见其发言"
    - 字段是自由的: 成员分页同步会写入 username / joined_at / union_openid 等

行表 (group_members):
    user_id / last_active / member_role 是真列, 其余字段原样存进 extra
    is_bot 不再入库, 由 bots 表全局记录
"""

import json

# 已提升为真列或已由 bots 表承载的字段, 不再重复写进 extra
COLUMN_KEYS = frozenset({
    'userid', 'member_openid', 'openid',
    'last_active', 'member_role', 'is_bot',
})
# 历史遗留常量字段: 恒为 1 且全仓库无任何读取方, 按决定不再保留。
# 仅当出现非 1 的异常值时才落进 extra, 避免丢掉未知历史数据。
_LEGACY_CONSTANT_KEYS = {'value': 1}


def parse_member_entries(raw):
    """把 users 列的原始 JSON 解析为 {uid: entry}。

    兼容字符串元素、缺失字段与损坏 JSON; 解析失败返回空映射, 不抛异常。
    同一 uid 重复出现时后者覆盖前者, 与迁移前的解析行为一致。
    """
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode('utf-8', 'ignore')
    if not raw:
        return {}
    try:
        items = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(items, list):
        return {}
    result = {}
    for item in items:
        if isinstance(item, dict):
            uid = item.get('userid') or item.get('member_openid') or item.get('openid')
            entry = item
        else:
            uid = item
            entry = {}
        uid = str(uid or '').strip()
        if not uid:
            continue
        result[uid] = entry if isinstance(entry, dict) else {}
    return result


def member_extra_json(entry):
    """非标准字段序列化为 JSON; 常见情况没有任何额外字段, 返回空串。"""
    extra = {}
    for key, value in entry.items():
        if key in COLUMN_KEYS:
            continue
        if _LEGACY_CONSTANT_KEYS.get(key, object()) == value:
            continue
        extra[key] = value
    if not extra:
        return ''
    return json.dumps(extra, ensure_ascii=False, separators=(',', ':'))


def build_member_entry(user_id, last_active='', member_role='', extra='', is_bot=False):
    """重建公开条目形状 (get_group_record 的返回值元素)。

    与迁移前 groups_users.users 中的字典保持一致, 只是不再输出已废弃的
    常量字段 value。
    """
    entry = {'userid': user_id, 'last_active': last_active or ''}
    if member_role:
        entry['member_role'] = member_role
    if is_bot:
        entry['is_bot'] = True
    if extra:
        try:
            value = json.loads(extra)
        except (TypeError, ValueError):
            value = None
        if isinstance(value, dict):
            entry.update(value)
    return entry
