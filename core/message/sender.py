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

    async def get_group_member(self, group_id, member_id):
        """查询单个群成员详情, 返回 dict, 失败返回 None"""
        if not group_id or not member_id:
            return None
        success, data = await self.get_json(f'/v2/groups/{group_id}/members/{member_id}')
        if success and isinstance(data, dict):
            return data
        return None

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
            limit = max(1, min(int(limit), 100))
        except (TypeError, ValueError):
            limit = 20
        params = {'limit': limit}
        if cursor:
            params['cursor'] = str(cursor)
        success, data = await self._request_group(
            group_id,
            'join_request_list',
            params=params,
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
        success, data = await self._request_group(group_id, 'restrict_chat_setting')
        if not success:
            return (None, data) if return_error else None
        return (data, None) if return_error else data

    async def set_group_member_mute(self, group_id, members):
        """批量增加、更新或解除成员禁言，单次最多处理 10 人。"""
        if not isinstance(members, (list, tuple)):
            return False, {'message': 'members 必须为列表', 'code': -1}
        if len(members) > 10:
            return False, {'message': '单次最多设置 10 个成员', 'code': -1}
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
