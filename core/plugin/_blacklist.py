"""黑名单/权限检查 — PluginManager 的 Mixin"""

from core.base.config import cfg


class _BlacklistMixin:
    """黑名单与主人权限检查"""

    # ==================== 黑名单 (仅从 bot.yaml 读取, 列表非空即生效) ====================

    def _check_blacklist(self, event):
        appid = event.appid or self._appid
        for kind, value in (('user', event.user_id), ('group', event.group_id)):
            if value and value in (cfg.get_bot_setting(appid, f'blacklist.{kind}_list', []) or []):
                return kind
        return None

    def _is_owner(self, event):
        if not event.user_id:
            return False
        bot_cfg = cfg.get_bot_config(event.appid or self._appid)
        return bool(bot_cfg) and event.user_id in (bot_cfg.get('owner_ids') or [])
