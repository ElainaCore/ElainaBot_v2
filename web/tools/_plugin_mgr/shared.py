"""共享状态 / 路径校验 / 入口探测"""

import logging
import os

from aiohttp import web

from web.tools._zipsafe import is_within

log = logging.getLogger('ElainaBot.web.plugin_mgr')

# ==================== 全局状态 (由 plugin_manager.set_context 注入) ====================

_state: dict[str, object] = {'base_dir': '', 'bot_manager': None}


def set_context(base_dir: str, bot_manager=None):
    _state['base_dir'] = base_dir
    if bot_manager is not None:
        _state['bot_manager'] = bot_manager


def base_dir() -> str:
    return str(_state['base_dir'])


def bot_manager():
    return _state['bot_manager']


def plugins_dir() -> str:
    return os.path.join(str(_state['base_dir']), 'plugins')


def modules_dir() -> str:
    return os.path.join(str(_state['base_dir']), 'modules')


def get_pm():
    """获取 PluginManager 实例 (无则返回 None)"""
    bm = _state['bot_manager']
    if not bm:
        return None
    return getattr(bm, '_plugin_manager', None) or getattr(bm, 'plugin_manager', None)


def get_mm():
    """获取 ModuleManager 实例 (无则返回 None)"""
    bm = _state['bot_manager']
    return getattr(bm, 'module_manager', None) if bm else None


# ==================== 路径校验 ====================


def validate_path(path, base):
    abs_p = os.path.abspath(path)
    return is_within(base, abs_p), abs_p


def validate_config_path(raw_path):
    """校验配置路径在 modules/ 或 plugins/ 下, 返回 (abs_path, error_response)"""
    abs_path = os.path.abspath(os.path.normpath(raw_path))
    if not any(is_within(d, abs_path) for d in (modules_dir(), plugins_dir())):
        return None, web.json_response({'success': False, 'message': '无效路径'}, status=403)
    return abs_path, None


# ==================== 插件入口探测 ====================

ENTRY_CANDIDATES = ('index.py', 'app.py', 'main.py')


def find_entry(plugin_dir):
    """查找插件入口文件 (与 PluginManager._find_large_entry 一致)"""
    for name in ENTRY_CANDIDATES:
        path = os.path.join(plugin_dir, name)
        if os.path.isfile(path):
            return path
    return None


# ==================== 配置文件格式检测 ====================

CONFIG_EXTS = frozenset(
    {
        '.yaml',
        '.yml',
        '.json',
        '.toml',
        '.ini',
        '.cfg',
        '.conf',
        '.txt',
        '.md',
        '.backup',
    }
)

_FORMAT_MAP = {
    '.yaml': 'yaml',
    '.yml': 'yaml',
    '.json': 'json',
    '.toml': 'toml',
    '.ini': 'ini',
    '.cfg': 'ini',
    '.conf': 'ini',
    '.txt': 'text',
    '.log': 'text',
    '.md': 'text',
}


def detect_config_format(ext):
    return _FORMAT_MAP.get(ext, 'raw')


def list_config_files(data_dir):
    """列出 data/ 下可编辑配置文件 (排除 .db 等)"""
    files = []
    if not os.path.isdir(data_dir):
        return files
    for fname in sorted(os.listdir(data_dir)):
        fpath = os.path.join(data_dir, fname)
        if not os.path.isfile(fpath):
            continue
        ext = os.path.splitext(fname)[1].lower()
        if ext not in CONFIG_EXTS:
            continue
        files.append(
            {
                'name': fname,
                'path': fpath.replace('\\', '/'),
                'format': detect_config_format(ext),
                'size': os.path.getsize(fpath),
            }
        )
    return files
