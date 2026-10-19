"""模块群生命周期通知：保留 QQ 原生身份的第 1 版接口。"""

import asyncio
import inspect
import math
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import uuid4

from core.base.tasks import spawn
from core.message.event import GROUP_JOIN_REQUEST, GROUP_MSG_RECEIVE, GROUP_MSG_REJECT, GROUP_TYPES, INTERACTION_CREATE, SUBSCRIBE_MESSAGE_STATUS
from core.module.connection_status import ConnectionContext

GROUP_LIFECYCLE_API_VERSION = 1
GROUP_LIFECYCLE_TYPES = frozenset({'GROUP_MEMBER_ADD', 'GROUP_MEMBER_REMOVE', 'GROUP_ADD_ROBOT', 'GROUP_DEL_ROBOT'})
_GROUP_EVENT_TYPES = GROUP_LIFECYCLE_TYPES | GROUP_TYPES | {GROUP_JOIN_REQUEST, GROUP_MSG_RECEIVE, GROUP_MSG_REJECT}


def _native_id(value):
    return value if isinstance(value, str) and value else None


def _consume_task(task):
    if not task.cancelled():
        task.exception()


@dataclass(frozen=True, slots=True)
class GroupLifecycleEvent:
    """仅保留原生身份；缺失为 None，不从显示 ID 推断。"""

    delivery_id: str
    appid: str
    event_type: str
    group_openid: str | None
    member_openid: str | None
    op_member_openid: str | None
    event_id: str | None
    timestamp: str | int | float | None
    sequence: int | None
    transport: str
    session_id: str | None
    received_at: float
    connection_context: ConnectionContext | None = None


@dataclass(frozen=True, slots=True)
class GroupLifecycleResult:
    event: GroupLifecycleEvent
    subscription_id: str
    owner: str
    status: str
    error_type: str | None
    started_at: float
    finished_at: float


class GroupLifecycleDelivery:
    """进程内投递回执；取消等待者不会取消实际处理。"""

    def __init__(self, event, completion):
        self.event = event
        self._completion = completion

    @property
    def results(self) -> tuple[GroupLifecycleResult, ...] | None:
        return self._completion.result() if self._completion.done() else None

    async def wait(self) -> tuple[GroupLifecycleResult, ...]:
        return await asyncio.shield(self._completion)


class GroupLifecycleSubscription:
    """订阅由单个模块上下文拥有，历史保留最近 100 个结果。"""

    def __init__(self, manager, callback, owner, appid, timeout):
        self.subscription_id = uuid4().hex
        self.owner = owner
        self.appid = appid
        self.timeout = timeout
        self._manager = manager
        self._callback = callback
        self._closed = asyncio.Event()
        self._close_status = None
        self._history: deque[GroupLifecycleResult] = deque(maxlen=100)
        self._running: dict[tuple[str, str | None], asyncio.Task] = {}
        self._abandoned: set[asyncio.Task] = set()

    @property
    def active(self):
        return not self._closed.is_set()

    @property
    def results(self) -> tuple[GroupLifecycleResult, ...]:
        return tuple(self._history)

    def cancel(self):
        """幂等取消订阅；在途调用返回 unsubscribed 结果。"""
        self._close('unsubscribed')

    def _close(self, status):
        if self.active:
            self._close_status = status
            self._closed.set()
            self._manager._subscriptions.pop(self.subscription_id, None)
            for task in self._running.values():
                task.cancel()

    async def _invoke(self, event):
        started = time.monotonic()
        status, error = self._close_status, None
        key = (event.appid, event.group_openid)
        if self.active:
            previous = self._running.get(key)
            bot_busy = bool(self._abandoned) and any(
                task in self._abandoned and not task.done() for (bot, _), task in self._running.items() if bot == event.appid
            )
            if bot_busy or (previous is not None and not previous.done()):
                status = 'busy'
            else:
                task = asyncio.create_task(self._callback(event))
                self._running[key] = task
                closed = asyncio.create_task(self._closed.wait())
                try:
                    done, _ = await asyncio.wait((task, closed), timeout=self.timeout, return_when=asyncio.FIRST_COMPLETED)
                    if self._closed.is_set():
                        status = self._close_status
                    elif task not in done:
                        status = 'timeout'
                    elif task.cancelled():
                        status = 'cancelled'
                    else:
                        exc = task.exception()
                        status = 'error' if exc else 'success'
                        error = type(exc).__name__ if exc else None
                finally:
                    closed.cancel()
                    if not task.done():
                        self._abandoned.add(task)
                        task.cancel()
                    task.add_done_callback(_consume_task)

                    # 保留尚未响应取消的调用，直到它真正退出。
                    def release(finished):
                        self._abandoned.discard(finished)
                        if self._running.get(key) is finished:
                            self._running.pop(key, None)

                    task.add_done_callback(release)
        result = GroupLifecycleResult(event, self.subscription_id, self.owner, status, error, started, time.monotonic())
        self._history.append(result)
        return result


