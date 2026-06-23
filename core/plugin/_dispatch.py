"""事件分发 (性能热路径) — PluginManager 的 Mixin"""

from __future__ import annotations

import asyncio
import re
import time
from typing import TYPE_CHECKING, Any, Iterator

from core.base.config import cfg
from core.base.logger import FRAMEWORK, PLUGIN, get_logger, report_error
from core.plugin.context import _make_reply_log_cb

if TYPE_CHECKING:
    from core.message.event import Event

log = get_logger(FRAMEWORK, '插件管理')

_non_at_debug_ts: dict[str, float] = {}  # {appid: last_log_time}

# ==================== 场景位掩码 ====================

_S_GROUP: int = 1
_S_DIRECT: int = 2
_S_CHANNEL: int = 4

_FULL_CHECK_TYPES: frozenset[str] = frozenset(
    {
        'GROUP_AT_MESSAGE_CREATE',
        'GROUP_MESSAGE_CREATE',
        'C2C_MESSAGE_CREATE',
        'AT_MESSAGE_CREATE',
        'DIRECT_MESSAGE_CREATE',
        'MESSAGE_CREATE',
    }
)


def _scene_mask(h: dict[str, Any]) -> int:
    return (_S_GROUP if h['group_only'] else 0) | (_S_DIRECT if h['direct_only'] else 0) | (_S_CHANNEL if h['channel_only'] else 0)


def _event_scene(event: Event) -> int:
    return (_S_GROUP if event.is_group else 0) | (_S_DIRECT if event.is_direct else 0) | (_S_CHANNEL if event.is_channel else 0)


def _strip_leading_bot_name_at(content, bot_name):
    """全量消息中移除所有 @机器人名 文本, 仅用于 handler 匹配。"""
    if not content or not bot_name:
        return content
    name = str(bot_name).strip()
    prefix = f'@{name}'
    if not name or prefix not in content:
        return content
    return content.replace(prefix, '').strip()


# ==================== Mixin ====================


