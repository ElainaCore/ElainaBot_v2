"""重启流程共享工具。"""

import contextlib

import psutil

RESTART_RECOVERY_ENV = 'ELAINABOT_RESTART_RECOVERY'


def stop_child_processes(timeout: float = 3) -> list[int]:
    """停止当前进程创建的全部后代进程。"""
    try:
        children = psutil.Process().children(recursive=True)
    except psutil.Error:
        return []

    for process in reversed(children):
        with contextlib.suppress(psutil.Error):
            process.terminate()
    _, alive = psutil.wait_procs(children, timeout=timeout)
    for process in alive:
        with contextlib.suppress(psutil.Error):
            process.kill()
    if alive:
        psutil.wait_procs(alive, timeout=1)
    return [process.pid for process in children]
