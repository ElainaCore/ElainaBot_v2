"""Version 1 module notifications for native QQ group lifecycle deliveries."""

import asyncio
import inspect
import math
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import uuid4

from core.base.tasks import spawn

GROUP_LIFECYCLE_API_VERSION = 1
GROUP_LIFECYCLE_TYPES = frozenset({'GROUP_MEMBER_ADD', 'GROUP_MEMBER_REMOVE', 'GROUP_ADD_ROBOT', 'GROUP_DEL_ROBOT'})


def _native_id(value):
    return value if isinstance(value, str) and value else None


def _consume_task(task):
    if not task.cancelled():
        task.exception()


@dataclass(frozen=True, slots=True)
class GroupLifecycleEvent:
    """Native identities only; None means unavailable, never inferred from display IDs."""

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
    """A process-local receipt. Waiting is shielded from waiter cancellation."""

    def __init__(self, event, task):
        self.event = event
        self._task = task

    @property
    def results(self) -> tuple[GroupLifecycleResult, ...] | None:
        return self._task.result() if self._task.done() else None

    async def wait(self) -> tuple[GroupLifecycleResult, ...]:
        return await asyncio.shield(self._task)


class GroupLifecycleSubscription:
    """Owned by one module context; history retains the latest 100 results."""

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
        """Idempotently unsubscribe. In-flight calls report unsubscribed."""
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
            bot_busy = any(task in self._abandoned and not task.done() for (bot, _), task in self._running.items() if bot == event.appid)
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

                    # Keep cancellation-resistant calls visible until they really stop.
                    def release(finished):
                        self._abandoned.discard(finished)
                        if self._running.get(key) is finished:
                            self._running.pop(key, None)

                    task.add_done_callback(release)
        result = GroupLifecycleResult(event, self.subscription_id, self.owner, status, error, started, time.monotonic())
        self._history.append(result)
        return result


class GroupLifecycleManager:
    """Ingress reservation and notification barriers, scoped to Bot/group."""

    api_version = GROUP_LIFECYCLE_API_VERSION

    def __init__(self):
        self._subscriptions: dict[str, GroupLifecycleSubscription] = {}
        self._tails: dict[str, dict[str | None, asyncio.Task]] = {}

    def subscribe(self, callback: Callable[[GroupLifecycleEvent], Awaitable[None]], *, owner='unknown', appid=None, timeout=5.0):
        if not inspect.iscoroutinefunction(callback):
            raise TypeError('group lifecycle callbacks must be async functions')
        if not isinstance(timeout, (float, int)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('timeout must be a finite positive number')
        subscription = GroupLifecycleSubscription(self, callback, owner, str(appid) if appid is not None else None, timeout)
        self._subscriptions[subscription.subscription_id] = subscription
        return subscription

    def prepare(self, event, *, transport='internal', session_id=None):
        """Synchronously reserve before transport schedules concurrent handlers."""
        if event._group_lifecycle_prepared:
            return
        event._group_lifecycle_prepared = True
        appid = str(event.appid)
        raw = event.raw or {}
        data = raw.get('d')
        data = data if isinstance(data, dict) else {}
        native_group = _native_id(data.get('group_openid'))
        # Compatible group_id adapters may order locally, but never fabricate native identity.
        group = native_group if event.event_type in GROUP_LIFECYCLE_TYPES else native_group or _native_id(event.group_id)
        tails = self._tails.get(appid, {})
        predecessors = tuple(tails.values()) if group is None else tuple(task for task in (tails.get(None), tails.get(group)) if task is not None)
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
        )
        subscribers = tuple(s for s in self._subscriptions.values() if s.appid is None or s.appid == appid)
        task = spawn(self._deliver(notification, predecessors, subscribers))
        event.group_lifecycle_delivery = GroupLifecycleDelivery(notification, task)
        event._group_lifecycle_barriers = (*predecessors, task)
        tails = self._tails.setdefault(appid, {})
        tails[group] = task

        def release(finished):
            if tails.get(group) is finished:
                tails.pop(group, None)
            if not tails and self._tails.get(appid) is tails:
                self._tails.pop(appid, None)

        task.add_done_callback(release)

    async def _deliver(self, event, predecessors, subscribers):
        for previous in predecessors:
            await asyncio.shield(previous)
        return tuple(await asyncio.gather(*(s._invoke(event) for s in subscribers)))

    async def wait(self, event):
        """Wait for known changes before storage, welcome replies or plugin dispatch."""
        for previous in event._group_lifecycle_barriers:
            await asyncio.shield(previous)
        if event.group_lifecycle_delivery is not None:
            await event.group_lifecycle_delivery.wait()
