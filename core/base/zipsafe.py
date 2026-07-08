"""zip 安全解压 — 防 zip-slip (路径穿越/绝对路径成员)"""

import os
import zipfile


def is_within(base_dir: str, target: str) -> bool:
    base = os.path.realpath(base_dir)
    real = os.path.realpath(target)
    return real == base or real.startswith(base + os.sep)


def safe_extractall(zf: zipfile.ZipFile, dest_dir: str) -> None:
    """替代 ZipFile.extractall, 逐成员校验目标不逃逸 dest_dir, 越界抛 ValueError"""
    dest_dir = os.path.realpath(dest_dir)
    for member in zf.infolist():
        target = os.path.join(dest_dir, member.filename.replace('\\', '/'))
        if not is_within(dest_dir, target):
            raise ValueError(f'非法压缩包成员路径 (疑似路径穿越): {member.filename!r}')
        if member.is_dir():
            os.makedirs(target, exist_ok=True)
        else:
            os.makedirs(os.path.dirname(target) or dest_dir, exist_ok=True)
            with zf.open(member) as src, open(target, 'wb') as dst:
                dst.write(src.read())
