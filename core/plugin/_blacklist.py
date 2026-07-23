"""黑名单/权限检查 — PluginManager 的 Mixin"""

from core.base.config import cfg


def get_blacklist_map(appid, kind):
    """读取黑名单 {ID: 理由} 映射"""
    raw = cfg.get_bot_setting(appid, f'blacklist.{kind}_list', {}) or {}
    if isinstance(raw, dict):
        return {str(k): v for k, v in raw.items()}
    return {str(x): None for x in raw}


def set_blacklist_map(appid, kind, mapping):
    """写回黑名单 {ID: 理由} 映射"""
    return cfg.set_bot_setting(appid, f'blacklist.{kind}_list', dict(sorted(mapping.items())))


def migrate_blacklist_config():
    """启动时迁移: 旧数组格式黑名单 → {ID: 理由} 映射 (理由为空)"""
    for bot in cfg.get_bot_configs() or []:
        appid = bot.get('appid')
        if not appid:
            continue
        for kind in ('user', 'group'):
            raw = cfg.get_bot_setting(appid, f'blacklist.{kind}_list')
            if isinstance(raw, list):
                cfg.set_bot_setting(appid, f'blacklist.{kind}_list', {str(x): None for x in raw})


class _BlacklistMixin:
    """黑名单与主人权限检查"""

    # ==================== 黑名单 (仅从 bot.yaml 读取, 列表非空即生效) ====================

    def _check_blacklist(self, event):
        """返回 (kind, reason) 或 None"""
        appid = event.appid or self._appid
        for kind, value in (('user', event.user_id), ('group', event.group_id)):
            if not value:
                continue
            bl = get_blacklist_map(appid, kind)
            if str(value) in bl:
                return kind, str(bl[str(value)] or '未指明原因')
        return None

    def _is_owner(self, event):
        if not event.user_id:
            return False
        bot_cfg = cfg.get_bot_config(event.appid or self._appid)
        return bool(bot_cfg) and event.user_id in (bot_cfg.get('owner_ids') or [])
