#!/usr/bin/env python
"""BotManager — 委托给 Application 的适配层"""

from core.application import Application

_bot_manager_ref = None


class BotManager:
    """委托给 Application 的 BotManager"""

    def __init__(self):
        self._app = Application()
        self._base_dir = self._app._base_dir

    # ----- 属性 -----

    @property
    def dau_service(self):
        return self._app.dau_service

    @property
    def module_manager(self):
        return self._app.module_manager

    @property
    def plugin_manager(self):
        return self._app.plugin_manager

    @property
    def _bots(self):
        return self._app._bots

    def get_bot(self, appid):
        return self._app.get_bot(appid)

    @property
    def bot_registry(self):
        return self._app.bot_registry

    # ----- Web 面板日志回调 -----

    @property
    def _web_log_cb(self):
        return self._app._web_log_cb

    @_web_log_cb.setter
    def _web_log_cb(self, cb):
        self._app._web_log_cb = cb

    # ----- 启动 / 关闭 -----

    async def start(self):
        global _bot_manager_ref
        _bot_manager_ref = self
        return await self._app.start()

    async def shutdown(self):
        return await self._app.shutdown()

    # ----- HTTP -----

    async def _handle_webhook(self, request):
        return await self._app._handle_webhook(request)

    async def _handle_health(self, request):
        return await self._app._handle_health(request)

    # ----- Web 日志推送 -----

    def _push_web_log(self, log_type: str, entry: dict):
        self._app._push_web_log(log_type, entry)
