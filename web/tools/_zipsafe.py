"""Web 面板 zip 安全解压工具。"""

import os
import shutil
import tempfile
import zipfile


def is_within(base_dir: str, target: str) -> bool:
    base = os.path.realpath(base_dir)
    real = os.path.realpath(target)
    return real == base or real.startswith(base + os.sep)


def safe_extractall(zf: zipfile.ZipFile, dest_dir: str) -> None:
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


def replace_dir_from_zip(zf: zipfile.ZipFile, target_dir: str, top_dir: str = '') -> None:
    """安全解压 zip 到 target_dir (已存在先备份为 .bak); top_dir 非空表示 zip 内为单一顶层目录, 解压后将其内容移动到 target_dir"""
    if os.path.exists(target_dir):
        backup = target_dir + '.bak'
        if os.path.exists(backup):
            shutil.rmtree(backup)
        shutil.move(target_dir, backup)
    if top_dir:
        extract_tmp = tempfile.mkdtemp()
        safe_extractall(zf, extract_tmp)
        shutil.move(os.path.join(extract_tmp, top_dir), target_dir)
        shutil.rmtree(extract_tmp, ignore_errors=True)
    else:
        os.makedirs(target_dir, exist_ok=True)
        safe_extractall(zf, target_dir)
