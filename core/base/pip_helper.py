"""pip 依赖安装辅助 — 插件/模块共用

设计目标 (对齐 nonebot 的"框架自装依赖", 且不挑环境):
  · 用 `sys.executable -m pip` 安装 → 天然装进"当前跑 bot 的这个 Python", 虚拟环境里也对;
  · site-packages 不可写时 (无 root / venv 属主为 root), 自动 `--target` 装到可写的
    .pydeps 目录并注入 sys.path, 无需 root, venv 内外均适用;
  · 多镜像兜底 (配置源失败自动换官方源重试);
  · pip 跑在独立单线程池里, 与事件循环 / DNS / Web 隔离, 装依赖不阻塞收发消息。
"""

import asyncio
import importlib
import importlib.metadata as _metadata
import os
import re
import subprocess
import sys
import threading

from core.base.config import cfg
from core.base.logger import FRAMEWORK, get_logger

log = get_logger(FRAMEWORK, '依赖安装')

# 当运行 bot 的 Python 环境 site-packages 不可写时 (无 root / venv 属主为 root, pip 报
# Errno 13 Permission denied), 依赖改装到这个可写目录并注入 sys.path: 免 root、
# venv 内外都能让插件 import 到 (venv 里 --user 会被 pip 拒绝, 故用 --target)。
_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DEPS_DIR = os.path.join(_ROOT_DIR, '.pydeps')


def _ensure_deps_dir_on_path():
    if _DEPS_DIR not in sys.path:
        sys.path.insert(0, _DEPS_DIR)
    importlib.invalidate_caches()


if os.path.isdir(_DEPS_DIR):
    _ensure_deps_dir_on_path()

# 独立单线程池: pip 是重 IO/CPU 的阻塞操作, 放这里跑绝不占用 asyncio 默认执行器
# (默认执行器还兼着 DNS 解析、Web/统计查询等, 被 pip 占满会连带卡住整个面板)。
_EXECUTOR = None
_EXECUTOR_LOCK = threading.Lock()


def _executor():
    global _EXECUTOR
    if _EXECUTOR is None:
        with _EXECUTOR_LOCK:
            if _EXECUTOR is None:
                from concurrent.futures import ThreadPoolExecutor

                _EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix='pip-install')
    return _EXECUTOR


def _looks_like_permission_error(stderr):
    s = (stderr or '').lower()
    return any(k in s for k in ('permission denied', 'errno 13', 'could not install packages',
                                'consider using the `--user`', "consider using the '--user'",
                                'read-only file system', 'access is denied'))


def _pip_install_sync(cmd, name, deps_summary=''):
    """同步执行 pip install (独立线程池中运行), 返回 (returncode, stderr)。"""
    log.info(f'[{name}] 正在安装依赖: {deps_summary}' if deps_summary else f'[{name}] 正在安装依赖...')
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return 124, 'pip 安装超时 (>300s)'
    stderr = (result.stderr or '').strip()
    return result.returncode, stderr


def all_requirements_met(req_path):
    """检查 requirements.txt 中所有包是否已安装 (纯本地, 不联网)。"""
    try:
        with open(req_path, encoding='utf-8') as f:
            lines = f.readlines()
    except Exception:
        return False
    for raw in lines:
        pkg = re.split(r'[>=<!\[;]', raw.strip())[0].strip()
        if not pkg or pkg.startswith(('#', '-')):
            continue
        try:
            _metadata.distribution(pkg)
        except _metadata.PackageNotFoundError:
            return False
    return True


def _mirror_attempts():
    """返回按优先级排列的镜像参数列表; '' 表示官方源 (不带 -i)。

    支持 settings.pip.mirrors (列表) 做多镜像兜底; 否则用 settings.pip.mirror
    (单个), 并追加官方源兜底。
    """
    mirrors = cfg.get('settings', 'pip.mirrors', None)
    if isinstance(mirrors, (list, tuple)) and mirrors:
        seq = [str(m).strip() for m in mirrors if str(m).strip()]
    else:
        one = str(cfg.get('settings', 'pip.mirror', '') or '').strip()
        seq = [one] if one else []
    # 追加官方源兜底 (去重, 保序)
    if '' not in seq:
        seq.append('')
    out, seen = [], set()
    for m in seq:
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out


def _build_cmd(req_path, mirror, no_cache, target=None):
    cmd = [sys.executable, '-m', 'pip', 'install', '-r', req_path, '--quiet',
           '--disable-pip-version-check']
    if no_cache:
        cmd.append('--no-cache-dir')
    if target:
        # 装到可写目录 (免 root); --upgrade 让已存在的旧文件被覆盖, 避免 target 冲突报错
        cmd.extend(['--target', target, '--upgrade'])
    if mirror:
        cmd.extend(['-i', mirror])
    return cmd


async def install_requirements(name, target_dir, *, skip_if_met=False, no_cache=False):
    """检查并安装 requirements.txt 依赖 (venv 原生, 无 root 时 --user 兜底, 多镜像兜底)。

    全程在独立线程池里跑, 不阻塞事件循环。
    """
    req_path = os.path.join(target_dir, 'requirements.txt')
    if not os.path.isfile(req_path):
        return
    if not cfg.get('settings', 'pip.auto_install', True):
        return
    if skip_if_met and all_requirements_met(req_path):
        return

    try:
        with open(req_path, encoding='utf-8') as f:
            deps = [line.strip() for line in f if line.strip() and not line.startswith(('#', '-'))]
        deps_summary = ', '.join(deps)
    except Exception:
        deps_summary = ''

    loop = asyncio.get_running_loop()
    mirrors = _mirror_attempts()

    async def _try(target=None):
        """按镜像顺序尝试安装; 返回 (成功?, 最后错误, 是否疑似权限问题)。"""
        last, perm = '', False
        for mirror in mirrors:
            cmd = _build_cmd(req_path, mirror, no_cache, target=target)
            tag = f'{mirror or "官方源"}{" →.pydeps" if target else ""}'
            try:
                rc, stderr = await loop.run_in_executor(
                    _executor(), _pip_install_sync, cmd, name,
                    f'{deps_summary} (源: {tag})' if deps_summary else '')
            except Exception as e:
                last = str(e)
                log.warning(f'[{name}] 依赖安装异常 (源: {tag}): {e}')
                continue
            if rc == 0:
                return True, '', False
            last = stderr
            perm = perm or _looks_like_permission_error(stderr)
            if stderr:
                log.warning(f'[{name}] pip 失败 (源: {tag}): {stderr[:200]}')
        return False, last, perm

    ok, last_stderr, perm = await _try()
    if ok:
        return
    # site-packages 不可写 (无 root / venv 属主为 root) → 改装到可写的 .pydeps 并注入 sys.path
    if perm:
        os.makedirs(_DEPS_DIR, exist_ok=True)
        _ensure_deps_dir_on_path()
        log.info(f'[{name}] site-packages 不可写, 依赖改装到 {_DEPS_DIR} (免 root)...')
        ok, last_stderr, _ = await _try(target=_DEPS_DIR)
        if ok:
            return

    log.warning(f'[{name}] 依赖安装失败, 已尝试全部镜像/兜底; 最后错误: {last_stderr[:200]}')
