"""分发 block 语义测试 — 多个插件注册相同指令时的放行/拦截行为"""

import asyncio
import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.base.config import cfg  # noqa: E402
from core.plugin.decorators import _pending_handlers, handler  # noqa: E402
from core.plugin.manager import PluginManager  # noqa: E402
from tests.stress.mocks.event_factory import EventFactory  # noqa: E402
from tests.stress.mocks.message_sender import MockMessageSender  # noqa: E402

APPID = '102000001'


@pytest.fixture(autouse=True)
def _init_cfg():
    """dispatch() 依赖全局 cfg.get_bot_setting()"""
    if not cfg._ready:
        cfg.init(os.path.join(ROOT, 'config'))


def _build_two_handlers(sink, block_first):
    """注册两个 pattern 相同的 handler (高优先级 First, 低优先级 Second)"""
    _pending_handlers.clear()

    @handler(r'test', name='First', priority=10, block=block_first)
    async def _first(event, match):
        sink.append('first')

    @handler(r'test', name='Second', priority=0)
    async def _second(event, match):
        sink.append('second')

    collected = list(_pending_handlers)
    _pending_handlers.clear()
    return collected


def _make_manager(handlers):
    tmp = tempfile.mkdtemp(prefix='block_test_')
    pm = PluginManager(plugins_dir=tmp, bot_appid=APPID)
    for h in handlers:
        h['_plugin'] = 'test_plugin'
    pm._all_handlers = sorted(handlers, key=lambda h: -h['priority'])
    pm._apply_bot_bindings()
    pm._build_dispatch_index()
    return pm


async def _dispatch_and_collect(pm, content='test'):
    sender = MockMessageSender(APPID)
    event = EventFactory.group_at_message(content, appid=APPID)
    await pm.dispatch(event, sender)
    # 等待 fire-and-forget 的 handler 链执行完毕
    for _ in range(50):
        await asyncio.sleep(0.01)
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if not pending:
            break


@pytest.mark.integration
@pytest.mark.asyncio
async def test_default_block_false_runs_all_matching_plugins():
    """默认 block=False: 相同指令的多个插件都会响应 (按 priority 顺序)"""
    sink: list[str] = []
    pm = _make_manager(_build_two_handlers(sink, block_first=False))
    await _dispatch_and_collect(pm)
    assert sink == ['first', 'second']


@pytest.mark.integration
@pytest.mark.asyncio
async def test_block_true_intercepts_subsequent_plugins():
    """高优先级 handler block=True: 命中后拦截, 只有它响应"""
    sink: list[str] = []
    pm = _make_manager(_build_two_handlers(sink, block_first=True))
    await _dispatch_and_collect(pm)
    assert sink == ['first']
