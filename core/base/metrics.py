#!/usr/bin/env python
"""进程内轻量指标 — 计数器与仪表盘。

只做"可观测"这一件事: 无第三方依赖, 线程安全 (executor 线程也会写),
供 web 面板与健康巡检读取。不引入指标后端, 不做聚合窗口。
"""

import threading

_lock = threading.Lock()
_counters: dict[str, float] = {}
_gauges: dict[str, float] = {}


def counter(name, delta=1):
    """累加计数器。"""
    with _lock:
        _counters[name] = _counters.get(name, 0) + delta


def gauge(name, value):
    """记录瞬时值 (覆盖)。"""
    with _lock:
        _gauges[name] = value


def snapshot():
    """返回当前指标快照。"""
    with _lock:
        return {'counters': dict(_counters), 'gauges': dict(_gauges)}


def reset():
    """清空指标 (测试用)。"""
    with _lock:
        _counters.clear()
        _gauges.clear()
