"""内置插件共用的轻量工具。"""

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def mask_id(value: object, visible: int = 3, mask: str = '****') -> str:
    """隐藏标识符中间部分，并保留两端指定数量的字符。"""
    text = str(value or '')
    if visible <= 0:
        return mask if text else ''
    return text if len(text) <= visible * 2 else f'{text[:visible]}{mask}{text[-visible:]}'


def load_json(path: str | os.PathLike[str], default: Any = None) -> Any:
    """读取 JSON 文件；文件缺失或内容无效时返回默认值。"""
    fallback = {} if default is None else default
    try:
        with Path(path).open(encoding='utf-8') as file:
            return json.load(file)
    except (OSError, TypeError, ValueError):
        return fallback


def save_json(path: str | os.PathLike[str], data: Any) -> None:
    """以原子替换方式写入 JSON，避免进程中断时留下不完整文件。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            'w',
            encoding='utf-8',
            dir=target.parent,
            prefix=f'.{target.name}.',
            suffix='.tmp',
            delete=False,
        ) as file:
            temporary = Path(file.name)
            json.dump(data, file, ensure_ascii=False, indent=2)
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
