#!/usr/bin/env python
"""消息发送器 — 回复、主动推送、交互、撤回"""

import asyncio
import json
import os
import re

from core.base.config import cfg
from core.base.logger import FRAMEWORK, report_error_raw
from core.message import bot_openid
from core.message._http import (
    MSG_TYPE_ARK,
    MSG_TYPE_CARD,
    MSG_TYPE_MARKDOWN,
    MSG_TYPE_MEDIA,
    MSG_TYPE_TEXT,
    _HttpMixin,
    _is_violation,
    _msg_seq,
    log,
)
from core.message._media_send import (
    _MediaSendMixin,
    _set_msg_or_event_id,
)
from core.message._sender_log import _SenderLogMixin
from core.message.keyboard import (
    build_keyboard,
    build_prompt_keyboard,
    convert_simple_ark_data,
)
from core.message.media import get_image_size as _get_image_size
from core.message.media import upload_media_bytes, upload_media_via_url
from core.message.template import tpl

_ESCAPE_MAP = {'n': '\n', 't': '\t', 'r': '\r', '\\': '\\', '0': '\0', 'a': '\a', 'b': '\b', 'f': '\f', 'v': '\v'}
_STREAM_CONTENT_TYPES = frozenset({'text', 'markdown'})
_STREAM_INPUT_MODES = frozenset({'append', 'replace'})
_PANEL_SCOPES = frozenset({'c2c', 'group', 'channel', 'dm'})
_PANEL_TARGET_TYPES = frozenset({'all', 'specific'})


def _unescape(text):
    """还原字符串中的字面转义序列 (\\n \\t 等), 未识别的序列原样保留"""
    return re.sub(
        r'\\(.)',
        lambda m: _ESCAPE_MAP.get(m.group(1), m.group(0)),
        text)


def _group_error_state(error):
    if not isinstance(error, dict):
        return ''
    if any(str(error.get(key, '')) == '11255' for key in ('code', 'err_code')):
        return 'removed'
    if str(error.get('err_code', '')) == '40011026':
        return 'left_group'
    return ''


async def _iter_stream_chunks(chunks):
    if isinstance(chunks, str):
        yield chunks
    elif hasattr(chunks, '__aiter__'):
        async for item in chunks:
            yield item
    else:
        for item in chunks:
            yield item


def _parse_stream_chunk(item):
    """Return (is_replacement, text), or None for unsupported LLM events."""
    if not isinstance(item, dict):
        text = str(item or '')
        return (False, text) if text else None
    kind = item.get('type')
    if kind not in (None, 'delta', 'text', 'replace'):
        return None
    text = str(item.get('text', item.get('content', '')) or '')
    return kind == 'replace', text


