"""OneBot 11 协议响应构建器 — Builder 模式"""

from __future__ import annotations

from typing import Any


class ResponseBuilder:
    """OneBot 11 协议响应构建器 (Builder 模式)"""

    @staticmethod
    def ok(data=None, echo=None) -> dict:
        r: dict[str, Any] = {'status': 'ok', 'retcode': 0, 'data': data or {}}
        if echo is not None:
            r['echo'] = echo
        return r

    @staticmethod
    def fail(msg='', echo=None, retcode=1) -> dict:
        r: dict[str, Any] = {
            'status': 'failed',
            'retcode': retcode,
            'data': None,
            'msg': msg,
            'wording': msg,
        }
        if echo is not None:
            r['echo'] = echo
        return r
