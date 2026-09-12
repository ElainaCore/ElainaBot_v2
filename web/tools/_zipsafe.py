"""Web 面板 zip 安全解压工具。"""

import os
import shutil
import stat
import tempfile
import zipfile


def is_within(base_dir: str, target: str) -> bool:
    base = os.path.realpath(base_dir)
    real = os.path.realpath(target)
    return real == base or real.startswith(base + os.sep)


def is_runtime_cache(path: str) -> bool:
    """判断压缩包成员或文件路径是否属于 Python/前端运行时缓存。"""
    parts = [part for part in path.replace('\\', '/').split('/') if part]
    return any(part in {'__pycache__', 'node_modules'} for part in parts) or (
        bool(parts) and parts[-1].lower().endswith(('.pyc', '.pyo'))
    )


def _validate_member_name(member_name: str) -> None:
    """拒绝绝对路径和显式 .. 成员，避免压缩包路径穿越。"""
    normalized = member_name.replace('\\', '/')
    if normalized.startswith('/') or os.path.isabs(normalized):
        raise ValueError(f'非法压缩包成员路径 (绝对路径): {member_name!r}')
    if any(part == '..' for part in normalized.split('/')):
        raise ValueError(f'非法压缩包成员路径 (疑似路径穿越): {member_name!r}')


def _remove_readonly(func, path, _exc_info):
    """shutil.rmtree 的 Windows 只读文件回调。"""
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        func(path)
    except FileNotFoundError:
        return


def remove_path(path: str | None, *, ignore_errors: bool = False) -> None:
    """删除文件或目录，自动处理只读文件；可选忽略无法删除的缓存。"""
    if not path or not os.path.lexists(path):
        return
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            # 不把 ignore_errors 直接传给 shutil.rmtree：其为 True 时会跳过
            # onerror 回调，Windows 只读文件便无法先解除属性再重试删除。
            shutil.rmtree(path, onerror=_remove_readonly, ignore_errors=False)
        else:
            try:
                os.unlink(path)
            except PermissionError:
                os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
                os.unlink(path)
    except OSError:
        if not ignore_errors:
            raise


def safe_extractall(zf: zipfile.ZipFile, dest_dir: str) -> None:
    dest_dir = os.path.realpath(dest_dir)
    for member in zf.infolist():
        member_name = member.filename.replace('\\', '/')
        _validate_member_name(member_name)
        # 字节码和运行时缓存属于本机环境，不应覆盖/删除，也可能在 Windows
        # 上被解释器锁定，直接跳过可避免模块更新因 PermissionError 失败。
        target = os.path.join(dest_dir, member_name)
        if not is_within(dest_dir, target):
            raise ValueError(f'非法压缩包成员路径 (疑似路径穿越): {member.filename!r}')
        if is_runtime_cache(member_name):
            continue
        if member.is_dir():
            os.makedirs(target, exist_ok=True)
        else:
            os.makedirs(os.path.dirname(target) or dest_dir, exist_ok=True)
            with zf.open(member) as src, open(target, 'wb') as dst:
                dst.write(src.read())


def replace_dir_from_zip(zf: zipfile.ZipFile, target_dir: str, top_dir: str = '') -> None:
    parent = os.path.dirname(target_dir)
    stage = tempfile.mkdtemp(prefix='__update-', dir=parent)
    backup_dir = backup_target = None
    try:
        safe_extractall(zf, stage)
        source = os.path.join(stage, top_dir) if top_dir else stage
        if top_dir and not os.path.isdir(source):
            raise ValueError(f'压缩包缺少顶层目录: {top_dir!r}')
        if os.path.exists(target_dir):
            backup_dir = tempfile.mkdtemp(prefix='__update-old-', dir=parent)
            backup_target = os.path.join(backup_dir, os.path.basename(os.path.normpath(target_dir)))
            shutil.move(target_dir, backup_target)
        shutil.move(source, target_dir)
    except Exception:
        if backup_target:
            remove_path(target_dir, ignore_errors=True)
            if os.path.exists(backup_target) and not os.path.exists(target_dir):
                shutil.move(backup_target, target_dir)
        raise
    finally:
        remove_path(stage, ignore_errors=True)
        remove_path(backup_dir, ignore_errors=True)
