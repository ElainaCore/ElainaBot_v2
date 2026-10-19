"""连接与会话事实；不承诺模块处理完成或跨进程连续性。"""

import asyncio
import inspect
import math
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from uuid import uuid4

from core.base.logger import FRAMEWORK, get_logger
from core.base.tasks import spawn

log = get_logger(FRAMEWORK, '连接状态')
CONNECTION_STATUS_API_VERSION = 1


@dataclass(frozen=True, slots=True)
class ConnectionCapabilities:
    """能力只说明可观测事实，不保证平台保存全部历史。"""

    transport_disconnect: bool
    session_resume: bool
    replay_boundary: bool
    reason: str


@dataclass(frozen=True, slots=True)
class ConnectionContext:
    """接收身份与顺序；sequence 是接收进度，不能作为业务完成凭证。"""

    appid: str
    transport: str
    runtime_id: str
    bot_generation: str
    connection_generation: str | None = None
    session_generation: str | None = None
    session_id: str | None = None
    resume_attempt_id: str | None = None
    receive_index: int = 0
    sequence: int | None = None


@dataclass(frozen=True, slots=True)
class ConnectionStatus:
    context: ConnectionContext
    revision: int
    phase: str
    continuity: str
    login_kind: str | None
    reason: str
    capabilities: ConnectionCapabilities
    resume_from_sequence: int | None = None
    replay_end_sequence: int | None = None
    replay_end_receive_index: int | None = None
    is_snapshot: bool = False

    @property
    def appid(self):
        return self.context.appid

    @property
    def transport(self):
        return self.context.transport


@dataclass(frozen=True, slots=True)
class ConnectionStatusResult:
    status: ConnectionStatus
    subscription_id: str
    owner: str
    outcome: str
    error_type: str | None


class ConnectionStatusSubscription:
    """每个订阅者及 Bot/传输独立排队，最近 100 项结果可查询。"""

    def __init__(self, manager, callback, owner, appid, transport, timeout):
        self.subscription_id = uuid4().hex
        self.owner = owner
        self.appid = appid
        self.transport = transport
        self.timeout = timeout
        self._manager = manager
        self._callback = callback
        self._closed = asyncio.Event()
        self._close_outcome = None
        self._history: deque[ConnectionStatusResult] = deque(maxlen=100)
        self._pending = {}
        self._drainers = {}
        self._running = {}

    @property
    def active(self):
        return not self._closed.is_set()

    @property
    def results(self) -> tuple[ConnectionStatusResult, ...]:
        return tuple(self._history)

    def cancel(self):
        self._close('unsubscribed')

    def _close(self, outcome):
        if self.active:
            self._close_outcome = outcome
            self._closed.set()
            self._manager._subscriptions.pop(self.subscription_id, None)
            for task in self._running.values():
                task.cancel()

    def _enqueue(self, status):
        if (self.appid is not None and self.appid != status.appid) or (self.transport is not None and self.transport != status.transport):
            return
        key = (status.appid, status.transport)
        pending = self._pending.setdefault(key, deque())
        pending.append(status)
        if key not in self._drainers:
            self._drainers[key] = spawn(self._drain(key, pending))

    async def _drain(self, key, pending):
        try:
            # 先登记排空任务，避免 eager 工厂同步结束后回写已完成的任务。
            await asyncio.sleep(0)
            while pending:
                status = pending.popleft()
                outcome, error = await self._invoke(key, status)
                self._history.append(ConnectionStatusResult(status, self.subscription_id, self.owner, outcome, error))
                if outcome != 'success':
                    log.warning(f'[{status.appid}] 连接通知 {self.subscription_id} ({self.owner}): {outcome}, {error}')
        finally:
            self._pending.pop(key, None)
            self._drainers.pop(key, None)

    async def _invoke(self, key, status):
        if not self.active:
            return self._close_outcome, None
        previous = self._running.get(key)
        if previous is not None and not previous.done():
            return 'busy', None
        try:
            task = asyncio.create_task(self._callback(status))
        except Exception as invocation_error:
            return 'error', type(invocation_error).__name__
        self._running[key] = task
        closed = asyncio.create_task(self._closed.wait())
        try:
            done, _ = await asyncio.wait((task, closed), timeout=self.timeout, return_when=asyncio.FIRST_COMPLETED)
            if self._closed.is_set():
                return self._close_outcome, None
            if task not in done:
                return 'timeout', None
            if task.cancelled():
                return 'cancelled', None
            error = task.exception()
            return ('error', type(error).__name__) if error else ('success', None)
        finally:
            closed.cancel()
            if not task.done():
                task.cancel()

            def release(finished):
                if not finished.cancelled():
                    finished.exception()
                if self._running.get(key) is finished:
                    self._running.pop(key, None)

            task.add_done_callback(release)


