"""消息管理 — 全局状态, 昵称缓存, bot 迭代器"""

import time

_nickname_cache = {}
_CACHE_TIMEOUT = 86400
_NICKNAME_CACHE_MAX = 5000
_nickname_last_purge = 0.0
_base_dir = ''
_bot_manager = None


def set_context(base_dir: str, bot_manager=None):
    global _base_dir, _bot_manager
    _base_dir = base_dir
    _bot_manager = bot_manager


def _purge_nickname_cache():
    global _nickname_last_purge
    now = time.time()
    if now - _nickname_last_purge < 600:
        return
    _nickname_last_purge = now
    expired = [k for k, v in _nickname_cache.items() if now - v['ts'] >= _CACHE_TIMEOUT]
    for k in expired:
        del _nickname_cache[k]
    if len(_nickname_cache) > _NICKNAME_CACHE_MAX:
        by_ts = sorted(_nickname_cache, key=lambda k: _nickname_cache[k]['ts'])
        for k in by_ts[:len(by_ts) // 2]:
            del _nickname_cache[k]


def _get_nickname(user_id):
    if not user_id:
        return "未知用户"
    _purge_nickname_cache()
    cached = _nickname_cache.get(user_id)
    if cached and time.time() - cached['ts'] < _CACHE_TIMEOUT:
        return cached['name']
    # 从 data.db 查 users.name
    if _bot_manager:
        for inst in _bot_manager._bots.values():
            try:
                r = inst.log_service.query_data(
                    "SELECT name FROM users WHERE user_id=?", (user_id,))
                if r and r[0].get('name'):
                    name = r[0]['name']
                    _nickname_cache[user_id] = {'name': name, 'ts': time.time()}
                    return name
            except Exception:
                pass
    return f"用户{user_id[-6:]}"


def _batch_get_nicknames(user_ids):
    """批量查询昵称 — 每个 bot 最多一次 SQL, 避免 N+1"""
    if not user_ids:
        return {}
    now = time.time()
    out = {}
    pending = []
    for uid in user_ids:
        if not uid:
            continue
        c = _nickname_cache.get(uid)
        if c and now - c['ts'] < _CACHE_TIMEOUT:
            out[uid] = c['name']
        else:
            pending.append(uid)
    if pending and _bot_manager:
        # SQLite 占位符限制, 分批 (1万/次 足够)
        for chunk_start in range(0, len(pending), 500):
            chunk = pending[chunk_start:chunk_start + 500]
            placeholders = ','.join('?' * len(chunk))
            sql = f"SELECT user_id, name FROM users WHERE user_id IN ({placeholders})"
            for inst in _bot_manager._bots.values():
                try:
                    rows = inst.log_service.query_data(sql, tuple(chunk))
                    for r in rows:
                        uid = r.get('user_id')
                        nm = r.get('name')
                        if uid and nm and uid not in out:
                            out[uid] = nm
                            _nickname_cache[uid] = {'name': nm, 'ts': now}
                except Exception:
                    pass
    # fallback for missing
    for uid in user_ids:
        if uid and uid not in out:
            out[uid] = f"用户{uid[-6:]}"
    return out


def _iter_bots(appid_filter=''):
    """按 appid 过滤机器人迭代器; 空字符串=全部"""
    if not _bot_manager:
        return []
    if appid_filter and appid_filter in _bot_manager._bots:
        return [(appid_filter, _bot_manager._bots[appid_filter])]
    return list(_bot_manager._bots.items())


def _get_bot(appid=''):
    """按 appid 获取单个 bot 实例, 找不到返回 None"""
    if not _bot_manager or not _bot_manager._bots:
        return None
    if appid and appid in _bot_manager._bots:
        return _bot_manager._bots[appid]
    return next(iter(_bot_manager._bots.values()))


def _get_full_access_group_ids():
    """返回所有全量群 group_id 集合"""
    if not _bot_manager:
        return set()
    try:
        rows = _bot_manager.get_full_access_groups()
        return {r['group_id'] for r in rows if r.get('group_id')}
    except Exception:
        return set()