class _DispatchMixin:
    """高性能事件分发"""

    _cached_app: Any = None

    # ---------- 索引构建 (由 _rebuild_handler_list 调用) ----------

    def _build_dispatch_index(self) -> None:
        """按 event_type 预分组 handler, 计算场景掩码, 构建合并列表"""
        by_et: dict[str, list[dict[str, Any]]] = {}
        any_et: list[dict[str, Any]] = []
        for h in self._all_handlers:
            h['_smask'] = _scene_mask(h)
            if h['event_types']:
                for et in h['event_types']:
                    by_et.setdefault(et, []).append(h)
            else:
                any_et.append(h)
        self._handlers_any_et = any_et
        # 预合并: 每个 event_type → 完整排序列表 (避免 dispatch 时重复归并)
        self._et_merged: dict[str, list[dict[str, Any]]] = {}
        for et, specific in by_et.items():
            self._et_merged[et] = list(_merge_by_priority(any_et, specific))

    def _handlers_for(self, et: str) -> list[dict[str, Any]]:
        """返回该事件类型的 handler 列表 (预合并, O(1) 查找)"""
        return self._et_merged.get(et, self._handlers_any_et)

    # ---------- 分发 ----------

    async def dispatch(self, event: Event, sender: Any) -> bool:
        content: str = event.content or ''
        user_id: str = event.user_id or ''
        appid: str = event.appid or self._appid
        event.appid = appid
        et: str = event.event_type
        event._sender = sender

        # ── 非消息事件快速路径: 跳过黑名单/维护/权限检查 ──
        if et not in _FULL_CHECK_TYPES:
            handlers = self._handlers_for(et)
            if not handlers:
                return False
            scene: int = _event_scene(event)
            for h in handlers:
                ab = h['_allowed_bots']
                if ab is not None and appid not in ab:
                    continue
                m: re.Match[str] | None = h['compiled'].search(content)
                if not m:
                    continue
                if h['_smask'] & ~scene:
                    continue
                plugin_name: str = h['name'] or h.get('_plugin', '')
                log_service = self._get_log_service(event)
                event._reply_log_cb = _make_reply_log_cb(plugin_name, log_service)
                event._reply_plugin_name = plugin_name or ''
                asyncio.create_task(self._run_handler(h, event, m, plugin_name, user_id, et, content))
                return True
            return False

        # ── 消息事件: 完整检查链 ──
        _get = cfg.get_bot_setting
        is_group_msg: bool = et == 'GROUP_MESSAGE_CREATE'
        is_at_self: bool = getattr(event, 'is_at_self', False)
        is_non_at: bool = is_group_msg and not is_at_self

        suppress_reply: bool = is_non_at or (is_group_msg and is_at_self and _get(appid, 'non_at_message.quiet_at_self', False))
        if not suppress_reply and getattr(event, 'is_bot', False) and _get(appid, 'message.suppress_bot_system_reply', False):
            suppress_reply = True

        # 仅@其他机器人 / 仅@其他用户 的全量消息: 标记为待跳过,
        # 但不立即 return, 让 ignore_at_check=True 的 handler 仍可匹配
        skip_at_other = (
            is_group_msg
            and not is_at_self
            and (
                (getattr(event, 'is_at_other_bot', False) and _get(appid, 'non_at_message.ignore_at_other_bot', False))
                or (getattr(event, 'is_at_other_user', False) and _get(appid, 'non_at_message.ignore_at_other_user', False))
            )
        )

        # 黑名单
        if not suppress_reply:
            bl: str | None = self._check_blacklist(event)
            if bl:
                tpl: str = 'blacklist' if bl == 'user' else 'group_blacklist'
                tvars: dict[str, str] | None = {'user_id': user_id, 'reason': '未指明原因'} if bl == 'user' else None
                asyncio.create_task(event.reply(template_name=tpl, template_vars=tvars))
                return True

        # 维护模式
        if not suppress_reply and _get(appid, 'maintenance.enabled', False) and not self._is_owner(event):
            if _get(appid, 'maintenance.reply', True):
                asyncio.create_task(event.reply(template_name='maintenance'))
            return True

        # 非AT群消息权限
        non_at_ok: bool = False
        if is_non_at:
            if _get(appid, 'non_at_message.enabled', False):
                non_at_ok = True
            else:
                gid: str = event.group_id or ''
                wl: list[str] = _get(appid, 'non_at_message.group_whitelist', []) or []
                non_at_ok = bool(gid and gid in wl)
                if not non_at_ok and time.time() - _non_at_debug_ts.get(appid, 0) >= 60:
                    _non_at_debug_ts[appid] = time.time()
                    log.debug(f'[{appid}] 收到全量群消息但 non_at_message 未启用 (group={gid})')

        # 拦截器
        for ic in self._all_interceptors:
            try:
                r: bool | None = await ic['func'](event) if ic['is_coro'] else await asyncio.get_running_loop().run_in_executor(None, ic['func'], event)
                if r is True:
                    return True
            except Exception as e:
                report_error(PLUGIN, ic.get('_plugin', '?'), e)

        # 处理器匹配
        scene: int = _event_scene(event)
        handlers = self._handlers_for(et)
        handler_content = content
        if is_group_msg and _get(appid, 'non_at_message.strip_bot_name_at', False):
            handler_content = _strip_leading_bot_name_at(content, getattr(sender, '_bot_name', '') or '')

        if self._match_handlers(handlers, handler_content, event, appid, is_non_at, non_at_ok, scene, user_id, et, content, skip_at_other):
            return True
        alt = handler_content[1:] if handler_content[:1] == '/' else '/' + handler_content
        if self._match_handlers(handlers, alt, event, appid, is_non_at, non_at_ok, scene, user_id, et, content, skip_at_other):
            return True

        # 无匹配 → 默认回复
        should_default: bool = not suppress_reply and (et in ('GROUP_AT_MESSAGE_CREATE', 'C2C_MESSAGE_CREATE') or (is_group_msg and is_at_self))
        if should_default and _get(appid, 'message.send_default_response', True):
            excluded: list[str] = _get(appid, 'message.default_response_excluded_regex', []) or []
            if not any(re.search(p, content) for p in excluded if p):
                asyncio.create_task(event.reply(template_name='default', template_vars={'user_id': user_id}))
        return False

    def _match_handlers(
        self,
        handlers: list[dict[str, Any]],
        try_content: str,
        event: Event,
        appid: str,
        is_non_at: bool,
        non_at_ok: bool,
        scene: int,
        user_id: str,
        et: str,
        content: str,
        skip_at_other: bool = False,
    ) -> bool:
        """内循环: 遍历 handler 尝试匹配, 匹配成功则 fire-and-forget 并返回 True"""
        for h in handlers:
            # 快速过滤: bot 白名单
            ab = h['_allowed_bots']
            if ab is not None and appid not in ab:
                continue
            # 仅@其他机器人/用户的消息: 没有 ignore_at_check 的 handler 跳过
            if skip_at_other and not h.get('ignore_at_check', False):
                continue
            # 非AT群消息过滤
            if is_non_at and not h.get('ignore_at_check', False) and not non_at_ok:
                continue
            # 正则匹配
            m: re.Match[str] | None = h['compiled'].search(try_content)
            if not m:
                continue
            # 场景过滤 (位掩码): handler 要求的场景位 & 事件不具备的场景位 → 不匹配
            if h['_smask'] & ~scene:
                # 群聊专属指令在私聊环境 → 明确告知用户
                if h['group_only'] and not is_non_at:
                    asyncio.create_task(
                        event.reply(
                            template_name='group_only',
                            template_vars={'user_id': user_id},
                        )
                    )
                    return True
                continue
            # 权限
            if h['owner_only'] and not self._is_owner(event):
                if not is_non_at:
                    asyncio.create_task(
                        event.reply(
                            template_name='owner_only',
                            template_vars={'user_id': user_id},
                        )
                    )
                return True
            # 日志绑定
            plugin_name: str = h['name'] or h.get('_plugin', '')
            log_service = self._get_log_service(event)
            event._reply_log_cb = _make_reply_log_cb(plugin_name, log_service)
            event._reply_plugin_name = plugin_name or ''
            asyncio.create_task(self._run_handler(h, event, m, plugin_name, user_id, et, content))
            return True
        return False

    async def _run_handler(
        self,
        h: dict[str, Any],
        event: Event,
        match: re.Match[str],
        plugin_name: str,
        user_id: str,
        et: str,
        content: str,
    ) -> None:
        t0: float = time.time()
        try:
            fn = h['func']
            coro = fn(event, match) if h['is_coro'] else asyncio.get_running_loop().run_in_executor(None, fn, event, match)
            await asyncio.wait_for(coro, timeout=300)
        except TimeoutError:
            report_error(
                PLUGIN,
                plugin_name or '?',
                f'处理器 [{h["name"]}] 超时(300s)',
                context={
                    'handler': h['name'],
                    'user_id': user_id,
                    'event_type': et,
                    'content': content[:200],
                },
            )
        except Exception as e:
            report_error(
                PLUGIN,
                plugin_name or '?',
                e,
                context={
                    'handler': h['name'],
                    'user_id': user_id,
                    'group_id': event.group_id or '',
                    'event_type': et,
                    'content': content[:200],
                },
            )
        finally:
            dt: float = time.time() - t0
            if dt > 3:
                log.warning(f'[性能] 处理器 [{plugin_name}] 耗时 {dt * 1000:.0f}ms content={content[:50]}')
            event.raw = event._reply_log_cb = None

    # ---------- 日志服务 ----------

    def _get_log_service(self, event: Event) -> Any:
        app = _DispatchMixin._cached_app
        if app is None:
            try:
                from core.application import get_app

                app = get_app()
            except Exception:
                return None
            if app is None:
                return None
            _DispatchMixin._cached_app = app
        bot = app.get_bot(event.appid)
        return bot.log_service if bot else None


def _merge_by_priority(
    a: list[dict[str, Any]],
    b: list[dict[str, Any]],
) -> Iterator[dict[str, Any]]:
    """归并两个按 priority 降序的列表, 生成器, 零分配"""
    ia: int = 0
    ib: int = 0
    la: int = len(a)
    lb: int = len(b)
    while ia < la and ib < lb:
        if a[ia]['priority'] >= b[ib]['priority']:
            yield a[ia]
            ia += 1
        else:
            yield b[ib]
            ib += 1
    while ia < la:
        yield a[ia]
        ia += 1
    while ib < lb:
        yield b[ib]
        ib += 1
