"""运行环境依赖版本检查 — 对照 pyproject.toml 声明范围"""

import contextlib
import os
import platform
import re
from importlib import metadata

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11 无 tomllib, 降级正则解析
    tomllib = None

from aiohttp import web

_base_dir = ''
_cache: dict | None = None


def set_context(base_dir: str):
    global _base_dir, _cache
    _base_dir = base_dir
    _cache = None


def _ver_tuple(v: str) -> tuple:
    """版本号转可比较的整数元组 (忽略非数字后缀)"""
    parts = []
    for p in v.split('.'):
        m = re.match(r'\d+', p)
        if not m:
            break
        parts.append(int(m.group()))
    return tuple(parts) or (0,)


def _cmp(a: str, b: str) -> int:
    ta, tb = _ver_tuple(a), _ver_tuple(b)
    n = max(len(ta), len(tb))
    ta += (0,) * (n - len(ta))
    tb += (0,) * (n - len(tb))
    return (ta > tb) - (ta < tb)


_SPEC_RE = re.compile(r'(>=|<=|==|!=|>|<|~=)\s*([\w.]+)')


def check_specifiers(installed: str, spec: str) -> str:
    """对照版本范围: 返回 ok / low / high"""
    for op, ver in _SPEC_RE.findall(spec):
        c = _cmp(installed, ver)
        if op in ('>=', '~=') and c < 0:
            return 'low'
        if op == '>' and c <= 0:
            return 'low'
        if op == '<=' and c > 0:
            return 'high'
        if op == '<' and c >= 0:
            return 'high'
        if op == '==' and c != 0:
            return 'low' if c < 0 else 'high'
    return 'ok'


_DEP_RE = re.compile(r'^([A-Za-z0-9_.-]+)\s*(\[[^\]]*\])?\s*(.*)$')


def _parse_pyproject(path: str) -> tuple[str, list[str]]:
    """返回 (requires-python, 依赖声明列表)"""
    if tomllib:
        with open(path, 'rb') as f:
            project = tomllib.load(f).get('project', {})
        return project.get('requires-python', ''), list(project.get('dependencies', []))
    # 降级: 正则提取 requires-python 与 dependencies 块中的字符串
    with open(path, encoding='utf-8') as f:
        text = f.read()
    m = re.search(r'requires-python\s*=\s*["\']([^"\']+)["\']', text)
    requires_python = m.group(1) if m else ''
    deps: list[str] = []
    m = re.search(r'^dependencies\s*=\s*\[(.*?)\]', text, re.S | re.M)
    if m:
        deps = re.findall(r'["\']([^"\']+)["\']', m.group(1))
    return requires_python, deps


def _collect() -> dict:
    """读取 pyproject.toml 声明并对照已安装版本"""
    pyproject = os.path.join(_base_dir, 'pyproject.toml')
    requires_python = ''
    declared: list[tuple[str, str]] = []
    with contextlib.suppress(Exception):
        requires_python, dep_strs = _parse_pyproject(pyproject)
        for dep in dep_strs:
            m = _DEP_RE.match(dep.strip())
            if m:
                declared.append((m.group(1), m.group(3).strip()))

    py_ver = platform.python_version()
    py_status = check_specifiers(py_ver, requires_python) if requires_python else 'ok'

    deps = []
    for name, spec in declared:
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            deps.append({'name': name, 'installed': '', 'required': spec, 'status': 'missing'})
            continue
        status = check_specifiers(installed, spec) if spec else 'ok'
        deps.append({'name': name, 'installed': installed, 'required': spec, 'status': status})

    return {
        'python': {'version': py_ver, 'required': requires_python, 'status': py_status},
        'dependencies': deps,
    }


async def handle_dependencies(request: web.Request):
    global _cache
    if _cache is None:
        _cache = _collect()
    return web.json_response(_cache)
