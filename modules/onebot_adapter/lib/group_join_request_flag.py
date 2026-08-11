"""QBot group join request details carried by the OneBot request flag."""

from __future__ import annotations

import base64
import json
from typing import Any


class GroupJoinRequestFlagCodec:
    """Encode the platform request identifiers into an opaque OneBot flag."""

    PREFIX = 'qbot-group-join:'

    @classmethod
    def encode(cls, group_openid: str, member_openid: str, join_request_id: str = '') -> str:
        payload = {
            'group_openid': str(group_openid or ''),
            'member_openid': str(member_openid or ''),
            'join_request_id': str(join_request_id or ''),
        }
        raw = json.dumps(payload, ensure_ascii=True, separators=(',', ':')).encode()
        token = base64.urlsafe_b64encode(raw).decode().rstrip('=')
        return f'{cls.PREFIX}{token}'

    @classmethod
    def decode(cls, flag: Any) -> dict[str, str] | None:
        if not isinstance(flag, str) or not flag.startswith(cls.PREFIX):
            return None
        token = flag[len(cls.PREFIX) :]
        if not token:
            return None
        try:
            padding = '=' * (-len(token) % 4)
            payload = json.loads(base64.urlsafe_b64decode(token + padding))
        except (ValueError, TypeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        group_openid = payload.get('group_openid')
        member_openid = payload.get('member_openid')
        join_request_id = payload.get('join_request_id') or ''
        if not isinstance(group_openid, str) or not group_openid:
            return None
        if not isinstance(member_openid, str) or not member_openid:
            return None
        if not isinstance(join_request_id, str):
            return None
        return {
            'group_openid': group_openid,
            'member_openid': member_openid,
            'join_request_id': join_request_id,
        }
