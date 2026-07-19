"""插件机器人绑定 — PluginManager 的 Mixin"""

import asyncio
import os

from core.base.logger import FRAMEWORK, get_logger

log = get_logger(FRAMEWORK, '插件管理')


class _PluginBotsMixin:
    """插件机器人绑定的加载/保存/应用"""

    @staticmethod
    def _fire_and_forget(func, *args):
        try:
            asyncio.get_running_loop().run_in_executor(None, func, *args)
        except RuntimeError:
            func(*args)

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
