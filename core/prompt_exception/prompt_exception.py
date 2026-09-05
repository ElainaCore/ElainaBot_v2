from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar


class PromptException:
    db: ClassVar[dict[int, PromptException]] = {}

    def __init__(self, msg: object, code: int = 500, data: dict | None = None, as_tpl: bool = False) -> None:
        self.msg = str(getattr(msg, 'value', msg))
        self.data = data or {}
        self.code = code
        if as_tpl is True:
            self.db[code] = self

    def __str__(self) -> str:
        return self.msg

    @property
    def value(self) -> str:
        return self.msg

    def __repr__(self) -> str:
        return f'PExp[{self.code}]{self.msg}'

    def format_msg(self, args: Mapping[str, object] | None) -> str:
        if args is None:
            return self.msg
        return self.msg.format_map(args)

    def to_dict(self, args: Mapping[str, object] | None = None) -> dict[str, object]:
        r: dict[str, object] = {
            'code': self.code,
            'msg': self.format_msg(args),
        }
        if self.data:
            r['data'] = self.data
        return r

    def d(self, args: Mapping[str, object] | None = None, data: dict | None = None) -> PromptException:
        "创建实例"
        tpl = self.db.get(self.code) or self
        val = tpl.format_msg(args)
        return PromptException(val, tpl.code, data or self.data)
