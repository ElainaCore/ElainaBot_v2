"""fork 子进程安全工具"""

import contextlib
import os
import socket
import stat


def close_inherited_listen_sockets():
    """关闭 fork 继承的监听 socket (供进程池 initializer 使用)

    fork 出的子进程会复制父进程全部 fd, 包括 HTTP 服务器的监听 socket;
    若子进程存活期间主进程重启 (execv), 端口仍被子进程占住导致新进程绑定失败。
    """
    fd_dir = '/proc/self/fd'
    if not os.path.isdir(fd_dir):
        return
    for name in os.listdir(fd_dir):
        with contextlib.suppress(OSError, ValueError):
            fd = int(name)
            if not stat.S_ISSOCK(os.fstat(fd).st_mode):
                continue
            dup = socket.socket(fileno=os.dup(fd))
            try:
                listening = dup.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN)
            finally:
                dup.close()
            if listening:
                os.close(fd)
