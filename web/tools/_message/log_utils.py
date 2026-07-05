"""消息管理 — 日志记录辅助"""

import asyncio
import json

from core.base.logger import FRAMEWORK, report_error, report_error_raw
from core.message.response import extract_message_id, extract_reference_id


def _build_display(msg_type, content, image_data, media_file_type, ark_template_id, media_label=''):
    """构建日志显示内容"""
    if msg_type == 'media':
        type_names = {1: '图片', 2: '视频', 3: '语音', 4: '文件'}
        return f'[富媒体:{type_names.get(media_file_type, "?")}] {content[:200]}'
    if msg_type == 'ark':
        return f'[ARK:{ark_template_id}] {content[:200]}'
    if msg_type == 'markdown':
        return f'[Markdown] {content[:200]}'
    if image_data and media_label:
        return f'{content[:200]}\n{media_label}' if content else media_label
    return content[:200]


def _log_sent_message(bot, chat_type, chat_id, display, bot_appid, bot_name, bot_qq='', payload=None, resp_data=None):
    """成功发送 → 写消息数据库 + 推送到面板"""
    group_id = chat_id if chat_type == 'group' else ''
    user_id = chat_id if chat_type != 'group' else ''
    raw = json.dumps(payload, ensure_ascii=False, default=str) if payload else display
    message_id = extract_message_id(resp_data)
    reference_id = extract_reference_id(resp_data)

    # 写 message.db
    try:
        log_service = getattr(bot, 'log_service', None)
        if log_service:
            asyncio.ensure_future(
                log_service.add(
                    'message',
                    {
                        'message_id': message_id,
                        'reference_id': reference_id,
                        'user_id': user_id,
                        'group_id': group_id,
                        'content': display,
                        'plugin_name': 'WebPanel',
                        'raw_message': raw,
                        'direction': 'send',
                        'context': resp_data if resp_data is not None else '',
                    },
                )
            )
    except Exception:
        pass

    # 推送到面板实时日志
    try:
        import web.ws as _ws

        _ws.push_log(
            'message',
            {
                'appid': bot_appid,
                'bot_name': bot_name,
                'bot_qq': bot_qq,
                'user_id': user_id,
                'group_id': group_id,
                'content': display,
                'message_id': message_id,
                'reference_id': reference_id,
                'raw_message': raw,
                'is_bot': True,
                'direction': 'send',
                'source': 'web_panel',
                'plugin_name': 'WebPanel',
            },
        )
    except Exception:
        pass


def _log_send_error(bot, msg_type, chat_type, chat_id, send_payload, api_resp, bot_appid, msg_id=''):
    """发送失败写报错数据库 (content=原始消息, traceback=API 响应, context=载荷)"""
    report_error_raw(
        FRAMEWORK,
        'Web消息发送',
        content=f'[WebPanel] chat_type={chat_type} chat_id={chat_id} msg_id={msg_id}',
        tb=json.dumps(api_resp, ensure_ascii=False, default=str)[:2000] if api_resp else '',
        context=json.dumps(send_payload, ensure_ascii=False, default=str)[:2000] if send_payload else '',
        appid=bot_appid,
    )


def _log_upload_error(sender, endpoint, resp_data, detail=''):
    """上传失败 → 写报错数据库"""
    err_msg = resp_data.get('message', '') if isinstance(resp_data, dict) else str(resp_data)
    err_code = resp_data.get('code', '') if isinstance(resp_data, dict) else ''
    report_error(
        FRAMEWORK,
        'Web媒体上传',
        f'上传失败 {detail} → {err_code}: {err_msg}',
        context={
            'appid': getattr(sender, '_appid', ''),
            'endpoint': endpoint,
            'api_response': json.dumps(resp_data, ensure_ascii=False, default=str)[:1000] if resp_data else '',
        },
    )
