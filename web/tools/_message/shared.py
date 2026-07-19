"""消息管理 — 全局状态, 昵称缓存"""

import time
from typing import Any

_nickname_cache: dict[str, tuple[float, str]] = {}
_CACHE_TIMEOUT = 86400
_base_dir: str = ''
_bot_manager: Any = None


def set_context(base_dir: str, bot_manager=None):
    global _base_dir, _bot_manager
    _base_dir = base_dir
    _bot_manager = bot_manager


def _get_nickname(user_id):
    if not user_id:
        return '未知用户'
    cached = _nickname_cache.get(user_id)
    if cached and time.time() - cached['ts'] < _CACHE_TIMEOUT:
        return cached['name']
    # 从 data.db 查 users.name
    if _bot_manager:
        for inst in _bot_manager._bots.values():
            try:
                r = inst.log_service.query_data('SELECT name FROM users WHERE user_id=?', (user_id,))
                if r and r[0].get('name'):
                    name = r[0]['name']
                    _nickname_cache[user_id] = {'name': name, 'ts': time.time()}
                    return name
            except Exception:
                pass
    return f'用户{user_id[-6:]}'


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
        # SQLite 占位符限制 999, 分批查询
        for chunk_start in range(0, len(pending), 900):
            chunk = pending[chunk_start : chunk_start + 900]
            placeholders = ','.join('?' * len(chunk))
            sql = f'SELECT user_id, name FROM users WHERE user_id IN ({placeholders})'
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
            out[uid] = f'用户{uid[-6:]}'
    return out
def _get_bot(appid=''):
    """按 appid 获取单个 bot 实例, 找不到返回 None"""
    if not _bot_manager or not _bot_manager._bots:
        return None
    if appid and appid in _bot_manager._bots:
        return _bot_manager._bots[appid]
    return next(iter(_bot_manager._bots.values()))


_fa_cache: set | None = None
_fa_cache_ts: float = 0
_FA_CACHE_TTL = 120


def _get_full_access_group_ids():
    """返回所有全量群 group_id 集合 (带 120s 内存缓存)"""
    global _fa_cache, _fa_cache_ts
    now = time.time()
    if _fa_cache is not None and now - _fa_cache_ts < _FA_CACHE_TTL:
        return _fa_cache
    if not _bot_manager:
        return set()
    try:
        rows = _bot_manager.get_full_access_groups()
        _fa_cache = {r['group_id'] for r in rows if r.get('group_id')}
    except Exception:
        _fa_cache = set()
    _fa_cache_ts = now
    return _fa_cache
