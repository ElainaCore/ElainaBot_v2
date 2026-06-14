#!/usr/bin/env python
"""消息发送器 — 回复、主动推送、交互、撤回"""

import asyncio
import contextlib
import contextvars
import json
import os
import random

from core.base.config import cfg
from core.base.logger import FRAMEWORK, report_error_raw
from core.message._http import (
    _API_BASE,
    _IGNORE_ERROR_CODES,
    MSG_TYPE_ARK,
    MSG_TYPE_MARKDOWN,
    MSG_TYPE_MEDIA,
    MSG_TYPE_TEXT,
    _HttpMixin,
    log,
)
from core.message._media_send import (
    _MediaSendMixin,
    _set_msg_or_event_id,
)
from core.message.keyboard import (
    build_keyboard,
    build_prompt_keyboard,
    convert_simple_ark_data,
)
from core.message.media import get_image_size as _get_image_size
from core.message.media import upload_media_bytes, upload_media_via_url
from core.message.response import extract_message_id, extract_reference_id
from core.message.template import tpl
from core.module.hook import get_hook_manager as _get_hooks


def _msg_seq():
    return random.randint(10000, 999999)


# 发送失败处理上下文: None=不在处理链路内; 否则为 {'failed': bool},
# 记录链路内(插件补救/模板回发)是否又发送失败 —— 用于「补救也失败时仍回发模板」并切断递归。
_failure_ctx = contextvars.ContextVar('_failure_ctx', default=None)


