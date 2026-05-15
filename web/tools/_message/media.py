"""消息管理 — 富媒体 / ARK 发送辅助"""

import json
import random
import base64

from web.tools._message.log_utils import _log_upload_error


# ==================== 辅助: 富媒体 ====================

def _media_endpoints(group_id, user_id):
    """返回 (upload_ep, send_ep)"""
    if group_id:
        return f"/v2/groups/{group_id}/files", f"/v2/groups/{group_id}/messages"
    return f"/v2/users/{user_id}/files", f"/v2/users/{user_id}/messages"


async def _web_send_media(sender, *, file_info, content='', group_id=None, user_id=None, msg_id=''):
    """file_info 已就绪, 直接发送富媒体消息"""
    _, send_ep = _media_endpoints(group_id, user_id)
    payload = {'msg_type': 7, 'msg_seq': random.randint(10000, 999999),
               'content': content or '', 'media': {'file_info': file_info}}
    if msg_id:
        payload['msg_id'] = msg_id
    return await sender.post_json(send_ep, payload)


async def _send_media_url(sender, url, *, file_type=1, group_id=None, user_id=None, msg_id=''):
    """通过 URL 上传并发送富媒体"""
    upload_ep, _ = _media_endpoints(group_id, user_id)
    ok, resp = await sender.post_json(upload_ep,
                                       {'srv_send_msg': False, 'file_type': file_type, 'url': url})
    if not ok:
        _log_upload_error(sender, upload_ep, resp, f'URL上传 file_type={file_type}')
        return False, resp
    file_info = resp.get('file_info')
    if not file_info:
        _log_upload_error(sender, upload_ep, resp, 'URL上传返回无file_info')
        return False, {'message': '上传失败: 无 file_info'}
    return await _web_send_media(sender, file_info=file_info,
                                  group_id=group_id, user_id=user_id, msg_id=msg_id)


async def _send_text_with_image(sender, content, image_bytes, *, group_id=None, user_id=None, msg_id=''):
    """上传图片 bytes 并发送"""
    if not image_bytes:
        return False, {'message': '图片数据为空'}
    upload_ep, _ = _media_endpoints(group_id, user_id)
    ok, resp = await sender.post_json(upload_ep, {
        'srv_send_msg': False, 'file_type': 1,
        'file_data': base64.b64encode(image_bytes).decode(),
    })
    if not ok:
        return False, resp
    file_info = resp.get('file_info')
    if not file_info:
        return False, {'message': '上传失败: 无 file_info'}
    return await _web_send_media(sender, file_info=file_info, content=content,
                                  group_id=group_id, user_id=user_id, msg_id=msg_id)


# ==================== 辅助: ARK ====================

async def _send_ark(sender, template_id, kv_json_str, *, group_id=None, user_id=None, msg_id=''):
    """发送 ARK 消息

    template_id: ARK 模板 ID (23, 24, 37 等)
    kv_json_str: kv 数据的 JSON 字符串 (数组)
    """
    try:
        kv_data = json.loads(kv_json_str)
    except json.JSONDecodeError as e:
        return False, {'message': f'ARK kv JSON 解析失败: {e}'}

    if not isinstance(kv_data, list):
        return False, {'message': 'ARK kv 必须是 JSON 数组'}

    payload = {
        'msg_type': 3, 'msg_seq': random.randint(10000, 999999),
        'content': '',
        'ark': {'template_id': template_id, 'kv': kv_data},
    }
    if msg_id:
        payload['msg_id'] = msg_id

    _, send_ep = _media_endpoints(group_id, user_id)
    return await sender.post_json(send_ep, payload)
