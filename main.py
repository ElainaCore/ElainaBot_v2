#!/usr/bin/env python
"""ElainaBot 入口"""

import asyncio
import contextlib
import json
import os
import pathlib
import shutil
import ssl
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
sys.dont_write_bytecode = True

# 全局跳过 SSL 证书验证
_orig_ctx = ssl.create_default_context


def _no_verify(*a, **kw):
    c = _orig_ctx(*a, **kw)
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE
    return c


ssl.create_default_context = _no_verify

# 加载 .env
_env = os.path.join(_ROOT, '.env')
if os.path.isfile(_env):
    with contextlib.suppress(ImportError):
        from dotenv import load_dotenv

        load_dotenv(_env, override=False)


def _is_debugger() -> bool:
    """检测是否从调试器启动 (VSCode / PyCharm 等)"""
    if sys.gettrace() is not None:
        return True
    # VSCode debugpy 环境变量
    if os.environ.get('DEBUGPY_SESSION') or os.environ.get('DEBUGPY_PROCESS_SPAWN_TIMEOUT'):
        return True
    # PyCharm pydevd 环境变量
    is_debugger = os.environ.get('PYDEVD_USE_FRAME_EVAL') or os.environ.get('PYCHARM_HOSTED')
    return bool(is_debugger)


def _resolve_env() -> str:
    """解析运行环境: dev / prod"""
    env = os.environ.get('ELAINABOT_ENV', '').strip().lower()
    if env in ('dev', 'development', 'test'):
        return 'dev'
    if env in ('prod', 'production'):
        return 'prod'
    # 未显式设置时, 从调试器启动默认 dev, 否则 prod
    return 'dev' if _is_debugger() else 'prod'


def main():
    _env = _resolve_env()

    # 清理废弃文件/目录 (仅生产环境执行)
    while _env == 'prod':
        with contextlib.suppress(Exception):
            dep_path = pathlib.Path(_ROOT, 'web', 'deprecated_files.json')
            if dep_path.is_file() is False:
                break
            for r in json.loads(dep_path.read_text('utf-8')):
                if not r:
                    continue
                t = pathlib.Path(_ROOT, r)
                if r.endswith('/'):
                    shutil.rmtree(t, ignore_errors=True)
                    continue
                t.unlink(missing_ok=True)
                shutil.rmtree(t.parent / '__pycache__', ignore_errors=True)
            break

    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    from core.application import Application, relaunch

    restart = False
    with contextlib.suppress(KeyboardInterrupt):
        restart = asyncio.run(Application().start())
    if restart:
        relaunch()


if __name__ == '__main__':
    main()