class MessageSender(_HttpMixin, _MediaSendMixin):
    """消息发送器 (每个机器人实例一个)"""

    __slots__ = (
        '_token_mgr',
        '_appid',
        '_client',
        '_base_url',
        '_custom_api_base',
        '_web_log_cb',
        '_bot_name',
        '_bot_qq',
        '_media_dir',
        '_log_service',
        '_reply_log_cb',
        '_reply_plugin_name',
    )

    def __init__(self, token_manager, custom_api_base=''):
        self._token_mgr = token_manager
        self._appid = token_manager.appid
        self._custom_api_base = custom_api_base.rstrip('/') if custom_api_base else ''
        self._base_url = self._custom_api_base or _API_BASE
        self._client = None
        self._web_log_cb = None
        self._bot_name = ''
        self._bot_qq = ''
        self._log_service = None
        self._reply_log_cb = None
        self._reply_plugin_name = ''
        self._media_dir = ''

    def bind_instance(self, *, log_service=None, bot_name='', bot_qq='', media_dir=''):
        """由 BotInstance 调用, 注入运行时依赖"""
        if log_service is not None:
            self._log_service = log_service
        if bot_name:
            self._bot_name = bot_name
        if bot_qq:
            self._bot_qq = bot_qq
        if media_dir:
            self._media_dir = media_dir

    # ==================== 回复 ====================

    async def reply(
        self,
        event,
        content=None,
        buttons=None,
        *,
        media=None,
        msg_type=None,
        template_name=None,
        template_vars=None,
        prompt_buttons=None,
        auto_delete_time=None,
        **kwargs,
    ):
        """回复事件消息"""
        if template_name:
            use_md = cfg.get_bot_setting(self._appid, 'message.use_markdown', True)
            vars_ = {'user_id': event.user_id or '', 'group_id': event.group_id or ''}
            if template_vars:
                vars_.update(template_vars)
            content, tpl_buttons = tpl.render(template_name, use_markdown=use_md, appid=self._appid, **vars_)
            if tpl_buttons and not buttons:
                buttons = tpl_buttons

        if not content and not media:
            return None

        endpoint = event.reply_endpoint
        if not endpoint:
            log.warning(f'[{self._appid}] 无法推断回复路径: {event.event_type}')
            return None

        payload = self._build_payload(
            event,
            content,
            buttons,
            media,
            msg_type,
            prompt_buttons=prompt_buttons,
            **kwargs,
        )

        success, data = await self._send_with_error_handling(endpoint, payload, event, content)
        if success:
            self._maybe_auto_recall(event, data, auto_delete_time)
        return data

    # ==================== 媒体回复 ====================

    async def reply_image(self, event, image_data, content='', **kw):
        return await self._send_media(event, image_data, 1, content, **kw)

    async def reply_voice(self, event, voice_data, content='', **kw):
        return await self._send_media(event, voice_data, 3, content, **kw)

    async def reply_video(self, event, video_data, content='', **kw):
        return await self._send_media(event, video_data, 2, content, **kw)

    async def reply_file(
        self,
        event,
        file_data,
        content='',
        *,
        file_name=None,
        auto_delete_time=None,
        target_user_id=None,
        target_group_id=None,
    ):
        kw = dict(
            auto_delete_time=auto_delete_time,
            target_user_id=target_user_id,
            target_group_id=target_group_id,
        )
        # URL → 直接上传
        if isinstance(file_data, str) and file_data.startswith(('http://', 'https://')):
            file_info = await upload_media_via_url(self, event, file_data, 4, file_name=file_name, **kw)
            return await self._send_media_payload(event, file_info, content, **kw) if file_info else None
        # 本地路径 → 异步读取
        if isinstance(file_data, str) and await asyncio.get_running_loop().run_in_executor(None, os.path.exists, file_data):
            file_name = file_name or os.path.basename(file_data)
            _path = file_data
            file_data = await asyncio.get_running_loop().run_in_executor(None, self._read_file_sync, _path)
        return await self._send_media(event, file_data, 4, content, file_name=file_name, **kw)

    async def reply_ark(self, event, template_id, kv_data, content='', *, auto_delete_time=None):
        if isinstance(kv_data, tuple | list) and template_id in (23, 24, 37):
            kv_data = convert_simple_ark_data(template_id, kv_data)
        payload = {
            'msg_type': MSG_TYPE_ARK,
            'msg_seq': _msg_seq(),
            'content': content or '',
            'ark': {'template_id': template_id, 'kv': kv_data},
        }
        _set_msg_or_event_id(payload, event)
        endpoint = event.reply_endpoint
        if not endpoint:
            return None
        success, data = await self._send_with_error_handling(endpoint, payload, event, content)
        if success:
            self._maybe_auto_recall(event, data, auto_delete_time)
        return data

    # ==================== 主动推送 ====================

    async def send_to_group(
        self,
        group_id,
        content=None,
        *,
        msg_id=None,
        event_id=None,
        buttons=None,
        media=None,
        msg_type=None,
        skip_suffix=False,
        **kwargs,
    ):
        return await self._send_push(
            f'/v2/groups/{group_id}/messages',
            content,
            buttons,
            media,
            msg_type,
            msg_id=msg_id,
            event_id=event_id,
            skip_suffix=skip_suffix,
            **kwargs,
        )

    async def send_to_user(
        self,
        user_id,
        content=None,
        *,
        msg_id=None,
        event_id=None,
        buttons=None,
        media=None,
        msg_type=None,
        skip_suffix=False,
        **kwargs,
    ):
        return await self._send_push(
            f'/v2/users/{user_id}/messages',
            content,
            buttons,
            media,
            msg_type,
            msg_id=msg_id,
            event_id=event_id,
            skip_suffix=skip_suffix,
            **kwargs,
        )

    async def _send_push(self, endpoint, content, buttons, media, msg_type, **kwargs):
        skip_suffix = kwargs.pop('skip_suffix', False)
        payload = self._build_core_payload(
            content,
            buttons,
            media,
            msg_type,
            skip_suffix=skip_suffix,
            **kwargs,
        )
        ok, data = await self.post_json(endpoint, payload)
        if ok:
            self._log_push(endpoint, payload, content, data)
        else:
            err_code = data.get('code', '') if isinstance(data, dict) else ''
            err_msg = data.get('message', str(data)) if isinstance(data, dict) else str(data)
            report_error_raw(
                FRAMEWORK,
                '主动消息',
                content=f'主动消息发送失败 [{err_code}] {err_msg}',
                tb=f'endpoint: {endpoint}\npayload: {json.dumps(payload, ensure_ascii=False, default=str)[:500]}',
                appid=self._appid,
            )
            await self._handle_send_failure(endpoint, data)
        return ok, data, payload

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

    async def send_to_channel(self, channel_id, content=None, *, msg_id=None, buttons=None, **kwargs):
        endpoint = f'/channels/{channel_id}/messages'
        payload = {'content': content or ''}
        if msg_id:
            payload['msg_id'] = msg_id
        if buttons:
            payload['keyboard'] = build_keyboard(buttons, self._appid)
        payload.update(kwargs)
        return await self.post_json(endpoint, payload)

    async def send_image(self, target_type, target_id, image_data, content='', *, msg_id=None):
        """主动推送图片 (target_type: 'group' 或 'user')"""
        prefix = 'groups' if target_type == 'group' else 'users'
        file_info = await upload_media_bytes(self, image_data, 1, f'/v2/{prefix}/{target_id}/files')
        if not file_info:
            return False, {'message': '图片上传失败'}
        payload = {
            'msg_type': MSG_TYPE_MEDIA,
            'msg_seq': _msg_seq(),
            'content': content,
            'media': {'file_info': file_info},
        }
        if msg_id:
            payload['msg_id'] = msg_id
        return await self.post_json(f'/v2/{prefix}/{target_id}/messages', payload)

    # ==================== 唤醒消息 ====================

    async def send_wakeup(self, user_id, content='', buttons=None):
        """发送唤醒消息, 返回 (success, reason)"""
        if not self._log_service:
            return (False, 'log_service 未初始化')
        can_send, stage, days = await self._log_service.wakeup_can_send(user_id)
        if not can_send:
            if days == -1:
                return (False, '用户未在召回表中(从未发过消息)')
            if days > 30:
                return (False, f'超过30天({days}天)无法召回')
            return (False, f'今日已推送过该周期(周期{stage})')
        ok, result = await self._do_wakeup(user_id, content, buttons)
        if ok and self._log_service:
            await self._log_service.wakeup_mark_sent(user_id, stage)
        return (ok, result)

    async def force_wakeup(self, user_id, content='', buttons=None):
        """强制发送唤醒消息 (不检查条件)"""
        return await self._do_wakeup(user_id, content, buttons)

    async def _do_wakeup(self, user_id, content, buttons):
        """唤醒消息发送核心"""
        try:
            payload = {
                'msg_type': 0,
                'content': content,
                'msg_seq': _msg_seq(),
                'is_wakeup': True,
            }
            if buttons:
                payload['keyboard'] = build_keyboard(buttons, self._appid)
            success, data = await self.post_json(f'/v2/users/{user_id}/messages', payload)
            if success:
                return (True, data.get('id') or data.get('msg_id', ''))
            return (False, data.get('message', '发送失败'))
        except Exception as e:
            return (False, str(e))

    # ==================== 交互 / 撤回 ====================

    async def ack_interaction(self, event, code=0, *, interaction_id=None):
        iid = interaction_id or event.message_id
        if not iid:
            return False, {'message': 'no interaction_id'}
        return await self.put(f'/interactions/{iid}', json={'code': code})

    async def recall(self, event, message_id=None):
        mid = message_id or event.message_id
        if not mid:
            return False
        template = event.recall_endpoint
        if not template:
            return False
        success, _ = await self.delete(template.format(message_id=mid))
        return success

    # ==================== 工具 ====================

    async def get_share_link(self, callback_data=None):
        if not callback_data:
            return None
        success, data = await self.post_json('/v2/generate_url_link', {'callbackData': str(callback_data)})
        if success and data.get('retcode') == 0:
            return data.get('data', {}).get('url')
        return None

    async def get_image_size(self, image_input):
        client = await self._ensure_client()
        return await _get_image_size(client, image_input)

    async def upload_media(self, event, file_bytes, file_type, *, file_name=None):
        endpoint = event.media_upload_endpoint
        if not endpoint:
            return None
        return await upload_media_bytes(self, file_bytes, file_type, endpoint, file_name=file_name)

    # ==================== 载荷构建 ====================

    def _build_payload(self, event, content, buttons, media, msg_type, *, prompt_buttons=None, **kwargs):
        payload = self._build_core_payload(content, buttons, media, msg_type, **kwargs)
        _set_msg_or_event_id(payload, event)
        if prompt_buttons:
            pk = build_prompt_keyboard(prompt_buttons)
            if pk:
                payload['prompt_keyboard'] = pk
        return payload

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

    def _build_core_payload(self, content, buttons, media, msg_type, **kwargs):
        """统一载荷构建 (回复/推送共用)"""
        skip_suffix = kwargs.pop('skip_suffix', False)
        message_reference = kwargs.pop('message_reference', None)
        message_reference_id = kwargs.pop('message_reference_id', None) or kwargs.pop('reference_message_id', None)
        use_md = cfg.get_bot_setting(self._appid, 'message.use_markdown', True)
        payload = {'msg_seq': _msg_seq()}
        for k in ('msg_id', 'event_id'):
            v = kwargs.pop(k, None)
            if v:
                payload[k] = v

        if media:
            payload['msg_type'] = MSG_TYPE_MEDIA
            payload['media'] = media
            if content:
                payload['content'] = content
        elif use_md and msg_type != MSG_TYPE_TEXT:
            payload['msg_type'] = MSG_TYPE_MARKDOWN
            md_content = str(content) if content is not None else ''
            suffix = '' if skip_suffix else cfg.get_bot_setting(self._appid, 'message.markdown_suffix', '')
            payload['markdown'] = {'content': md_content + suffix if suffix else md_content}
        else:
            payload['msg_type'] = MSG_TYPE_TEXT
            payload['content'] = content or ''

        if buttons:
            payload['keyboard'] = build_keyboard(buttons, self._appid)
        if message_reference:
            payload['message_reference'] = message_reference
        elif message_reference_id:
            payload['message_reference'] = {
                'message_id': str(message_reference_id),
                'ignore_get_message_error': True,
            }
        payload.update(kwargs)
        return payload

    # ==================== 错误处理 ====================

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
        """发送失败处理: 回传插件(send_failed 钩子)补救; 插件未处理、或补救也失败时回发 api_error 模板。

        send_failed 管道钩子返回 None 视为已处理。重入保护: 链路内(补救/模板)再失败只
        标记 failed、不递归; 据此在补救也失败时仍回发模板。模板被动用当前 msg_id, 主动直接推送。
        """
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
