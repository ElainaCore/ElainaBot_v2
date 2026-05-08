#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""消息发送器 - 异步

封装消息回复、主动推送、交互回复、自动撤回等。
媒体上传 / SILK / 分片在 media.py, 按钮构建在 keyboard.py。

用法:
    sender = MessageSender(token_manager)
    await sender.reply(event, "你好", buttons=[[{...}]])
    await sender.reply_image(event, image_bytes)
"""

import os
import json
import time
import random
import asyncio
import hashlib
import ssl
from datetime import datetime
import aiohttp
from core.base.logger import get_logger, FRAMEWORK, report_error, report_error_raw
from core.base.config import cfg
from core.message.template import tpl
from core.message.keyboard import (build_keyboard, build_prompt_keyboard,
                                    convert_simple_ark_data)
from core.message.media import (upload_media_bytes, upload_media_via_url,
                                 get_image_size as _get_image_size,
                                 _resolve_upload_ep)
from core.module.hook import get_hook_manager as _get_hooks

log = get_logger(FRAMEWORK, "消息发送")

# ==================== 常量 ====================

MSG_TYPE_TEXT = 0
MSG_TYPE_MARKDOWN = 2
MSG_TYPE_ARK = 3
MSG_TYPE_MEDIA = 7

_API_BASE = "https://api.sgroup.qq.com"

_IGNORE_ERROR_CODES = frozenset({11293, 40054002, 40054003})
_TOKEN_EXPIRED_CODE = 11244


def _msg_seq():
    return random.randint(10000, 999999)


class MessageSender:
    """消息发送器 (每个机器人实例一个)"""

    __slots__ = ('_token_mgr', '_appid', '_client', '_base_url', '_ssl_ctx', '_web_log_cb', '_bot_name', '_bot_qq', '_log_service', '_reply_log_cb', '_reply_plugin_name', '_custom_api_base', '_media_dir', '_last_media_label')

    def __init__(self, token_manager, custom_api_base=''):
        self._token_mgr = token_manager
        self._appid = token_manager.appid
        self._custom_api_base = custom_api_base.rstrip('/') if custom_api_base else ''
        self._base_url = self._custom_api_base or _API_BASE
        self._ssl_ctx = ssl.create_default_context()
        self._ssl_ctx.check_hostname = False
        self._ssl_ctx.verify_mode = ssl.CERT_NONE
        self._client = None  # 延迟创建
        self._web_log_cb = None  # 由 BotManager 注入
        self._bot_name = ''     # 由 BotInstance 设置
        self._bot_qq = ''       # 由 BotInstance 设置
        self._log_service = None # 由 BotInstance 注入, 用于持久化回复日志
        self._reply_log_cb = None # 由 dispatch 临时注入, 记录插件回复
        self._reply_plugin_name = '' # 由 dispatch 临时注入, 当前插件名称
        self._media_dir = ''         # 由 BotManager 注入, data/media 绝对路径
        self._last_media_label = ''  # 临时: 上次发送的媒体标签

    async def _ensure_client(self):
        if self._client is None or self._client.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            conn = aiohttp.TCPConnector(
                ssl=self._ssl_ctx, limit=50, limit_per_host=10,
                use_dns_cache=True, ttl_dns_cache=600,
                keepalive_timeout=30)
            self._client = aiohttp.ClientSession(
                base_url=self._base_url, timeout=timeout, connector=conn)
        return self._client

    async def close(self):
        if self._client and not self._client.closed:
            await self._client.close()

    # ==================== HTTP ====================

    async def _request(self, method, endpoint, **kwargs):
        client = await self._ensure_client()
        for attempt in range(2):
            token = await self._token_mgr.get_token()
            headers = kwargs.pop('headers', {})
            headers['Authorization'] = f"QQBot {token}"
            if 'json' in kwargs:
                headers.setdefault('Content-Type', 'application/json')
            try:
                _t = time.time()
                async with client.request(method, endpoint, headers=headers, **kwargs) as resp:
                    body = await resp.read()
                    _dt = time.time() - _t
                    if _dt > 1:
                        log.warning(f"[{self._appid}] API 慢请求 {_dt*1000:.0f}ms {method} {endpoint}")
                    if resp.status >= 400:
                        try:
                            err = json.loads(body)
                        except Exception:
                            err = {'message': body.decode(errors='replace'), 'code': resp.status}
                        if err.get('code') == _TOKEN_EXPIRED_CODE and attempt == 0:
                            await self._token_mgr.refresh_token()
                            await asyncio.sleep(0.1)
                            continue
                        return False, err
                    if body:
                        return True, json.loads(body)
                    return True, {}
            except Exception as e:
                return False, {'message': str(e), 'code': -1}
        return False, {'message': 'max retries', 'code': -1}

    async def post_json(self, endpoint, payload):
        return await self._request('POST', endpoint, json=payload)

    async def put(self, endpoint, **kwargs):
        return await self._request('PUT', endpoint, **kwargs)

    async def delete(self, endpoint):
        return await self._request('DELETE', endpoint)

    # ==================== 回复 ====================

    async def reply(self, event, content=None, *, buttons=None, media=None,
                    msg_type=None, template_name=None, template_vars=None,
                    prompt_buttons=None, auto_delete_time=None,
                    **kwargs):
        """回复事件消息"""
        if template_name:
            use_md = cfg.get_bot_setting(self._appid, 'message.use_markdown', True)
            vars_ = {'user_id': event.user_id or '',
                      'group_id': event.group_id or ''}
            if template_vars:
                vars_.update(template_vars)
            content, tpl_buttons = tpl.render(template_name, use_markdown=use_md,
                                              appid=self._appid, **vars_)
            if tpl_buttons and not buttons:
                buttons = tpl_buttons

        if not content and not media:
            return None

        endpoint = event.reply_endpoint
        if not endpoint:
            log.warning(f"[{self._appid}] 无法推断回复路径: {event.event_type}")
            return None

        payload = self._build_payload(event, content, buttons, media, msg_type,
                                      prompt_buttons=prompt_buttons,
                                      **kwargs)

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

    async def reply_file(self, event, file_data, content='', *, file_name=None,
                         auto_delete_time=None, target_user_id=None, target_group_id=None):
        kw = dict(auto_delete_time=auto_delete_time,
                  target_user_id=target_user_id, target_group_id=target_group_id)
        # URL → 直接上传
        if isinstance(file_data, str) and file_data.startswith(('http://', 'https://')):
            file_info = await upload_media_via_url(self, event, file_data, 4,
                                                   file_name=file_name, **kw)
            return await self._send_media_payload(event, file_info, content,
                                                  **kw) if file_info else None
        # 本地路径 → 读取
        if isinstance(file_data, str) and os.path.exists(file_data):
            file_name = file_name or os.path.basename(file_data)
            with open(file_data, 'rb') as f:
                file_data = f.read()
        return await self._send_media(event, file_data, 4, content,
                                      file_name=file_name, **kw)

    async def reply_ark(self, event, template_id, kv_data, content='', *,
                        auto_delete_time=None):
        if isinstance(kv_data, (tuple, list)) and template_id in (23, 24, 37):
            kv_data = convert_simple_ark_data(template_id, kv_data)
        payload = {
            'msg_type': MSG_TYPE_ARK, 'msg_seq': _msg_seq(),
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

    async def send_to_group(self, group_id, content=None, *, msg_id=None, event_id=None,
                            buttons=None, media=None, msg_type=None, **kwargs):
        return await self._send_push(f"/v2/groups/{group_id}/messages",
                                     content, buttons, media, msg_type,
                                     msg_id=msg_id, event_id=event_id, **kwargs)

    async def send_to_user(self, user_id, content=None, *, msg_id=None, event_id=None,
                           buttons=None, media=None, msg_type=None, **kwargs):
        return await self._send_push(f"/v2/users/{user_id}/messages",
                                     content, buttons, media, msg_type,
                                     msg_id=msg_id, event_id=event_id, **kwargs)

    async def _send_push(self, endpoint, content, buttons, media, msg_type, **kwargs):
        payload = self._build_push_payload(content, buttons, media, msg_type, **kwargs)
        ok, data = await self.post_json(endpoint, payload)
        return ok, data, payload

    async def send_to_channel(self, channel_id, content=None, *, msg_id=None,
                              buttons=None, **kwargs):
        endpoint = f"/channels/{channel_id}/messages"
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
        file_info = await upload_media_bytes(self, image_data, 1,
                                             f"/v2/{prefix}/{target_id}/files")
        if not file_info:
            return False, {'message': '图片上传失败'}
        payload = {'msg_type': MSG_TYPE_MEDIA, 'msg_seq': _msg_seq(),
                   'content': content, 'media': {'file_info': file_info}}
        if msg_id:
            payload['msg_id'] = msg_id
        return await self.post_json(f"/v2/{prefix}/{target_id}/messages", payload)

    # ==================== 唤醒消息 ====================

    async def send_wakeup(self, user_id, content='', buttons=None):
        """发送唤醒消息 (检查是否符合条件)

        Returns:
            (success: bool, msg_or_reason: str)
        """
        if not self._log_service:
            return (False, "log_service 未初始化")
        can_send, stage, days = await self._log_service.wakeup_can_send(user_id)
        if not can_send:
            if days == -1:
                return (False, "用户未在召回表中(从未发过消息)")
            if days > 30:
                return (False, f"超过30天({days}天)无法召回")
            return (False, f"今日已推送过该周期(周期{stage})")
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
            payload = {'msg_type': 0, 'content': content,
                       'msg_seq': _msg_seq(), 'is_wakeup': True}
            if buttons:
                payload['keyboard'] = build_keyboard(buttons, self._appid)
            success, data = await self.post_json(
                f'/v2/users/{user_id}/messages', payload)
            if success:
                return (True, data.get('id') or data.get('msg_id', ''))
            return (False, data.get('message', '发送失败'))
        except Exception as e:
            return (False, str(e))

    # ==================== 交互 / 撤回 ====================

    async def ack_interaction(self, event, code=0):
        return await self.put(f"/interactions/{event.message_id}", json={'code': code})

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
        success, data = await self.post_json(
            '/v2/generate_url_link', {'callbackData': str(callback_data)})
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
        return await upload_media_bytes(self, file_bytes, file_type, endpoint,
                                        file_name=file_name)

    # ==================== 载荷构建 ====================

    def _build_payload(self, event, content, buttons, media, msg_type, *,
                       prompt_buttons=None, **kwargs):
        payload = self._build_core_payload(content, buttons, media, msg_type, **kwargs)
        _set_msg_or_event_id(payload, event)
        if prompt_buttons:
            pk = build_prompt_keyboard(prompt_buttons)
            if pk:
                payload['prompt_keyboard'] = pk
        return payload

    def _build_push_payload(self, content, buttons, media, msg_type, **kwargs):
        return self._build_core_payload(content, buttons, media, msg_type, **kwargs)

    def _build_core_payload(self, content, buttons, media, msg_type, **kwargs):
        """统一载荷构建 (回复/推送共用)"""
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
            suffix = cfg.get_bot_setting(self._appid, 'message.markdown_suffix', '')
            payload['markdown'] = {'content': content + suffix if suffix else content}
        else:
            payload['msg_type'] = MSG_TYPE_TEXT
            payload['content'] = content or ''

        if buttons:
            payload['keyboard'] = build_keyboard(buttons, self._appid)
        payload.update(kwargs)
        return payload

    # ==================== 内部: 媒体发送 ====================

    def _maybe_auto_recall(self, event, data, delay):
        if delay and data:
            mid = _extract_message_id(data)
            if mid:
                asyncio.create_task(self._auto_recall(event, mid, delay))

    async def _send_media(self, event, data, file_type, content, *,
                          file_name=None, auto_delete_time=None,
                          target_user_id=None, target_group_id=None, msg_id=None):
        upload_ep = _resolve_upload_ep(target_group_id, target_user_id, event)
        if not upload_ep:
            return None

        # 网络地址: 直接记录 URL, 不保存到本地
        is_url = isinstance(data, str) and data.startswith(('http://', 'https://'))
        original_url = data if is_url else None

        if is_url:
            try:
                client = await self._ensure_client()
                async with client.get(data) as resp:
                    data = await resp.read()
            except Exception as e:
                log.warning(f"[{self._appid}] 下载媒体失败: {e}")
                return None
        if not isinstance(data, bytes):
            return None

        type_name = self._MEDIA_TYPE_NAMES.get(file_type, '媒体')
        if original_url:
            self._last_media_label = f'[{type_name}]{original_url}'
        else:
            self._last_media_label = self._save_media(data, file_type)

        file_info = await upload_media_bytes(self, data, file_type, upload_ep,
                                             file_name=file_name)
        if not file_info:
            self._last_media_label = ''
            return None
        return await self._send_media_payload(event, file_info, content, auto_delete_time,
                                              target_user_id=target_user_id,
                                              target_group_id=target_group_id,
                                              msg_id=msg_id)

    _MEDIA_TYPE_NAMES = {1: '图片', 2: '视频', 3: '语音', 4: '文件'}
    _MEDIA_TYPE_EXTS  = {1: '.png', 2: '.mp4', 3: '.mp3', 4: '.dat'}

    def _save_media(self, data, file_type):
        """本地 bytes → 保存到 data/media/, MD5 校验唯一 (同内容只保存一次)"""
        type_name = self._MEDIA_TYPE_NAMES.get(file_type, '媒体')
        if not self._media_dir:
            return f'[{type_name}]'
        try:
            ext = self._MEDIA_TYPE_EXTS.get(file_type, '.dat')
            md5 = hashlib.md5(data).hexdigest()
            filename = f'{md5}{ext}'
            filepath = os.path.join(self._media_dir, filename)
            if not os.path.exists(filepath):
                with open(filepath, 'wb') as f:
                    f.write(data)
            return f'[{type_name}]/api/media/{filename}'
        except Exception as e:
            log.debug(f'[媒体保存] {e}')
            return f'[{type_name}]'

    async def _send_media_payload(self, event, file_info, content, auto_delete_time=None, *,
                                  target_user_id=None, target_group_id=None, msg_id=None):
        proactive = bool(target_user_id or target_group_id)
        payload = {
            'msg_type': MSG_TYPE_MEDIA, 'msg_seq': _msg_seq(),
            'content': content or '', 'media': {'file_info': file_info},
        }
        if not proactive:
            _set_msg_or_event_id(payload, event)
        elif msg_id:
            payload['msg_id'] = msg_id

        endpoint = (f"/v2/groups/{target_group_id}/messages" if target_group_id
                    else f"/v2/users/{target_user_id}/messages" if target_user_id
                    else event.reply_endpoint)
        if not endpoint:
            return None

        success, data = await self._send_with_error_handling(endpoint, payload, event, content)
        if success:
            self._maybe_auto_recall(event, data, auto_delete_time)
        return data

    # ==================== 错误处理 ====================

    async def _send_with_error_handling(self, endpoint, payload, event, content=None):
        # before_send hook (管道模式, 可修改/拦截)
        hooks = _get_hooks()
        if hooks.has('before_send'):
            hook_data = {'endpoint': endpoint, 'payload': payload,
                         'event': event, 'content': content, 'appid': self._appid}
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
            report_error_raw(
                FRAMEWORK, '消息发送',
                content=(getattr(event, 'content', '') or '')[:2000],
                tb=json.dumps(data, ensure_ascii=False, default=str)[:2000] if data else '',
                context=json.dumps(payload, ensure_ascii=False, default=str)[:2000],
                appid=self._appid,
            )
            return False, data

        self._log_sent(payload, event, content)

        # after_send hook (广播)
        if hooks.has('after_send'):
            await hooks.emit('after_send', {
                'success': True, 'data': data, 'payload': payload,
                'event': event, 'appid': self._appid,
            })
        return True, data

    def _log_sent(self, payload, event, content):
        """发送成功后的日志记录 (Web面板 + 持久化)"""
        # 拼装日志文本
        md = payload.get('markdown')
        text = (md.get('content', '') if md else None) or content or payload.get('content', '') or ''
        if payload.get('msg_type') == MSG_TYPE_MEDIA:
            label = self._last_media_label or '[media]'
            self._last_media_label = ''
            text = f'{label} {text}'.rstrip() if text else label
        kb = payload.get('keyboard')
        if kb:
            try:
                text += '\n[keyboard] ' + json.dumps(kb, ensure_ascii=False)
            except Exception:
                pass
        user_id = getattr(event, 'user_id', '') or ''
        group_id = getattr(event, 'group_id', '') or ''
        if self._web_log_cb:
            try:
                self._web_log_cb('message', {
                    'appid': self._appid,
                    'bot_name': self._bot_name or self._appid,
                    'bot_qq': self._bot_qq or '',
                    'user_id': user_id, 'group_id': group_id,
                    'content': text, 'is_bot': True,
                    'direction': 'send',
                    'plugin_name': self._reply_plugin_name or '',
                })
            except Exception:
                pass
        if self._reply_log_cb:
            try:
                self._reply_log_cb(text, user_id, group_id,
                                   json.dumps(payload, ensure_ascii=False, default=str))
            except Exception:
                pass

    async def _auto_recall(self, event, message_id, delay):
        try:
            await asyncio.sleep(delay)
            await self.recall(event, message_id)
        except Exception:
            pass


# ==================== 模块级辅助 ====================

def _set_msg_or_event_id(payload, event):
    if event.needs_msg_id and event.message_id:
        payload['msg_id'] = event.message_id
    elif event.needs_event_id:
        payload['event_id'] = event.event_id or ''


def _extract_message_id(data):
    if isinstance(data, dict):
        return data.get('id') or data.get('msg_id') or data.get('message_id')
    return None
