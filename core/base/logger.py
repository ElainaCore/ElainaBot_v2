#!/usr/bin/env python
"""统一日志系统 — 格式化输出、错误上报"""

import contextlib
import json
import logging
import os
import sys
import traceback
from datetime import datetime

from core.base import console as _console

# ==================== 常量 ====================

PLUGIN = '插件'
FRAMEWORK = '框架'
EXTENSION = '拓展模块'
SERVICE = '服务'
SCHEDULER = '定时'
SYSTEM = '系统'

_FRAMEWORK_NAME = 'ElainaBot'

# 错误回调(由 service/log.py 注册, 写入 SQLite)
_error_callbacks: list = []
# 框架日志回调
_framework_callbacks: list = []


def _now_str():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _fire(callbacks, data):
    for cb in callbacks:
        with contextlib.suppress(Exception):
            cb(data)


def _get_app_callbacks():
    """获取 Application 的回调列表 (惰性导入, 避免循环依赖)"""
    try:
        from core.application import get_app

        app = get_app()
        if app:
            return app._error_callbacks, app._framework_callbacks
    except Exception:
        pass
    return [], []


# ==================== 终端颜色 ====================

_COLORS_ENABLED = False

# ANSI 转义码
_RESET = '\033[0m'
_COLORS = {
    'DEBUG': '\033[90m',  # 灰色
    'INFO': '\033[0m',  # 默认
    'WARNING': '\033[33m',  # 黄色
    'ERROR': '\033[31m',  # 红色
    'CRITICAL': '\033[1;31m',  # 红色加粗
}
_PREFIX_COLOR = '\033[36m'  # 青色 [模块:名称]
_BRAND_COLOR = '\033[35m'  # 紫色 [ElainaBot]
_TIME_COLOR = '\033[90m'  # 灰色 时间


def _enable_colors():
    """启用终端颜色(Windows 10+ 支持 ANSI)"""
    global _COLORS_ENABLED
    if sys.platform == 'win32':
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
            handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
            mode = ctypes.c_ulong()
            kernel32.GetConsoleMode(handle, ctypes.byref(mode))
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
            _COLORS_ENABLED = True
        except Exception:
            _COLORS_ENABLED = False
    else:
        _COLORS_ENABLED = hasattr(sys.stdout, 'isatty') and sys.stdout.isatty()


# ==================== 格式化器 ====================


class ElainaFormatter(logging.Formatter):
    """统一日志格式化器"""

    def format(self, record):
        dt = datetime.fromtimestamp(record.created).strftime('%m-%d %H:%M:%S')
        level = record.levelname.ljust(8)
        prefix = self._extract_prefix(record.name)
        msg = record.getMessage()

        # 异常信息追加
        if record.exc_info and record.exc_info[1]:
            tb = self.formatException(record.exc_info)
            msg = f'{msg}\n{tb}'

        if _COLORS_ENABLED:
            return (
                f'{_BRAND_COLOR}[{_FRAMEWORK_NAME}]{_RESET} '
                f'{_TIME_COLOR}{dt}{_RESET} - '
                f'{_COLORS.get(record.levelname, "")}{level}{_RESET} - '
                f'{_PREFIX_COLOR}{prefix}{_RESET}'
                f'{msg}'
            )
        return f'[{_FRAMEWORK_NAME}] {dt} - {level} - {prefix}{msg}'

    @staticmethod
    def _extract_prefix(name):
        """从 logger 名称提取 [模块类型:模块名] 前缀"""
        parts = name.split('.', 2)
        if len(parts) < 2:
            return ''
        return f'[{parts[1]}:{parts[2]}]' if len(parts) > 2 else f'[{parts[1]}]'


class ElainaFilter(logging.Filter):
    """过滤非 ElainaBot 日志(可选)"""

    def filter(self, record):
        return record.name.startswith('ElainaBot')


# ==================== 初始化 ====================

_initialized = False


def setup(framework_name=None, level=logging.INFO, log_file=None):
    """初始化日志系统 (框架启动时调用一次)"""
    global _FRAMEWORK_NAME, _initialized
    if _initialized:
        return
    _initialized = True

    if framework_name:
        _FRAMEWORK_NAME = framework_name

    _console.install()
    _enable_colors()

    root_logger = logging.getLogger('ElainaBot')
    root_logger.setLevel(level)
    root_logger.handlers.clear()

    # 控制台
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(ElainaFormatter())
    root_logger.addHandler(console)

    # 文件(可选)
    if log_file:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        fh = logging.FileHandler(log_file, encoding='utf-8')
        fh.setFormatter(ElainaFormatter())
        root_logger.addHandler(fh)

    # 抑制第三方库噪音
    for name in ('websockets', 'aiohttp', 'asyncio'):
        logging.getLogger(name).setLevel(logging.WARNING)

    root_logger.info('日志系统初始化完成')


# ==================== 获取日志器 ====================


def get_logger(module_type, module_name):
    """获取模块日志器"""
    return logging.getLogger(f'ElainaBot.{module_type}.{module_name}')


# ==================== 错误上报 ====================


def report_error(module_type, module_name, error, context=None, notify=True):
    """统一错误上报: 捕获 traceback, 输出日志, 通知回调(SQLite 等)"""
    log = get_logger(module_type, module_name)

    if isinstance(error, BaseException):
        tb_str = ''.join(traceback.format_exception(type(error), error, error.__traceback__))
        log.error(f'{error}\n{tb_str}')
    else:
        tb_str = ''
        log.error(str(error))

    if not notify:
        return

    ctx = context or {}
    ts = _now_str()
    err_data = {
        'timestamp': ts,
        'appid': ctx.get('appid', '0000') if isinstance(ctx, dict) else '0000',
        'module_type': module_type,
        'module_name': module_name,
        'content': str(error),
        'traceback': tb_str,
        'context': ctx,
    }
    fw_data = {
        'timestamp': ts,
        'content': f'[{module_type}:{module_name}] {error}',
        'level': 'ERROR',
    }

    _fire(_error_callbacks, err_data)
    _fire(_framework_callbacks, fw_data)

    # Application 级回调
    err_cbs, fw_cbs = _get_app_callbacks()
    _fire(err_cbs, err_data)
    _fire(fw_cbs, fw_data)


def report_framework(module_name, message, level='INFO'):
    """框架事件上报(非错误, 如初始化/状态变更)"""
    log = get_logger(FRAMEWORK, module_name)
    getattr(log, level.lower(), log.info)(message)
    data = {
        'timestamp': _now_str(),
        'content': f'[{FRAMEWORK}:{module_name}] {message}',
        'level': level,
    }
    _fire(_framework_callbacks, data)
    _, fw_cbs = _get_app_callbacks()
    _fire(fw_cbs, data)


def report_error_raw(module_type, module_name, content='', tb='', context='', appid=''):
    """直接提交自定义字段的错误, 触发回调链(SQLite+WebSocket)。仅记入报错表, 不输出控制台日志。"""
    data = {
        'timestamp': _now_str(),
        'appid': str(appid) if appid else '0000',
        'module_type': module_type,
        'module_name': module_name,
        'content': content,
        'traceback': tb,
        'context': context if isinstance(context, str) else json.dumps(context, ensure_ascii=False, default=str),
    }
    _fire(_error_callbacks, data)
    err_cbs, _ = _get_app_callbacks()
    _fire(err_cbs, data)


# ==================== 回调注册 ====================


def on_error(callback):
    """注册错误回调"""
    _error_callbacks.append(callback)


def on_framework_log(callback):
    """注册框架日志回调"""
    _framework_callbacks.append(callback)
