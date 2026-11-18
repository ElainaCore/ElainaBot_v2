"""pip 依赖安装辅助 — 插件/模块共用, 自动适配环境并多镜像兜底"""

import asyncio
import importlib
import importlib.metadata as _metadata
import importlib.util
import os
import re
import subprocess
import sys
import threading

from core.base.logger import FRAMEWORK, get_logger

log = get_logger(FRAMEWORK, '依赖安装')

# site-packages 不可写时, 依赖 --target 装到此可写目录并注入 sys.path
_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DEPS_DIR = os.path.join(_ROOT_DIR, '.pydeps')

def _pip_setting(key, default):
    # main.py 引导阶段可能连 PyYAML 都没有，此文件必须可以仅用标准库导入。
    try:
        from core.base.config import cfg
    except ImportError:
        return default
    if cfg._ready:
        return cfg.get('settings', f'pip.{key}', default)

    import yaml

    path = os.path.join(_ROOT_DIR, 'config', 'settings.yaml')
    try:
        with open(path, encoding='utf-8') as f:
            settings = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return default
    except (OSError, yaml.YAMLError) as exc:
        log.warning(f'无法读取依赖安装设置: {exc}')
        return default
    settings = cfg._resolve_env_vars_recursive(settings)
    pip_settings = settings.get('pip') if isinstance(settings, dict) else None
    return pip_settings.get(key, default) if isinstance(pip_settings, dict) else default


def _subprocess_env():
    """子进程使用与当前进程一致的依赖搜索路径，包括免 root 的 .pydeps。"""
    env = os.environ.copy()
    if _DEPS_DIR in sys.path:
        env['PYTHONPATH'] = os.pathsep.join(filter(None, (_DEPS_DIR, env.get('PYTHONPATH'))))
    return env


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
                                'read-only file system', 'access is denied', 'externally-managed-environment'))


def _pip_install_sync(cmd, name, deps_summary=''):
    """同步执行 pip install (独立线程池中运行), 返回 (returncode, stderr)。"""
    log.info(f'[{name}] 正在安装依赖: {deps_summary}' if deps_summary else f'[{name}] 正在安装依赖...')
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace',
                                timeout=300, env=_subprocess_env())
    except subprocess.TimeoutExpired:
        return 124, 'pip 安装超时 (>300s)'
    stderr = (result.stderr or '').strip()
    return result.returncode, stderr


_REQ_RE = re.compile(r'^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*([<>=!~;@].*)?$')


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
    name, rest = m.group(1), (m.group(2) or '').split(';', 1)[0]
    vm = re.search(r'([0-9]+(?:\.[0-9]+)*)', rest)
    try:
        ver = tuple(int(x) for x in vm.group(1).split('.')) if vm else ()
    except ValueError:
        ver = ()
    return name, ver, s


def _merge_requirements(req_files):
    """只对简单的最低版本声明取最大值，其余声明保留原始约束。"""
    specs = {}
    lower_bounds = {}
    for fp in req_files:
        with open(fp, encoding='utf-8-sig') as f:
            for raw in f:
                # URL 中的 #fragment 不能作为注释删除。
                spec = re.split(r'\s+#', raw.strip(), maxsplit=1)[0].strip()
                if not spec or spec.startswith('#'):
                    continue
                if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*\s*>=\s*\d+(?:\.\d+)*', spec):
                    name, version, _ = _parse_req(spec)
                    key = _norm_pkg(name)
                    if key not in lower_bounds or version > lower_bounds[key][0]:
                        lower_bounds[key] = (version, spec)
                        specs[key] = spec
                else:
                    specs[(spec,)] = spec
    return list(specs.values())


def _discover_req_files(target_dir):
    """目录下所有依赖清单: requirements.txt + *_requirements.txt。"""
    try:
        names = sorted(os.listdir(target_dir))
    except OSError:
        return []
    return [os.path.join(target_dir, f) for f in names
            if (f == 'requirements.txt' or f.endswith('_requirements.txt'))
            and os.path.isfile(os.path.join(target_dir, f))]


def _missing_specs(specs):
    """只按发行包名检查是否已安装。其他清单语法交给 pip 处理。"""
    missing = []
    for spec in specs:
        parsed = _parse_req(spec)
        if parsed is None or ' --' in spec:
            return None
        try:
            _metadata.distribution(parsed[0])
        except _metadata.PackageNotFoundError:
            missing.append(spec)
    return missing


def all_requirements_met(req_path):
    """检查单份 requirements.txt 中所有包是否已安装 (纯本地)。"""
    return os.path.isfile(req_path) and _missing_specs(_merge_requirements([req_path])) == []


