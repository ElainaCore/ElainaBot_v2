"""黑名单/权限/插件机器人绑定 — PluginManager 的 Mixin"""

import asyncio
import os

from core.base.config import cfg
from core.base.logger import FRAMEWORK, get_logger

log = get_logger(FRAMEWORK, '插件管理')


class _BlacklistMixin:
    """黑名单、权限检查、插件机器人绑定"""

    # ==================== 黑名单 (仅从 bot.yaml 读取) ====================

    @staticmethod
    def _fire_and_forget(func, *args):
        try:
            asyncio.get_running_loop().run_in_executor(None, func, *args)
        except RuntimeError:
            func(*args)

    def _check_blacklist(self, event):
        appid = event.appid or self._appid
        uid, gid = event.user_id or '', event.group_id or ''
        if (
            uid
            and cfg.get_bot_setting(appid, 'blacklist.user_enabled', False)
            and uid in (cfg.get_bot_setting(appid, 'blacklist.user_list', []) or [])
        ):
            return 'user'
        if (
            gid
            and cfg.get_bot_setting(appid, 'blacklist.group_enabled', False)
            and gid in (cfg.get_bot_setting(appid, 'blacklist.group_list', []) or [])
        ):
            return 'group'
        return None

    def _is_owner(self, event):
        if not event.user_id:
            return False
        bot_cfg = cfg.get_bot_config(event.appid or self._appid)
        return bool(bot_cfg) and event.user_id in (bot_cfg.get('owner_ids') or [])

    # ==================== 插件机器人绑定 ====================

    def _load_plugin_bots(self):
        import yaml

        path = os.path.join(self._base_dir, 'data', 'plugin_bots.yaml')
        if not os.path.isfile(path):
            self._plugin_bots = {}
            return
        try:
            with open(path, encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
            self._plugin_bots = {str(k): [str(v) for v in vs] if isinstance(vs, list) else [] for k, vs in data.items()}
        except Exception as e:
            log.warning(f'加载插件机器人绑定失败: {e}')
            self._plugin_bots = {}

    def _save_plugin_bots(self):
        self._fire_and_forget(self._write_plugin_bots_sync, dict(self._plugin_bots))

    def _write_plugin_bots_sync(self, data):
        import yaml

        path = os.path.join(self._base_dir, 'data', 'plugin_bots.yaml')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, 'w', encoding='utf-8') as f:
                yaml.dump(
                    data,
                    f,
                    allow_unicode=True,
                    default_flow_style=False,
                    sort_keys=False,
                )
        except Exception as e:
            log.warning(f'保存插件机器人绑定失败: {e}')

    def get_plugin_bots(self):
        return dict(self._plugin_bots)

    def set_plugin_bots(self, data):
        self._plugin_bots = {str(k): [str(v) for v in vs] if isinstance(vs, list) else [] for k, vs in data.items()}
        self._save_plugin_bots()
        self._apply_bot_bindings()

    def reload_plugin_bots(self):
        self._load_plugin_bots()
        self._apply_bot_bindings()
