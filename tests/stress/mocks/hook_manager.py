"""HookManager mocks for stress testing.

The real HookManager is fully usable in tests — this module provides
convenience helpers for batch-registering hooks with configurable work.
"""

import asyncio


async def _noop_hook(data):
    """Hook that does nothing."""
    return data


async def _sleep_hook(data, duration=0.01):
    """Hook that simulates work by sleeping."""
    await asyncio.sleep(duration)
    return data


async def _block_hook(data):
    """Hook that blocks all sends."""
    return None


async def _error_hook(data):
    """Hook that raises an exception (tests exception resilience)."""
    raise RuntimeError("simulated hook error")


def register_noop_hooks(hook_manager, hook_name, count):
    """Register N noop hooks."""
    for i in range(count):
        hook_manager.register(hook_name, _noop_hook, owner=f"test_hook_{i}", priority=100 + i)


def register_sleep_hooks(hook_manager, hook_name, count, duration=0.01):
    """Register N sleep hooks."""
    import functools
    for i in range(count):
        fn = functools.partial(_sleep_hook, duration=duration)
        hook_manager.register(hook_name, fn, owner=f"test_sleep_hook_{i}", priority=100 + i)


def register_block_hook(hook_manager, hook_name):
    """Register a single blocking hook."""
    hook_manager.register(hook_name, _block_hook, owner="test_block", priority=1)


def register_error_hook(hook_manager, hook_name):
    """Register a single error-raising hook."""
    hook_manager.register(hook_name, _error_hook, owner="test_error", priority=1)