@dataclass(slots=True)
class _PendingDelivery:
    """队列保留逐事件数据和完成凭据，不为等待中的事件创建任务。"""

    event: GroupLifecycleEvent
    predecessors: tuple[asyncio.Future, ...]
    subscribers: tuple[GroupLifecycleSubscription, ...]
    completion: asyncio.Future


class GroupLifecycleManager:
    """按 Bot／群登记接收顺序，并等待通知处理屏障。"""

    api_version = GROUP_LIFECYCLE_API_VERSION

    def __init__(self):
        self._subscriptions: dict[str, GroupLifecycleSubscription] = {}
        self._tails: dict[str, dict[str | None, asyncio.Future]] = {}
        self._pending: dict[tuple[str, str | None], deque[_PendingDelivery]] = {}
        self._drainers: dict[tuple[str, str | None], asyncio.Task] = {}

    def subscribe(self, callback: Callable[[GroupLifecycleEvent], Awaitable[None]], *, owner='unknown', appid=None, timeout=5.0):
        if not inspect.iscoroutinefunction(callback):
            raise TypeError('group lifecycle callbacks must be async functions')
        if not isinstance(timeout, (float, int)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('timeout must be a finite positive number')
        subscription = GroupLifecycleSubscription(self, callback, owner, str(appid) if appid is not None else None, timeout)
        self._subscriptions[subscription.subscription_id] = subscription
        return subscription

    def prepare(self, event, *, transport='internal', session_id=None):
        """在传输层启动并发处理前，同步登记接收顺序。"""
        if event._group_lifecycle_prepared:
            return
        event._group_lifecycle_prepared = True
        appid = str(event.appid)
        raw = event.raw or {}
        data = raw.get('d')
        data = data if isinstance(data, dict) else {}
        native_group = _native_id(data.get('group_openid'))
        group_event = event.event_type in _GROUP_EVENT_TYPES
        if event.event_type == INTERACTION_CREATE:
            group_event = event.is_group or data.get('chat_type') == 1 or data.get('scene') == 'group'
        elif event.event_type == SUBSCRIBE_MESSAGE_STATUS:
            group_event = bool(native_group or _native_id(event.group_id))
        if not group_event:
            return
        # 兼容 group_id 的适配器可参与本地排序，但不补造原生身份。
        group = native_group if event.event_type in GROUP_LIFECYCLE_TYPES else native_group or _native_id(event.group_id)
        tails = self._tails.get(appid, {})
        predecessors = tuple(tails.values()) if group is None else tuple(task for task in (tails.get(None), tails.get(group)) if task is not None)
        event._group_lifecycle_scope = (appid, group)
        event._group_lifecycle_barriers = predecessors
        if event.event_type not in GROUP_LIFECYCLE_TYPES:
            return
        timestamp = data.get('timestamp')
        if isinstance(timestamp, bool) or not isinstance(timestamp, (str, int, float)) or timestamp == '':
            timestamp = None
        sequence = raw.get('s')
        notification = GroupLifecycleEvent(
            uuid4().hex,
            appid,
            event.event_type,
            native_group,
            _native_id(data.get('member_openid')),
            _native_id(data.get('op_member_openid')),
            _native_id(raw.get('id')) or _native_id(raw.get('event_id')),
            timestamp,
            sequence if type(sequence) is int else None,
            transport,
            _native_id(session_id),
            time.monotonic(),
            event.connection_context,
        )
        subscribers = tuple(s for s in self._subscriptions.values() if s.appid is None or s.appid == appid)
        completion = asyncio.get_running_loop().create_future()
        event.group_lifecycle_delivery = GroupLifecycleDelivery(notification, completion)
        # 排空任务已等待前序事件，入口只需等待本次完成凭据。
        event._group_lifecycle_barriers = (completion,)
        tails = self._tails.setdefault(appid, {})
        tails[group] = completion
        key = (appid, group)
        pending = self._pending.setdefault(key, deque())
        pending.append(_PendingDelivery(notification, predecessors, subscribers, completion))
        if key not in self._drainers:
            self._drainers[key] = spawn(self._drain(key, pending))

        def release(finished):
            if tails.get(group) is finished:
                tails.pop(group, None)
            if not tails and self._tails.get(appid) is tails:
                self._tails.pop(appid, None)

        completion.add_done_callback(release)

    async def _drain(self, key, pending):
        """每个 Bot／群只运行一个排空任务，队列保持接收顺序。"""
        try:
            # 无订阅者时也可能同步排空，先让创建方登记任务。
            await asyncio.sleep(0)
            while pending:
                item = pending[0]
                for previous in item.predecessors:
                    await asyncio.shield(previous)
                results = tuple(await asyncio.gather(*(s._invoke(item.event) for s in item.subscribers)))
                item.completion.set_result(results)
                pending.popleft()
        finally:
            # 事件循环关闭时释放未完成凭据，不把中断误报为成功。
            for item in pending:
                item.completion.cancel()
            self._pending.pop(key, None)
            self._drainers.pop(key, None)

    async def wait(self, event):
        """在记录、欢迎回复及插件分发前，等待已知群变化。"""
        for previous in event._group_lifecycle_barriers:
            await asyncio.shield(previous)
