"""统计模块共享上下文 (bot_manager / base_dir)"""

bot_manager = None
base_dir = ''


def set_context(manager, directory=''):
    global bot_manager, base_dir
    bot_manager = manager
    if directory:
        base_dir = directory
