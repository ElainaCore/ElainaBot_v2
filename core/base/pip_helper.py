"""pip 依赖安装辅助 — 插件/模块共用"""

import os
import re
import sys
import subprocess
import asyncio
import importlib.metadata as _metadata

from core.base.config import cfg
from core.base.logger import get_logger, FRAMEWORK

log = get_logger(FRAMEWORK, "依赖安装")


def _pip_install_sync(cmd, name):
    """同步执行 pip install (线程池中运行)"""
    log.info(f"[{name}] 正在安装依赖...")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode == 0:
        log.info(f"[{name}] 依赖安装完成")
    else:
        stderr = result.stderr.strip()
        if stderr:
            log.warning(f"[{name}] pip: {stderr[:200]}")
    return result.returncode


def all_requirements_met(req_path):
    """检查 requirements.txt 中所有包是否已安装"""
    try:
        with open(req_path, 'r', encoding='utf-8') as f:
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


async def install_requirements(name, target_dir, *, skip_if_met=False, no_cache=False):
    """检查并安装 requirements.txt 依赖"""
    req_path = os.path.join(target_dir, 'requirements.txt')
    if not os.path.isfile(req_path):
        return
    if not cfg.get('settings', 'pip.auto_install', True):
        return
    if skip_if_met and all_requirements_met(req_path):
        return

    mirror = cfg.get('settings', 'pip.mirror', '')
    cmd = [sys.executable, '-m', 'pip', 'install', '-r', req_path, '--quiet']
    if no_cache:
        cmd.append('--no-cache-dir')
    if mirror:
        cmd.extend(['-i', mirror])

    try:
        exit_code = await asyncio.get_running_loop().run_in_executor(
            None, _pip_install_sync, cmd, name)
        if exit_code != 0:
            log.warning(f"[{name}] 依赖安装可能失败 (exit={exit_code})")
    except Exception as e:
        log.warning(f"[{name}] 依赖安装异常: {e}")
