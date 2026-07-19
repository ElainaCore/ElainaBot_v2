"""消息发送日志与失败处理 Mixin"""

import asyncio
import contextlib
import contextvars
import json

from core.base.logger import FRAMEWORK, report_error_raw
from core.message._http import _IGNORE_ERROR_CODES, MSG_TYPE_MEDIA
from core.message.response import extract_message_id, extract_reference_id
from core.message.template import tpl
from core.module.hook import get_hook_manager as _get_hooks

# 发送失败处理上下文: None=不在链路内, 否则 {'failed': bool} 记录链路内是否再失败 (切断递归)
_failure_ctx = contextvars.ContextVar('_failure_ctx', default=None)


class _SenderLogMixin:
    """发送日志记录 / 错误上报 / 失败补救 (由 MessageSender 继承)"""

    __slots__ = ()

    def _log_push(self, endpoint, payload, content, resp_data=None):
        """主动推送成功后的日志记录"""
        parts = endpoint.strip('/').split('/')
        group_id = user_id = ''
        if len(parts) >= 3:
            if parts[1] == 'groups':
                group_id = parts[2]
            elif parts[1] == 'users':
                user_id = parts[2]
        text = self._extract_log_text(payload, content)
        raw_msg = json.dumps(payload, ensure_ascii=False, default=str)
        msg_id = extract_message_id(resp_data)
        ref_id = extract_reference_id(resp_data)
        self._emit_log(
            text,
            user_id,
            group_id,
            raw_msg,
            'proactive',
            message_id=msg_id,
            reference_id=ref_id,
            context=resp_data if resp_data is not None else '',
        )

    def _extract_log_text(self, payload, content, media_label=''):
        """从 payload 提取日志显示文本"""
        md = payload.get('markdown')
        text = (md.get('content', '') if md else None) or content or payload.get('content', '') or ''
        if payload.get('msg_type') == MSG_TYPE_MEDIA:
            label = media_label or '[media]'
            text = f'{label} {text}'.rstrip() if text else label
        kb = payload.get('keyboard')
        if kb:
            try:
                rows = kb.get('content', {}).get('rows', [])
                labels = [b.get('render_data', {}).get('label', '?') for r in rows for b in r.get('buttons', [])]
                text += '\n[keyboard] ' + ' | '.join(labels)
            except Exception:
                pass
        return text

    def _emit_log(
        self,
        text,
        user_id,
        group_id,
        raw_msg,
        log_type='proactive',
        plugin_name='',
        message_id='',
        reference_id='',
        context=None,
    ):
        """推送到 Web 面板 + 持久化到数据库"""
        if self._web_log_cb:
            with contextlib.suppress(Exception):
                self._web_log_cb(
                    'message',
                    {
                        'appid': self._appid,
                        'bot_name': self._bot_name or self._appid,
                        'bot_qq': self._bot_qq or '',
                        'user_id': user_id,
                        'group_id': group_id,
                        'content': text,
                        'is_bot': True,
                        'direction': 'send',
                        'message_id': message_id,
                        'reference_id': reference_id,
                        'raw_message': raw_msg,
                        'plugin_name': plugin_name or log_type,
                    },
                )
        if self._log_service:
            with contextlib.suppress(Exception):
                asyncio.ensure_future(
                    self._log_service.add(
                        'message',
                        {
                            'message_id': message_id,
                            'reference_id': reference_id,
                            'user_id': user_id,
                            'group_id': group_id,
                            'content': text,
                            'raw_message': raw_msg,
                            'direction': 'send',
                            'plugin_name': plugin_name or log_type,
                            'context': context if context is not None else '',
                        },
                    )
                )

    async def _send_with_error_handling(self, endpoint, payload, event, content=None, *, media_label=''):
        # before_send hook (管道模式, 可修改/拦截)
        hooks = _get_hooks()
        if hooks.has('before_send'):
            hook_data = {
                'endpoint': endpoint,
                'payload': payload,
                'event': event,
                'content': content,
                'appid': self._appid,
            }
            hook_data = await hooks.pipeline('before_send', hook_data)
            if hook_data is None:
                return False, None
            payload = hook_data.get('payload', payload)
            content = hook_data.get('content', content)

        success, data = await self.post_json(endpoint, payload)
        if not success:
            code = data.get('code') if isinstance(data, dict) else None
            if code in _IGNORE_ERROR_CODES:
                return False, None
            raw_event = getattr(event, 'raw', None)
            report_error_raw(
                FRAMEWORK,
                '消息发送',
                content=json.dumps(raw_event, ensure_ascii=False, default=str) if raw_event else (getattr(event, 'content', '') or ''),
                tb=json.dumps(data, ensure_ascii=False, default=str) if data else '',
                context=json.dumps(payload, ensure_ascii=False, default=str),
                appid=self._appid,
            )
            await self._handle_send_failure(endpoint, data, event)
            return False, data

        self._log_sent(payload, event, content, media_label, data)

        # after_send hook (广播)
        if hooks.has('after_send'):
            await hooks.emit(
                'after_send',
                {
                    'success': True,
                    'data': data,
                    'payload': payload,
                    'event': event,
                    'appid': self._appid,
                },
            )
        return True, data

    async def _handle_send_failure(self, endpoint, data, event=None):
        """发送失败处理: 先回传 send_failed 钩子补救, 未处理或补救也失败时回发 api_error 模板"""
        ctx = _failure_ctx.get()
        if ctx is not None:
            ctx['failed'] = True
            return
        code = data.get('code', '') if isinstance(data, dict) else ''
        message = data.get('message', '') if isinstance(data, dict) else str(data)
        ctx = {'failed': False}
        token = _failure_ctx.set(ctx)
        try:
            hooks = _get_hooks()
            if hooks.has('send_failed'):
                handled = await hooks.pipeline('send_failed', {
                    'endpoint': endpoint, 'data': data, 'event': event,
                    'appid': self._appid, 'code': code, 'message': message,
                }) is None
                if handled and not ctx['failed']:
                    return  # 插件已处理且补救成功, 不发模板
            if tpl.get_raw('api_error', self._appid) is None:
                return
            content, buttons = tpl.render_error(
                error_code=code, error_message=message, appid=self._appid,
                user_id=getattr(event, 'user_id', '') or '',
                group_id=getattr(event, 'group_id', '') or '',
            )
            if not content:
                return
            payload = self._build_payload(event, content, buttons, None, None) if event is not None else self._build_core_payload(content, buttons, None, None)
            with contextlib.suppress(Exception):
                await self.post_json(endpoint, payload)
        finally:
            _failure_ctx.reset(token)

    def _log_sent(self, payload, event, content, media_label='', resp_data=None):
        """发送成功后的日志记录 (Web面板 + 持久化)"""
        reply_log_cb = getattr(event, '_reply_log_cb', None) or self._reply_log_cb
        plugin_name = getattr(event, '_reply_plugin_name', '') or self._reply_plugin_name
        text = self._extract_log_text(payload, content, media_label)
        user_id = getattr(event, 'user_id', '') or ''
        group_id = getattr(event, 'group_id', '') or ''
        raw_msg = json.dumps(payload, ensure_ascii=False, default=str)
        msg_id = extract_message_id(resp_data)
        ref_id = extract_reference_id(resp_data)
        if reply_log_cb:
            try:
                reply_log_cb(
                    text,
                    user_id,
                    group_id,
                    raw_msg,
                    msg_id,
                    resp_data if resp_data is not None else '',
                    reference_id=ref_id,
                )
            except TypeError:
                with contextlib.suppress(Exception):
                    reply_log_cb(text, user_id, group_id, raw_msg, msg_id, resp_data if resp_data is not None else '')
        # 无论是否有 reply_log_cb, 都推送到 Web 面板实时日志
        if reply_log_cb and self._web_log_cb:
            with contextlib.suppress(Exception):
                self._web_log_cb(
                    'message',
                    {
                        'appid': self._appid,
                        'bot_name': self._bot_name or self._appid,
                        'bot_qq': self._bot_qq or '',
                        'user_id': user_id,
                        'group_id': group_id,
                        'content': text,
                        'is_bot': True,
                        'direction': 'send',
                        'message_id': msg_id,
                        'reference_id': ref_id,
                        'raw_message': raw_msg,
                        'plugin_name': plugin_name or 'framework',
                    },
                )
        elif not reply_log_cb:
            self._emit_log(
                text,
                user_id,
                group_id,
                raw_msg,
                'template',
                plugin_name or 'framework',
                message_id=msg_id,
                reference_id=ref_id,
                context=resp_data if resp_data is not None else '',
            )
