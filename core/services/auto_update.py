"""框架自动更新调度：通知和更新流程复用 FrameworkUpdater。"""

import asyncio
import json
import logging
import os
from pathlib import Path

from core.base.config import cfg
from core.message._http import MSG_TYPE_TEXT

log = logging.getLogger('ElainaBot.auto_update')
_DEFAULT_INTERVAL = 600


class AutoUpdateService:
    def __init__(self, base_dir, app):
        self._app = app
        self._task = None
        self._running = False
        self._pending = Path(base_dir) / 'data' / 'auto_update_pending.json'

    def start(self):
        if not self._task:
            self._task = asyncio.create_task(self())

    def stop(self):
        if self._task:
            self._task.cancel()
            self._task = None

    async def __call__(self):
        await self._notify_pending()
        while True:
            try:
                settings = cfg.get('settings', 'auto_update') or {}
                if not settings.get('enabled', False):
                    await asyncio.sleep(10)
                    continue
                await asyncio.sleep(self._interval(settings))
                if (cfg.get('settings', 'auto_update') or {}).get('enabled', False):
                    await self._update()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception('自动更新检查失败')
                await asyncio.sleep(10)

    @staticmethod
    def _interval(settings):
        try:
            return max(60, int(settings.get('interval_seconds', _DEFAULT_INTERVAL)))
        except (TypeError, ValueError):
            return _DEFAULT_INTERVAL

    def _owner_bot(self):
        bots = getattr(self._app, '_bots', {}) or {}
        first_id = next((str(b.get('appid')) for b in cfg.get_bot_configs() if b.get('appid') and b.get('enabled', True)), None)
        bot = bots.get(first_id) if first_id else None
        bot = bot or (next(iter(bots.values())) if bots else None)
        owner = next((str(x).strip() for x in (getattr(bot, 'owner_ids', []) or []) if str(x).strip()), '') if bot else ''
        return bot, owner

    async def _notify(self, text):
        bot, owner = self._owner_bot()
        if not bot or not owner:
            log.warning('自动更新通知跳过: 未找到第一个机器人的主人')
            return False
        try:
            await bot.sender.send_to_user(owner, text, msg_type=MSG_TYPE_TEXT, skip_suffix=True)
            return True
        except Exception:
            log.exception('自动更新通知发送失败')
            return False

    async def _update(self):
        if self._running:
            return
        from web.tools._updater.handlers import _get_updater

        updater = _get_updater()
        if updater.get_progress().get('is_updating'):
            return
        self._running = True
        try:
            check = await updater.check_for_updates()
            if check.get('error') or not check.get('has_update'):
                return
            latest = check.get('latest_version', '')
            changes = '\n'.join(
                f'- {((item.get("commit") or {}).get("message") or "").splitlines()[0]}'
                for item in (check.get('changelog') or [])[:5]
                if ((item.get('commit') or {}).get('message') or '').strip()
            ) or '暂无提交说明'
            await self._notify(f'发现框架更新：{check.get("current_version", "未知")} → {latest}\n更新内容：\n{changes}\n正在备份并更新。')
            result = await updater.update_to_version(latest, skip_backup=False, auto_restart=False)
            if not result.get('success'):
                await self._notify(f'框架自动更新失败：{result.get("message", "未知错误")}')
                return
            self._pending.parent.mkdir(parents=True, exist_ok=True)
            temp = self._pending.with_suffix('.tmp')
            temp.write_text(json.dumps({'version': latest, 'updated': result.get('updated', 0)}, ensure_ascii=False), encoding='utf-8')
            os.replace(temp, self._pending)
            await self._notify(f'框架更新完成：{latest}，更新 {result.get("updated", 0)} 个文件，即将重启。')
            updater._trigger_restart()
        finally:
            self._running = False

    async def _notify_pending(self):
        if not self._pending.is_file():
            return
        try:
            data = json.loads(self._pending.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return
        if await self._notify(f'自动重启完成，当前版本 {data.get("version", "未知")}，更新 {data.get("updated", 0)} 个文件。'):
            self._pending.unlink(missing_ok=True)