class MessageSender(_HttpMixin, _MediaSendMixin, _SenderLogMixin):
    """消息发送器 (每个机器人实例一个)"""

    __slots__ = (
        '_token_mgr',
        '_appid',
        '_client',
        '_web_log_cb',
        '_bot_name',
        '_bot_qq',
        '_media_dir',
        '_log_service',
        '_group_member_sync_locks',
        '_reply_log_cb',
        '_reply_plugin_name',
    )

    def __init__(self, token_manager):
        self._token_mgr = token_manager
        self._appid = token_manager.appid
        self._client = None
        self._web_log_cb = None
        self._bot_name = ''
        self._bot_qq = ''
        self._log_service = None
        self._group_member_sync_locks = {}
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
        return await self._reply_send(endpoint, event, payload, content, auto_delete_time)

    async def reply_stream(
        self,
        event,
        chunks,
        *,
        content_type=None,
        input_mode='replace',
        msg_id=None,
        event_id=None,
        msg_seq=None,
        min_interval=0.25,
    ):
        """回复 QQ 单聊事件，并发送纯文本或 Markdown 流式消息。"""
        if getattr(event, 'event_type', '') != 'C2C_MESSAGE_CREATE':
            raise ValueError('流式消息仅支持 C2C_MESSAGE_CREATE 事件')
        user_id = getattr(event, 'raw_user_id', None) or getattr(event, 'user_id', None)
        if not user_id:
            raise ValueError('私聊流式消息缺少 user_openid')
        return await self._send_stream_to_user(
            user_id,
            chunks,
            event=event,
            content_type=content_type,
            input_mode=input_mode,
            msg_id=msg_id,
            event_id=event_id,
            msg_seq=msg_seq,
            min_interval=min_interval,
        )

    async def send_stream_to_user(
        self,
        user_id,
        chunks,
        *,
        content_type=None,
        input_mode='replace',
        msg_id=None,
        event_id=None,
        msg_seq=None,
        is_wakeup=False,
        min_interval=0.25,
    ):
        """主动发送私聊流式消息，返回最后一个分片的响应。"""
        return await self._send_stream_to_user(
            user_id,
            chunks,
            content_type=content_type,
            input_mode=input_mode,
            msg_id=msg_id,
            event_id=event_id,
            msg_seq=msg_seq,
            is_wakeup=is_wakeup,
            min_interval=min_interval,
        )

    async def _send_stream_to_user(
        self,
        user_id,
        chunks,
        *,
        event=None,
        content_type=None,
        input_mode='replace',
        msg_id=None,
        event_id=None,
        msg_seq=None,
        is_wakeup=False,
        min_interval=0.25,
    ):
        """QQ 单聊流式消息公共实现。"""
        user_id = str(user_id or '').strip()
        if not user_id:
            raise ValueError('私聊流式消息缺少 user_openid')
        input_mode = str(input_mode or '').strip().lower()
        if input_mode not in _STREAM_INPUT_MODES:
            raise ValueError('input_mode 只能为 append 或 replace')
        endpoint = f'/v2/users/{user_id}/stream_messages'
        content_type = str(content_type or (
            'markdown' if cfg.get_bot_setting(self._appid, 'message.use_markdown', True)
            else 'text'
        )).strip().lower()
        if content_type not in _STREAM_CONTENT_TYPES:
            raise ValueError('content_type 只能为 text 或 markdown')
        try:
            min_interval = max(0.0, float(min_interval))
        except (TypeError, ValueError) as exc:
            raise ValueError('min_interval 必须为非负数字') from exc
        try:
            sequence = _msg_seq() if msg_seq is None else int(msg_seq)
        except (TypeError, ValueError) as exc:
            raise ValueError('msg_seq 必须为整数') from exc

        async def _send(content, state, index, stream_msg_id=''):
            payload = {
                'input_mode': input_mode,
                'input_state': state,
                'index': index,
                'content_type': content_type,
                'content_raw': content,
                'msg_seq': sequence,
            }
            if stream_msg_id:
                payload['stream_msg_id'] = stream_msg_id
            if msg_id:
                payload['msg_id'] = str(msg_id)
            elif event_id:
                payload['event_id'] = str(event_id)
            elif event is not None:
                _set_msg_or_event_id(payload, event)
            if is_wakeup:
                payload['is_wakeup'] = True
            ok, data = await self.post_json(endpoint, payload)
            if not ok:
                self._report_send_error(
                    f'私聊流式消息发送失败: {endpoint}', data, payload,
                )
                raise RuntimeError(
                    data.get('message', '私聊流式消息发送失败')
                    if isinstance(data, dict) else str(data)
                )
            return payload, data

        full_text = pending = sent_text = stream_msg_id = ''
        index = 0
        last_sent_at = 0.0
        loop = asyncio.get_running_loop()

        async for item in _iter_stream_chunks(chunks):
            chunk = _parse_stream_chunk(item)
            if chunk is None:
                continue
            is_replacement, text = chunk
            if is_replacement:
                if not text.startswith(sent_text):
                    raise ValueError('不能修改已经发送的正文前缀')
                full_text = text
                pending = text[len(sent_text):] if input_mode == 'append' else text
            else:
                if not text:
                    continue
                full_text += text
                pending += text

            content = full_text if input_mode == 'replace' else pending
            if not content or full_text == sent_text:
                continue
            now = loop.time()
            if index and now - last_sent_at < min_interval:
                continue
            _, data = await _send(content, 1, index, stream_msg_id)
            stream_msg_id = str((data or {}).get('id') or stream_msg_id)
            sent_text = full_text
            pending = ''
            index += 1
            last_sent_at = loop.time()

        if not full_text and not index:
            return None
        content = full_text if input_mode == 'replace' else pending
        last_payload, last_data = await _send(content, 10, index, stream_msg_id)
        if event is not None:
            self._log_sent(last_payload, event, full_text, resp_data=last_data)
        else:
            self._log_push(endpoint, last_payload, full_text, last_data)
        return last_data

    async def _reply_send(self, endpoint, event, payload, content, auto_delete_time):
        """回复发送公共尾部: 发送 + 成功后自动撤回 (reply/reply_ark/reply_card 共用)"""
        success, data = await self._send_with_error_handling(endpoint, payload, event, content)
        if success:
            self._maybe_auto_recall(event, data, auto_delete_time)
        return data

    # ==================== 媒体回复 ====================

    async def reply_image(self, event, image_data, content='', **kw):
        return await self._send_media(event, image_data, 1, content, **kw)

    async def reply_voice(self, event, voice_data, content='', **kw):
        if (
            isinstance(voice_data, str)
            and not voice_data.startswith(('http://', 'https://'))
            and await asyncio.get_running_loop().run_in_executor(None, os.path.isfile, voice_data)
        ):
            voice_data = await asyncio.get_running_loop().run_in_executor(None, self._read_file_sync, voice_data)
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
        return await self._reply_send(endpoint, event, payload, content, auto_delete_time)

    async def reply_card(self, event, card_type='tuwen', data=None, content='', *, auto_delete_time=None):
        """回复卡片消息 (msg_type=8); data 为 dict 时原样作为 card.content, card_type='tuwen' 时也可传 (标题,描述,图片URL,跳转URL) 元组简写"""
        if isinstance(data, tuple | list) and card_type == 'tuwen':
            title, description, pic_url, url = (list(data) + [''] * 4)[:4]
            data = {'title': title or '', 'description': description or '',
                    'pic_url': pic_url or '', 'url': url or ''}
        payload = {
            'msg_type': MSG_TYPE_CARD,
            'msg_seq': _msg_seq(),
            'content': content or '',
            'card': {'type': card_type, 'content': data or {}},
        }
        _set_msg_or_event_id(payload, event)
        endpoint = event.reply_endpoint
        if not endpoint:
            return None
        return await self._reply_send(endpoint, event, payload, content, auto_delete_time)

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
            # 违规拦截: 先给插件机会重发其他内容, 补救成功则不写报错日志
            remedied = await self._handle_send_failure(endpoint, data)
            if not (_is_violation(data) and remedied):
                err_code = data.get('code', '') if isinstance(data, dict) else ''
                err_msg = data.get('message', str(data)) if isinstance(data, dict) else str(data)
                report_error_raw(
                    FRAMEWORK,
                    '主动消息',
                    content=f'主动消息发送失败 [{err_code}] {err_msg}',
                    tb=f'endpoint: {endpoint}\npayload: {json.dumps(payload, ensure_ascii=False, default=str)[:500]}',
                    appid=self._appid,
                )
        return ok, data, payload

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
        iid = interaction_id or (event.message_id if event else '')
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

    # ==================== 自定义菜单 / 指令面板 ====================

    @staticmethod
    def _api_error(message):
        return {'message': message, 'code': -1}

    @classmethod
    def _normalize_panel_choice(cls, value, choices, name):
        value = str(value or '').strip().lower()
        if value not in choices:
            return '', cls._api_error(
                f'{name} 只能为 {"、".join(sorted(choices))}'
            )
        return value, None

    @classmethod
    def _panel_targets(
        cls,
        *,
        user_openids=None,
        group_openids=None,
        scope='',
    ):
        if user_openids is not None and group_openids is not None:
            return None, cls._api_error('user_openids 和 group_openids 不能同时提供')
        if user_openids is None and group_openids is None:
            return {}, None

        key, values = (
            ('user_openids', user_openids)
            if user_openids is not None
            else ('group_openids', group_openids)
        )
        if not isinstance(values, (list, tuple)):
            return None, cls._api_error(f'{key} 必须为列表')
        if len(values) > 20:
            return None, cls._api_error(f'{key} 单次最多提供 20 个')
        expected = {'c2c': 'user_openids', 'group': 'group_openids'}.get(scope)
        if expected and key != expected:
            return None, cls._api_error(f'{scope} 场景只能关联 {expected}')
        if scope in ('channel', 'dm'):
            return None, cls._api_error(f'{scope} 场景不支持关联指定对象')
        return {key: [str(value) for value in values]}, None

    async def get_global_menu(self, *, return_error=False):
        """查询当前生效的全局自定义菜单。"""
        success, data = await self.get_json('/v2/menu')
        if not success:
            return (None, data) if return_error else None
        return (data, None) if return_error else data

    async def update_global_menu(self, menu=None):
        """覆盖全局自定义菜单，返回 (是否成功, 响应 JSON)。"""
        if menu is None:
            return await self.put('/v2/menu', json={})
        if not isinstance(menu, dict):
            return False, self._api_error('menu 必须为字典')
        return await self.put('/v2/menu', json={'menu': dict(menu)})

    async def get_panels(
        self,
        scope,
        *,
        cursor='',
        limit=20,
        return_error=False,
    ):
        """分页查询指定场景的指令面板。"""
        scope, error = self._normalize_panel_choice(scope, _PANEL_SCOPES, 'scope')
        if error:
            return (None, error) if return_error else None
        try:
            limit = max(1, min(int(limit), 50))
        except (TypeError, ValueError):
            limit = 20
        params = {'scope': scope, 'limit': limit}
        if cursor:
            params['cursor'] = str(cursor)
        success, data = await self.get_json('/v2/panels', params=params)
        if not success:
            return (None, data) if return_error else None
        data.setdefault('records', [])
        data.setdefault('next_cursor', '')
        data.setdefault('is_end', not data['next_cursor'])
        return (data, None) if return_error else data

    async def create_panel(
        self,
        scope,
        panel,
        *,
        target_type='all',
        user_openids=None,
        group_openids=None,
    ):
        """创建指令面板，返回 (是否成功, 响应 JSON)。"""
        scope, error = self._normalize_panel_choice(scope, _PANEL_SCOPES, 'scope')
        if error:
            return False, error
        target_type, error = self._normalize_panel_choice(
            target_type, _PANEL_TARGET_TYPES, 'target_type'
        )
        if error:
            return False, error
        if target_type == 'specific' and scope not in ('c2c', 'group'):
            return False, self._api_error('channel 和 dm 场景只支持全局面板')
        if not isinstance(panel, dict):
            return False, self._api_error('panel 必须为字典')
        if target_type == 'all' and (user_openids is not None or group_openids is not None):
            return False, self._api_error('全局面板不能关联指定对象')

        targets, error = self._panel_targets(
            user_openids=user_openids,
            group_openids=group_openids,
            scope=scope,
        )
        if error:
            return False, error
        payload = {
            'scope': scope,
            'target_type': target_type,
            'panel': dict(panel),
            **targets,
        }
        return await self.post_json('/v2/panels', payload)

    async def get_panel(self, panel_id, *, return_error=False):
        """查询指定指令面板的完整配置。"""
        if not panel_id:
            error = self._api_error('缺少 panel_id')
            return (None, error) if return_error else None
        success, data = await self.get_json(f'/v2/panels/{panel_id}')
        if not success:
            return (None, data) if return_error else None
        return (data, None) if return_error else data

    async def update_panel(self, panel_id, panel):
        """覆盖指定指令面板的内容和备注。"""
        if not panel_id:
            return False, self._api_error('缺少 panel_id')
        if not isinstance(panel, dict):
            return False, self._api_error('panel 必须为字典')
        return await self.put(
            f'/v2/panels/{panel_id}',
            json={'panel': dict(panel)},
        )

    async def delete_panel(self, panel_id):
        """删除指定指令面板。"""
        if not panel_id:
            return False, self._api_error('缺少 panel_id')
        return await self.delete(f'/v2/panels/{panel_id}')

    async def update_panel_targets(
        self,
        panel_id,
        op,
        *,
        user_openids=None,
        group_openids=None,
    ):
        """增加或移除指定面板关联的用户或群。"""
        if not panel_id:
            return False, self._api_error('缺少 panel_id')
        op, error = self._normalize_panel_choice(op, {'add', 'del'}, 'op')
        if error:
            return False, error
        targets, error = self._panel_targets(
            user_openids=user_openids,
            group_openids=group_openids,
        )
        if error:
            return False, error
        if not targets:
            return False, self._api_error('必须提供 user_openids 或 group_openids')
        return await self.put(
            f'/v2/panels/{panel_id}/target',
            json={'op': op, **targets},
        )

    async def get_group_member(self, group_id, member_id=None, *, member_openid=None):
        """查询单个群成员详情, 返回 dict, 失败返回 None"""
        member_id = member_id or member_openid
        if not group_id or not member_id:
            return None
        success, data = await self.get_json(f'/v2/groups/{group_id}/members/{member_id}')
        if success and isinstance(data, dict):
            return data
        return None

    get_group_member_info = get_group_member

    async def get_group_members(self, group_id, *, cursor='', return_error=False):
        """分页获取群成员列表；平台每页最多返回 30 条。"""
        params = {'cursor': str(cursor)} if cursor else None
        success, data = await self._request_group(
            group_id,
            'members',
            params=params,
            handle_error=not return_error,
        )
        if not success:
            return (None, data) if return_error else None
        data.setdefault('members', [])
        data.setdefault('next_cursor', '')
        await self._sync_group_members(group_id, data.get('members'))
        return (data, None) if return_error else data

    get_group_member_list = get_group_members

    async def _sync_group_members(self, group_id, members):
        """将群成员分页结果合并写入 groups_users.users，并按 userid/member_openid 去重。"""
        group_id = str(group_id or '').strip()
        if not group_id or not isinstance(members, list) or self._log_service is None:
            return

        lock = self._group_member_sync_locks.setdefault(group_id, asyncio.Lock())
        async with lock:
            try:
                row = await self._log_service.db_fetch_one(
                    'SELECT users FROM groups_users WHERE group_id=?',
                    (group_id,),
                )
                raw_users = row.get('users', '[]') if isinstance(row, dict) else '[]'
                try:
                    stored_users = json.loads(raw_users or '[]')
                except (json.JSONDecodeError, TypeError):
                    stored_users = []
                if not isinstance(stored_users, list):
                    stored_users = []

                user_map = {}
                for item in stored_users:
                    if isinstance(item, dict):
                        entry = dict(item)
                        uid = entry.get('userid') or entry.get('member_openid') or entry.get('openid')
                    else:
                        uid = item
                        entry = {'value': 1, 'last_active': ''}
                    uid = str(uid or '').strip()
                    if not uid:
                        continue
                    entry['userid'] = uid
                    user_map.setdefault(uid, {}).update(entry)

                for member in members:
                    if not isinstance(member, dict):
                        continue
                    member_openid = str(member.get('member_openid') or '').strip()
                    if not member_openid:
                        continue

                    entry = user_map.setdefault(
                        member_openid,
                        {'userid': member_openid, 'value': 1, 'last_active': ''},
                    )
                    entry['userid'] = member_openid
                    entry.update({
                        key: value
                        for key in ('username', 'member_role', 'joined_at', 'union_openid')
                        if (value := member.get(key)) not in (None, '')
                    })
                    if member.get('bot') is not None:
                        if member['bot']:
                            entry['is_bot'] = True
                        else:
                            entry.pop('is_bot', None)

                await self._log_service.db_execute(
                    'INSERT INTO groups_users (group_id, users) VALUES (?, ?) '
                    'ON CONFLICT(group_id) DO UPDATE SET users=excluded.users',
                    (group_id, json.dumps(list(user_map.values()), ensure_ascii=False)),
                )
            except Exception as error:
                log.warning(f'[{self._appid}] 群成员列表写入数据库失败 group={group_id}: {error}')

    async def batch_remove_group_members(
        self,
        group_id,
        member_openids,
        *,
        add_to_member_blacklist=False,
    ):
        """批量移除群成员，单次最多 20 人，可选同时加入群黑名单。"""
        member_openids, error = self._normalize_group_member_openids(member_openids)
        if error:
            return False, error
        payload = {'member_openids': member_openids}
        if add_to_member_blacklist:
            payload['add_to_member_blacklist'] = True
        return await self._request_group(
            group_id,
            'batch_remove_members',
            payload=payload,
        )

    remove_group_members = batch_remove_group_members

    async def get_group_member_blacklist(
        self,
        group_id,
        *,
        cursor='',
        limit=20,
        return_error=False,
    ):
        """分页查询群黑名单，limit 范围为 1 到 100。"""
        try:
            limit = max(1, min(int(limit), 100))
        except (TypeError, ValueError):
            limit = 20
        params = {'limit': limit}
        if cursor:
            params['cursor'] = str(cursor)
        success, data = await self._request_group(
            group_id,
            'member_blacklist',
            params=params,
            handle_error=not return_error,
        )
        if not success:
            return (None, data) if return_error else None
        data.setdefault('users', [])
        data.setdefault('next_cursor', '')
        return (data, None) if return_error else data

    async def operate_group_member_blacklist(self, group_id, op, member_openids):
        """添加或移出群黑名单；op 只能为 add 或 del，单次最多 20 人。"""
        op = str(op or '').strip().lower()
        if op not in ('add', 'del'):
            return False, {'message': 'op 只能为 add 或 del', 'code': -1}
        member_openids, error = self._normalize_group_member_openids(member_openids)
        if error:
            return False, error
        return await self._request_group(
            group_id,
            'member_blacklist',
            payload={'op': op, 'member_openids': member_openids},
        )

    update_group_member_blacklist = operate_group_member_blacklist

    @staticmethod
    def _normalize_group_member_openids(member_openids):
        if not isinstance(member_openids, (list, tuple)):
            return None, MessageSender._api_error('member_openids 必须为列表')
        if not member_openids:
            return None, MessageSender._api_error('member_openids 不能为空')
        if len(member_openids) > 20:
            return None, MessageSender._api_error('单次最多处理 20 个成员')
        normalized = [str(openid).strip() for openid in member_openids]
        if any(not openid for openid in normalized):
            return None, MessageSender._api_error('member_openids 不能包含空值')
        return normalized, None

    async def get_group_record(self, group_id):
        """从 data.db 读取完整群记录，不调用平台接口。"""
        if not group_id or self._log_service is None:
            return None
        row = await self._log_service.db_fetch_one(
            'SELECT group_id, group_name, users, group_member_num, is_admin, '
            'is_full_access, allow_proactive_msg, in_group '
            'FROM groups_users WHERE group_id=?',
            (str(group_id),),
        )
        if not row:
            return None
        try:
            users = json.loads(row.get('users') or '[]')
        except (json.JSONDecodeError, TypeError):
            users = []
        return {
            'group_id': str(row.get('group_id') or ''),
            'group_name': str(row.get('group_name') or ''),
            'users': users if isinstance(users, list) else [],
            'group_member_num': int(row.get('group_member_num') or 0),
            'is_admin': bool(row.get('is_admin')),
            'is_full_access': bool(row.get('is_full_access')),
            'allow_proactive_msg': bool(row.get('allow_proactive_msg')),
            'in_group': bool(row.get('in_group')),
        }

    async def _handle_group_error(self, group_id, error):
        state = _group_error_state(error)
        if not state or self._log_service is None:
            return
        group_id = str(group_id)
        if state == 'removed':
            await self._log_service.db_execute('DELETE FROM groups_users WHERE group_id=?', (group_id,))
        else:
            await self._log_service.db_execute(
                'INSERT INTO groups_users '
                '(group_id, is_admin, is_full_access, allow_proactive_msg, in_group) '
                'VALUES (?, 0, 0, 0, 0) ON CONFLICT(group_id) DO UPDATE SET '
                'is_admin=0, is_full_access=0, allow_proactive_msg=0, in_group=0',
                (group_id,),
            )
        action = '已清理群成员和全量群记录' if state == 'removed' else '已标记退群并移出全量群'
        log.info(f'[{self._appid}] 群 {group_id} {action}')

    async def _request_group(
        self, group_id, endpoint, *, payload=None, params=None, handle_error=True,
    ):
        """请求群接口并统一处理错误，返回 (是否成功, 响应 JSON)。"""
        if not group_id:
            return False, {'message': '缺少 group_id', 'code': -1}
        try:
            url = f'/v2/groups/{group_id}/{endpoint}'
            if payload is None:
                success, data = await self.get_json(url, **({'params': params} if params else {}))
            else:
                success, data = await self.post_json(url, payload)
        except Exception as e:
            success, data = False, {'message': str(e), 'code': -1}
        data = data if isinstance(data, dict) else {'message': str(data), 'code': -1}
        if not success and handle_error:
            await self._handle_group_error(group_id, data)
        return success, data

    async def get_group_info(self, group_id, *, return_error=False):
        """获取群资料，写入 data.db，成功时返回接口数据。"""
        success, data = await self._request_group(
            group_id, 'info', handle_error=not return_error)
        if not success:
            return (None, data) if return_error else None
        group_name = str(data.get('group_name') or '')
        try:
            member_num = max(0, int(data.get('group_member_num') or 0))
        except (TypeError, ValueError):
            member_num = 0
        if self._log_service is not None:
            await self._log_service.db_execute(
                """
                INSERT INTO groups_users (group_name, group_id, users, group_member_num, in_group)
                VALUES (?, ?, '[]', ?, 1)
                ON CONFLICT(group_id) DO UPDATE SET
                    group_name=excluded.group_name,
                    group_member_num=excluded.group_member_num,
                    in_group=1
                """,
                (group_name, str(group_id), member_num),
            )
        return (data, None) if return_error else data

    async def get_group_bot_state(self, group_id, *, return_error=False):
        """获取机器人群状态，并同步 data.db 中的群权限字段。"""
        success, data = await self._request_group(
            group_id, 'bot_state', handle_error=not return_error)
        if not success:
            return (None, data) if return_error else None
        if self._log_service is not None:
            group_id = str(group_id)
            role = str(data.get('member_role') or '')
            is_full_access = data.get('recv_msg_setting') == 'all'
            await self._log_service.db_execute(
                """
                INSERT INTO groups_users (
                    group_id, is_admin, is_full_access, allow_proactive_msg, in_group
                ) VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(group_id) DO UPDATE SET
                    is_admin=excluded.is_admin,
                    is_full_access=excluded.is_full_access,
                    allow_proactive_msg=excluded.allow_proactive_msg,
                    in_group=1
                """,
                (
                    group_id,
                    1 if role in ('admin', 'owner') else 0,
                    1 if is_full_access else 0,
                    1 if data.get('allow_proactive_msg') else 0,
                ),
            )
        return (data, None) if return_error else data

    async def refresh_group_info(self, group_id):
        """同时刷新群资料和机器人群状态，并返回两个接口的结果。"""
        (group_info, group_error), (bot_state, bot_state_error) = await asyncio.gather(
            self.get_group_info(group_id, return_error=True),
            self.get_group_bot_state(group_id, return_error=True),
        )
        states = {_group_error_state(error) for error in (group_error, bot_state_error)}
        state = 'removed' if 'removed' in states else 'left_group' if 'left_group' in states else ''
        if state:
            source_error = group_error if _group_error_state(group_error) == state else bot_state_error
            await self._handle_group_error(group_id, source_error)
        return {
            'group_info': group_info,
            'bot_state': bot_state,
            'removed': state == 'removed',
            'left_group': state == 'left_group',
            'errors': {
                'group_info': {
                    'endpoint': f'/v2/groups/{group_id}/info',
                    'response': group_error,
                }
                if group_error
                else None,
                'bot_state': {
                    'endpoint': f'/v2/groups/{group_id}/bot_state',
                    'response': bot_state_error,
                }
                if bot_state_error
                else None,
            },
        }

    async def get_group_join_requests(self, group_id, *, cursor='', limit=20, return_error=False):
        """分页获取入群申请；成功返回接口数据，失败返回 None。"""
        try:
            limit = max(1, min(int(limit), 50))
        except (TypeError, ValueError):
            limit = 20
        params = {'limit': limit}
        if cursor:
            params['cursor'] = str(cursor)
        success, data = await self._request_group(
            group_id,
            'join_request_list',
            params=params,
            handle_error=not return_error,
        )
        if not success:
            return (None, data) if return_error else None
        data.setdefault('list', [])
        data.setdefault('next_cursor', '')
        return (data, None) if return_error else data

    async def review_group_join_request(
        self,
        group_id,
        member_openid,
        op,
        *,
        join_request_id='',
        reject_reason='',
        add_to_member_blacklist=False,
    ):
        """审批入群申请；op 为 approve 或 decline，返回 (是否成功, 响应 JSON)。"""
        op = str(op or '').strip().lower()
        if op not in ('approve', 'decline'):
            return False, {'message': 'op 只能为 approve 或 decline', 'code': -1}
        if not member_openid:
            return False, {'message': '缺少 member_openid', 'code': -1}
        payload = {'op': op}
        if join_request_id:
            payload['join_request_id'] = str(join_request_id)
        if op == 'decline':
            if reject_reason:
                payload['reject_reason'] = str(reject_reason)
            if add_to_member_blacklist:
                payload['add_to_member_blacklist'] = True
        return await self._request_group(
            group_id,
            f'approval_join_request/{member_openid}',
            payload=payload,
        )

    async def get_group_restrict_chat_setting(self, group_id, *, return_error=False):
        """查询全员禁言规则与当前成员禁言列表。"""
        success, data = await self._request_group(
            group_id,
            'restrict_chat_setting',
            handle_error=not return_error,
        )
        if not success:
            return (None, data) if return_error else None
        return (data, None) if return_error else data

    async def set_group_member_mute(self, group_id, members):
        """批量增加、更新或解除成员禁言，单次最多处理 20 人。"""
        if not isinstance(members, (list, tuple)):
            return False, {'message': 'members 必须为列表', 'code': -1}
        if len(members) > 20:
            return False, {'message': '单次最多设置 20 个成员', 'code': -1}
        if any(not isinstance(item, dict) for item in members):
            return False, {'message': 'members 中的每一项都必须为字典', 'code': -1}
        return await self._request_group(
            group_id,
            'restrict_chat_setting',
            payload={'members': [dict(item) for item in members]},
        )

    async def get_bot_member(self, group_id):
        """查询机器人自身在该群的成员信息, 返回 dict, 失败返回 None"""
        if not group_id:
            return None
        member_id = bot_openid.first_id(self._appid)
        if not member_id:
            return None
        return await self.get_group_member(group_id, member_id)

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

    def _build_core_payload(self, content, buttons, media, msg_type, **kwargs):
        """统一载荷构建 (回复/推送共用)"""
        skip_suffix = kwargs.pop('skip_suffix', False)
        force_verify_image_resource = kwargs.pop('force_verify_image_resource', False)
        message_reference = kwargs.pop('message_reference', None)
        message_reference_id = kwargs.pop('message_reference_id', None) or kwargs.pop('reference_message_id', None)
        button_font_size = kwargs.pop('button_font_size', None)
        button_style = kwargs.pop('button_style', None)
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
        elif msg_type == MSG_TYPE_MARKDOWN or (use_md and msg_type != MSG_TYPE_TEXT):
            payload['msg_type'] = MSG_TYPE_MARKDOWN
            md_content = str(content) if content is not None else ''
            suffix = '' if skip_suffix else cfg.get_bot_setting(self._appid, 'message.markdown_suffix', '')
            if suffix and '\\' in suffix:
                suffix = _unescape(suffix)
            payload['markdown'] = {'content': md_content + suffix if suffix else md_content}
            if force_verify_image_resource:
                payload['markdown']['force_verify_image_resource'] = True
        else:
            payload['msg_type'] = MSG_TYPE_TEXT
            payload['content'] = content or ''

        if buttons:
            payload['keyboard'] = build_keyboard(
                buttons, self._appid, font_size=button_font_size, style=button_style)
        if message_reference:
            payload['message_reference'] = message_reference
        elif message_reference_id:
            payload['message_reference'] = {
                'message_id': str(message_reference_id),
                'ignore_get_message_error': True,
            }
        payload.update(kwargs)
        return payload