def _mirror_attempts():
    """None 表示沿用 pip 配置，空字符串表示显式切换到官方源。"""
    mirrors = _pip_setting('mirrors', None)
    if isinstance(mirrors, (list, tuple)) and mirrors:
        seq = [str(m).strip() for m in mirrors if str(m).strip()]
    else:
        one = str(_pip_setting('mirror', '') or os.environ.get('ELAINABOT_PIP_MIRROR', '')).strip()
        seq = [one] if one else []
    return list(dict.fromkeys([*(seq or [None]), '']))


def _build_cmd(requirement_args, mirror, no_cache, target=None):
    cmd = [sys.executable, '-m', 'pip', 'install', '--quiet', '--no-input', '--disable-pip-version-check']
    if no_cache:
        cmd.append('--no-cache-dir')
    if target:
        # 装到可写目录 (免 root); --upgrade 让已存在的旧文件被覆盖, 避免 target 冲突报错
        cmd.extend(['--target', target, '--upgrade'])
    if mirror is not None:
        cmd.extend(['-i', mirror or 'https://pypi.org/simple'])
    return cmd + requirement_args


def _install_requirements_sync(name, target_dir, no_cache):
    req_files = _discover_req_files(target_dir)
    if not req_files:
        return True
    specs = _merge_requirements(req_files)
    missing = _missing_specs(specs)
    if missing == []:
        return True
    if not _pip_setting('auto_install', True):
        log.warning(f'[{name}] 依赖未满足，但 pip.auto_install 已关闭，请手动安装: {", ".join(missing or specs)}')
        return False
    if missing is None:
        # -r/-c、相对路径等 pip 专用语法需要使用原始文件。
        requirement_args = [arg for path in req_files for arg in ('-r', path)]
        return _install_packages(name, requirement_args, specs, no_cache)
    return _install_packages(name, missing, missing, no_cache)


def _install_packages(name, requirement_args, specs, no_cache):
    if importlib.util.find_spec('pip') is None:
        code, stderr = _pip_install_sync([sys.executable, '-m', 'ensurepip', '--upgrade'], name, 'pip')
        if code:
            log.warning(f'[{name}] 当前 Python 缺少 pip，自动初始化失败: {stderr[-1000:]}')
            return False
        importlib.invalidate_caches()

    deps_summary = ', '.join(specs)
    mirrors = _mirror_attempts()

    def _try(target=None):
        """按镜像顺序尝试安装; 返回 (成功?, 最后错误, 是否疑似权限问题)。"""
        last, perm = '', False
        for mirror in mirrors:
            cmd = _build_cmd(requirement_args, mirror, no_cache, target=target)
            source = '默认源' if mirror is None else mirror or '官方源'
            tag = f'{source}{" →.pydeps" if target else ""}'
            try:
                rc, stderr = _pip_install_sync(cmd, name, f'{deps_summary} (源: {tag})')
            except Exception as e:
                last = str(e)
                log.warning(f'[{name}] 依赖安装异常 (源: {tag}): {e}')
                continue
            if rc == 0:
                importlib.invalidate_caches()
                return True, '', False
            last = stderr
            perm = perm or _looks_like_permission_error(stderr)
            if stderr:
                log.warning(f'[{name}] pip 失败 (源: {tag}): {stderr[:200]}')
        return False, last, perm

    ok, last_stderr, perm = _try()
    # site-packages 不可写 → 改装到可写的 .pydeps 并注入 sys.path。
    if not ok and perm:
        os.makedirs(_DEPS_DIR, exist_ok=True)
        _ensure_deps_dir_on_path()
        log.info(f'[{name}] site-packages 不可写, 依赖改装到 {_DEPS_DIR} (免 root)...')
        ok, last_stderr, _ = _try(target=_DEPS_DIR)
    if ok:
        return True
    log.warning(f'[{name}] 依赖安装失败, 已尝试全部镜像/兜底; 最后错误: {last_stderr[:1000]}')
    return False


async def install_requirements(name, target_dir, *, skip_if_met=True, no_cache=False):
    """只补装缺失的依赖，保留 skip_if_met 参数以兼容现有模块。"""
    return await asyncio.get_running_loop().run_in_executor(_executor(), _install_requirements_sync, name, target_dir, no_cache)


def ensure_framework_dependencies():
    """在导入 Application 及其第三方依赖前引导框架运行环境。"""
    if not _install_requirements_sync('框架', _ROOT_DIR, False):
        raise RuntimeError('框架依赖未就绪，请检查上方依赖安装日志')
