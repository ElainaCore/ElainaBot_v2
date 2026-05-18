"""载荷转换 — Strategy 模式

将不同格式的消息载荷 (str / text dict / markdown dict) 转换为
send_to_group/send_to_user 所需的 kwargs。
"""

from __future__ import annotations

from typing import Any

from core.message._http import MessageType


class PayloadConverter:
    """载荷转换策略: 将不同格式的消息载荷转换为发送参数

    支持的输入类型:
      - 纯文本 (str)
      - OneBot 文本消息 (dict: {type: 'text', data: {text: ...}})
      - OneBot Markdown 消息 (dict: {type: 'markdown', data: ...})
    """

    @staticmethod
    def convert(payload: str | dict) -> dict[str, Any]:
        """转换载荷为 kwargs dict"""
        if isinstance(payload, str):
            return {'content': payload}
        if not isinstance(payload, dict):
            return {'content': str(payload)}

        msg_type = payload.get('type')
        if msg_type == 'text':
            return {'content': str(payload.get('data', {}).get('text', ''))}
        if msg_type == 'markdown':
            return PayloadConverter._convert_markdown(payload)
        return {'content': str(payload)}

    @staticmethod
    def _convert_markdown(payload: dict) -> dict[str, Any]:
        data = dict(payload.get('data', {}).get('data', {}))
        # keyboard 结构扁平化
        if 'keyboard' in data:
            buttons = data.pop('keyboard')
            data['buttons'] = (
                (buttons.get('rows') or [])
                if isinstance(buttons, dict)
                else (buttons or [])
            )
        # markdown 子字段提升到顶层
        if 'markdown' in data:
            md = data.pop('markdown')
            if isinstance(md, dict):
                data.update(md)
        data['msg_type'] = MessageType.MSG_TYPE_MARKDOWN
        return data
