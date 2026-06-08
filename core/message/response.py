"""消息 API 响应辅助: 保留原文并提取常用 ID。"""

import json

_MESSAGE_ID_KEYS = ('id', 'msg_id', 'message_id')
_REFERENCE_ID_KEYS = ('ref_idx', 'msg_idx', 'message_reference_id', 'reference_id')


class RawResponseDict(dict):
    """带 HTTP 原始 JSON body 的 dict 响应。"""

    __slots__ = ('_raw_response_text',)

    def __init__(self, *args, raw_response_text='', **kwargs):
        super().__init__(*args, **kwargs)
        self._raw_response_text = raw_response_text


class RawResponseList(list):
    """带 HTTP 原始 JSON body 的 list 响应。"""

    __slots__ = ('_raw_response_text',)

    def __init__(self, *args, raw_response_text=''):
        super().__init__(*args)
        self._raw_response_text = raw_response_text


def loads_raw_response(body):
    """解析 JSON body, 同时保留原始响应文本供日志原样写入。"""
    raw_text = body.decode('utf-8', errors='replace')
    result = json.loads(raw_text)
    if isinstance(result, dict):
        return RawResponseDict(result, raw_response_text=raw_text)
    if isinstance(result, list):
        return RawResponseList(result, raw_response_text=raw_text)
    return result


def raw_response_text(value):
    raw = getattr(value, '_raw_response_text', None)
    return raw if isinstance(raw, str) else None


def _first_str(data, keys):
    for key in keys:
        value = data.get(key)
        if value:
            return str(value)
    return ''


def extract_message_id(data):
    return _first_str(data, _MESSAGE_ID_KEYS) if isinstance(data, dict) else ''


def extract_reference_id(data):
    if not isinstance(data, dict):
        return ''
    ext = data.get('ext_info')
    if isinstance(ext, dict):
        ref = _first_str(ext, _REFERENCE_ID_KEYS)
        if ref:
            return ref
    ref = _first_str(data, _REFERENCE_ID_KEYS)
    if ref:
        return ref
    for key in ('response', 'data'):
        ref = extract_reference_id(data.get(key))
        if ref:
            return ref
    return ''
