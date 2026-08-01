"""pip 依赖安装辅助 — 插件/模块共用, 自动适配环境并多镜像兜底"""

import asyncio
import contextlib
import importlib
import importlib.metadata as _metadata
import os
import re
import subprocess
import sys
import tempfile
import threading

from core.base.config import cfg
from core.base.logger import FRAMEWORK, get_logger

log = get_logger(FRAMEWORK, '依赖安装')

# site-packages 不可写时, 依赖 --target 装到此可写目录并注入 sys.path
_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DEPS_DIR = os.path.join(_ROOT_DIR, '.pydeps')


def _ensure_deps_dir_on_path():
    if _DEPS_DIR not in sys.path:
        sys.path.insert(0, _DEPS_DIR)
    importlib.invalidate_caches()


if os.path.isdir(_DEPS_DIR):
    _ensure_deps_dir_on_path()

# 独立单线程池: pip 阻塞操作不占用 asyncio 默认执行器
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


_REQ_RE = re.compile(r'^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*(.*)$')  # 包名 + 可选 extras + 其余


def _norm_pkg(name):
    """PEP 503 规范化包名 (跨清单去重用)。"""
    return re.sub(r'[-_.]+', '-', name).lower()


def _parse_req(line):
    """解析一行依赖 → (包名, 版本元组, 原始 spec); 无效行返回 None。"""
    s = line.split('#', 1)[0].strip()
    if not s or s.startswith('-'):
        return None
    m = _REQ_RE.match(s)
    if not m:
        return None
    name, rest = m.group(1), m.group(2).split(';', 1)[0]
    vm = re.search(r'([0-9]+(?:\.[0-9]+)*)', rest)
    try:
        ver = tuple(int(x) for x in vm.group(1).split('.')) if vm else ()
    except ValueError:
        ver = ()
    return name, ver, s


def _merge_requirements(req_files):
    """合并多份依赖清单, 同包版本冲突取最高版本, 保持首次出现顺序。"""
    best, order = {}, []
    for fp in req_files:
        try:
            with open(fp, encoding='utf-8') as f:
                lines = f.readlines()
        except OSError:
            continue
        for raw in lines:
            parsed = _parse_req(raw)
            if not parsed:
                continue
            name, ver, spec = parsed
            key = _norm_pkg(name)
            if key not in best:
                best[key] = (ver, spec)
                order.append(key)
            elif ver > best[key][0]:
                best[key] = (ver, spec)
    return [best[k][1] for k in order]


def _discover_req_files(target_dir):
    """目录下所有依赖清单: requirements.txt + *_requirements.txt。"""
    try:
        names = sorted(os.listdir(target_dir))
    except OSError:
        return []
    return [os.path.join(target_dir, f) for f in names
            if (f == 'requirements.txt' or f.endswith('_requirements.txt'))
            and os.path.isfile(os.path.join(target_dir, f))]


def _specs_met(specs):
    """检查依赖 spec 是否都已安装 (纯本地)。"""
    for spec in specs:
        parsed = _parse_req(spec)
        if not parsed:
            continue
        try:
            _metadata.distribution(parsed[0])
        except _metadata.PackageNotFoundError:
            return False
    return True


def all_requirements_met(req_path):
    """检查单份 requirements.txt 中所有包是否已安装 (纯本地)。"""
    return _specs_met(_merge_requirements([req_path]))


def _mirror_attempts():
    """返回按优先级排列的镜像参数列表, '' 表示官方源"""
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
    """安装目录下所有依赖清单 (合并后装齐, venv原生/.pydeps免root/多镜像兜底, 独立线程池不阻塞)。"""
    if not cfg.get('settings', 'pip.auto_install', True):
        return
    req_files = _discover_req_files(target_dir)
    if not req_files:
        return
    specs = _merge_requirements(req_files)
    if not specs:
        return
    if skip_if_met and _specs_met(specs):
        return
    deps_summary = ', '.join(specs)

    loop = asyncio.get_running_loop()
    mirrors = _mirror_attempts()
    # 合并结果写临时清单交给 pip, 不污染插件目录
    fd, req_path = tempfile.mkstemp(prefix='elaina-req-', suffix='.txt')
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write('\n'.join(specs) + '\n')

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

    try:
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
    finally:
        with contextlib.suppress(OSError):
            os.remove(req_path)
