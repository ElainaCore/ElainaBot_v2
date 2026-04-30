#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ElainaBot 入口 - 仅负责启动, 不含任何业务逻辑"""

import sys
import os
import asyncio

# 项目根目录加入 sys.path
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# 禁止生成 .pyc
sys.dont_write_bytecode = True


def main():
    from core.bot import BotManager
    manager = BotManager()

    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    try:
        asyncio.run(manager.start())
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
