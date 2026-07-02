from __future__ import annotations
from typing import ClassVar


class PromptException:
    db: ClassVar[dict[int, PromptException]] = {}

    def __init__(self, msg: str, code: int = 500, data: dict = None, as_tpl: bool = False) -> None:
        if hasattr(msg, 'value'):
            self.msg = msg.value
        else:
            self.msg = str(msg)
        self.data = data or {}
        self.code = code
        if as_tpl is True:
            self.db[code] = self

    def __str__(self) -> str:
        return self.msg

    @property
    def value(self):
        return self.msg

    def __repr__(self) -> str:
        return f'PExp[{self.code}]{self.msg}'

    def format_msg(self, args: dict[str, str]):
        val = str(self.msg)
        if args is None:
            return val
        val = val.format_map(args)
        return val

    def to_dict(self, args: dict[str, str] = None):
        r = {
            'code': self.code,
            'msg': self.format_msg(args),
        }
        if self.data:
            r['data'] = self.data
        return r

    def d(self, args: dict[str, str] = None, data: dict = None):
        "创建实例"
        tpl = self.db.get(self.code) or self
        val = tpl.format_msg(args)
        return PromptException(val, tpl.code, data or self.data)
