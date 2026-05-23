"""机器人重启"""

import os
import platform
import subprocess
import sys
import threading
import time

from aiohttp import web

_IS_WINDOWS = platform.system().lower() == 'windows'
_base_dir = ''


def set_context(base_dir: str):
    global _base_dir
    _base_dir = base_dir


_WIN_TEMPLATE = """import os, sys, time, subprocess
def main():
    time.sleep(3)
    main_path = r"{main_py}"
    os.chdir(os.path.dirname(main_path))
    subprocess.Popen([sys.executable, main_path], creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),  # 旧版Python没有这个字段，进行兼容
                     cwd=os.path.dirname(main_path))
    time.sleep(1)
    try: os.remove(__file__)
    except: pass
    sys.exit(0)
if __name__ == "__main__":
    main()
"""

_UNIX_TEMPLATE = """import os, sys, time, psutil
def main():
    main_path = r"{main_py}"
    port = {port}
    try:
        for conn in psutil.net_connections():
            if conn.laddr.port == port and conn.status == 'LISTEN':
                try:
                    proc = psutil.Process(conn.pid)
                    proc.terminate()
                    proc.wait(timeout=3)
                except: pass
    except: pass
    time.sleep(1)
    os.chdir(os.path.dirname(main_path))
    try: os.remove(__file__)
    except: pass
    os.execv(sys.executable, [sys.executable, main_path])
if __name__ == "__main__":
    main()
"""


async def handle_restart(request: web.Request):
    # 优雅重启: 触发 Application._stop_event, 让 shutdown() 刷写 SQLite 缓冲后由 main.py 自行 os.execv
    try:
        from core.application import get_app
        app = get_app()
        if app:
            app._restart_requested = True
            if app._stop_event:
                app._stop_event.set()
            return web.json_response({'success': True, 'message': '正在重启...'})
    except Exception:
        pass

    # 兜底: Application 不可用 (旧路径), 用外部脚本重启
    main_py = os.path.join(_base_dir, 'main.py')
    if not os.path.exists(main_py):
        return web.json_response({'success': False, 'error': 'main.py 不存在'})

    from core.base.config import cfg

    port = cfg.get('settings', 'server.port', 5001)
    data_dir = os.path.join(_base_dir, 'data')
    os.makedirs(data_dir, exist_ok=True)
    restarter = os.path.join(data_dir, 'bot_restarter.py')

    try:
        script = _WIN_TEMPLATE.format(main_py=main_py) if _IS_WINDOWS else _UNIX_TEMPLATE.format(main_py=main_py, port=port)

        with open(restarter, 'w', encoding='utf-8') as f:
            f.write(script)

        if _IS_WINDOWS:
            subprocess.Popen(
                [sys.executable, restarter],
                cwd=_base_dir,
                creationflags=getattr(subprocess, 'CREATE_NEW_CONSOLE', 0), # 旧版Python没有这个字段，进行兼容
            )

            def _delayed_exit():
                time.sleep(1)
                os._exit(0)

            threading.Thread(target=_delayed_exit, daemon=True).start()
        else:
            subprocess.Popen([sys.executable, restarter], cwd=_base_dir, start_new_session=True)
        return web.json_response({'success': True, 'message': '正在重启...'})
    except Exception as e:
        return web.json_response({'success': False, 'error': str(e)})