class ConnectionStatusManager:
    """当前快照同步更新；订阅回调不能阻塞传输或其他订阅者。"""

    api_version = CONNECTION_STATUS_API_VERSION

    def __init__(self):
        self.runtime_id = uuid4().hex
        self._revision = 0
        self._current: dict[tuple[str, str], ConnectionStatus] = {}
        self._sources = {}
        self._session_bots = {}
        self._subscriptions: dict[str, ConnectionStatusSubscription] = {}

    def get(self, appid, *, transport='websocket') -> ConnectionStatus | None:
        return self._current.get((str(appid), transport))

    @property
    def statuses(self) -> tuple[ConnectionStatus, ...]:
        return tuple(self._current.values())

    def capabilities(self, appid, *, transport='websocket') -> ConnectionCapabilities | None:
        status = self.get(appid, transport=transport)
        return status.capabilities if status else None

    def subscribe(
        self, callback: Callable[[ConnectionStatus], Awaitable[None]], *, owner='unknown', appid=None, transport=None, timeout=5.0, replay_current=True
    ) -> ConnectionStatusSubscription:
        if not inspect.iscoroutinefunction(callback):
            raise TypeError('connection status callbacks must be async functions')
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('timeout must be a finite positive number')
        subscription = ConnectionStatusSubscription(self, callback, owner, str(appid) if appid is not None else None, transport, timeout)
        self._subscriptions[subscription.subscription_id] = subscription
        if replay_current:
            for status in self.statuses:
                subscription._enqueue(replace(status, is_snapshot=True))
        return subscription

    def _register(self, appid, transport, *, bot_generation=None):
        key = (str(appid), transport)
        previous = self._sources.get(key)
        if previous is not None:
            previous.publish('stopped', reason='superseded', continuity='unknown')
        source = _ConnectionReporter(self, str(appid), transport, bot_generation or uuid4().hex)
        self._sources[key] = source
        source.publish('unknown', reason='created')
        return source


class _ConnectionReporter:
    """传输持有的来源凭据；被替换的来源不能覆盖新状态。"""

    def __init__(self, manager, appid, transport, bot_generation):
        self._manager = manager
        self.context = ConnectionContext(appid, transport, manager.runtime_id, bot_generation)
        self.capabilities = ConnectionCapabilities(
            transport == 'websocket', False, False, 'unverified_gateway' if transport == 'websocket' else 'http_callback'
        )
        self._status = None
        self.stopped = False

    def reactivate(self):
        """仅当前来源可重启，并保留与协议恢复参数配对的会话身份。"""
        key = (self.context.appid, self.context.transport)
        if self._manager._sources.get(key) is not self:
            return False
        self.stopped = False
        return True

    def new_session(self, session_id):
        """同一 Bot 实例替换 WSClient 后仍能区分后续新会话。"""
        appid = self.context.appid
        kind = 'new' if self._manager._session_bots.get(appid) == self.context.bot_generation else 'first'
        if not self.stopped and self._manager._sources.get((appid, self.context.transport)) is self:
            self._manager._session_bots[appid] = self.context.bot_generation
        self.update_context(session_id=session_id, session_generation=uuid4().hex, resume_attempt_id=None)
        return kind

    def update_context(self, **kwargs):
        self.context = replace(self.context, **kwargs)
        key = (self.context.appid, self.context.transport)
        if not self.stopped and self._manager._sources.get(key) is self and self._status is not None:
            self._status = replace(self._status, context=self.context)
            self._manager._current[key] = self._status

    def publish(
        self, phase, *, reason, continuity='unknown', login_kind=None, resume_from_sequence=None, replay_end_sequence=None, replay_end_receive_index=None
    ):
        key = (self.context.appid, self.context.transport)
        if self.stopped or self._manager._sources.get(key) is not self:
            return
        candidate = ConnectionStatus(
            self.context, 0, phase, continuity, login_kind, reason, self.capabilities, resume_from_sequence, replay_end_sequence, replay_end_receive_index
        )
        if self._status is not None and replace(self._status, revision=0) == candidate:
            return
        self._manager._revision += 1
        self._status = replace(candidate, revision=self._manager._revision)
        self._manager._current[key] = self._status
        if phase == 'stopped':
            self.stopped = True
        for subscription in tuple(self._manager._subscriptions.values()):
            subscription._enqueue(self._status)
