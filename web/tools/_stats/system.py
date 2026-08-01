"""系统信息采集"""

import asyncio
import contextlib
import gc
import logging
import os
import platform
import time
from datetime import datetime

import psutil
from aiohttp import web

log = logging.getLogger('ElainaBot.web.sysinfo')

_IS_WINDOWS = platform.system() == 'Windows'
_start_time = datetime.now()
_last_gc: float = 0.0
_GC_INTERVAL = 30
_bot_manager = None
_info_cache: tuple[float, dict | None] = (0.0, None)
_INFO_CACHE_TTL = 5


def set_context(bot_manager, start_time=None):
    global _bot_manager, _start_time
    _bot_manager = bot_manager
    if start_time:
        _start_time = start_time


_cpu_model_cache = None


def _cpu_model():
    global _cpu_model_cache
    if _cpu_model_cache:
        return _cpu_model_cache
    model = ''
    try:
        if _IS_WINDOWS:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r'HARDWARE\DESCRIPTION\System\CentralProcessor\0',
            )
            model = winreg.QueryValueEx(key, 'ProcessorNameString')[0].strip()
            winreg.CloseKey(key)
        else:
            with open('/proc/cpuinfo') as f:
                for line in f:
                    if line.startswith('model name'):
                        model = line.split(':', 1)[1].strip()
                        break
    except OSError:
        pass
    if not model:
        with contextlib.suppress(Exception):
            model = platform.processor() or ''
    if not model:
        cores = psutil.cpu_count(logical=True)
        model = f'{cores} 核处理器'
    _cpu_model_cache = model
    return model


def get_system_info() -> dict:
    global _last_gc
    proc = psutil.Process(os.getpid())
    now = time.time()
    if now - _last_gc >= _GC_INTERVAL:
        gc.collect(0)
        _last_gc = now

    mem = proc.memory_info()
    sys_mem = psutil.virtual_memory()
    rss_mb = mem.rss / (1024**2)
    mem_total_mb = sys_mem.total / (1024**2)

    try:
        cpu_cores = psutil.cpu_count(logical=True)
        cpu_pct = max(proc.cpu_percent(interval=0.05), 1.0)
        sys_cpu = max(psutil.cpu_percent(interval=0.05), 5.0)
    except Exception:
        cpu_cores, cpu_pct, sys_cpu = 1, 1.0, 5.0

    uptime = int((datetime.now() - _start_time).total_seconds())
    try:
        boot = datetime.fromtimestamp(psutil.boot_time())
        sys_uptime = int((datetime.now() - boot).total_seconds())
    except Exception:
        sys_uptime = uptime

    disk = psutil.disk_usage(os.path.abspath(os.getcwd()))

    plugins_count = bots_count = today_active = today_messages = 0
    active_groups = total_users = total_groups = 0
    if _bot_manager:
        bots_count = len(_bot_manager._bots)
        pm = getattr(_bot_manager, '_plugin_manager', None)
        if pm:
            plugins_count = getattr(pm, 'handler_count', 0)
        for inst in _bot_manager._bots.values():
            try:
                rows = inst.log_service.query(
                    'message',
                    "SELECT COUNT(*) as cnt, COUNT(DISTINCT user_id) as users, COUNT(DISTINCT group_id) as groups FROM log WHERE user_id != ''",
                )
                if rows:
                    today_messages += rows[0].get('cnt', 0)
                    today_active += rows[0].get('users', 0)
                    active_groups += rows[0].get('groups', 0)
            except Exception as e:
                log.debug(f'统计今日消息失败: {e}')
            try:
                r = inst.log_service.query_data('SELECT COUNT(*) as c FROM users')
                if r:
                    total_users += r[0].get('c', 0)
            except Exception as e:
                log.debug(f'统计用户数失败: {e}')
            try:
                r = inst.log_service.query_data('SELECT COUNT(*) as c FROM groups_users')
                if r:
                    total_groups += r[0].get('c', 0)
            except Exception as e:
                log.debug(f'统计群数失败: {e}')

    return {
        'cpu_percent': round(sys_cpu, 1),
        'framework_cpu_percent': round(cpu_pct, 1),
        'cpu_cores': cpu_cores,
        'cpu_model': _cpu_model(),
        'memory_percent': round(sys_mem.percent, 1),
        'memory_used': round(sys_mem.used / (1024**2), 1),
        'memory_total': round(mem_total_mb, 1),
        'framework_memory_percent': round((rss_mb / mem_total_mb) * 100 if mem_total_mb else 0, 1),
        'framework_memory_total': round(rss_mb, 1),
        'disk_info': {
            'total': disk.total,
            'used': disk.used,
            'free': disk.free,
            'percent': disk.percent,
        },
        'uptime': uptime,
        'system_uptime': sys_uptime,
        'start_time': _start_time.strftime('%Y-%m-%d %H:%M:%S'),
        'system_version': platform.platform(),
        'plugins_count': plugins_count,
        'bots_count': bots_count,
        'today_active': today_active,
        'today_messages': today_messages,
        'active_groups': active_groups,
        'total_users': total_users,
        'total_groups': total_groups,
    }


async def handle_system_info(request: web.Request):
    global _info_cache
    try:
        now = time.time()
        ts, data = _info_cache
        if data and now - ts < _INFO_CACHE_TTL:
            return web.json_response(data)
        data = await asyncio.get_running_loop().run_in_executor(None, get_system_info)
        _info_cache = (now, data)
        return web.json_response(data)
    except Exception as e:
        log.error(f'获取系统信息失败: {e}')
        return web.json_response({'error': str(e)}, status=500)
